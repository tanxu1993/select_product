"""上品帮插件与登录态管理。"""

from __future__ import annotations

from dataclasses import dataclass
import io
import re
import struct
import shutil
import time
import zipfile
from pathlib import Path

import requests
from playwright.sync_api import Browser, BrowserContext, BrowserType, Playwright

from config.settings import Settings, get_settings


class ShopbangExtensionError(RuntimeError):
    """上品帮插件相关异常。"""


@dataclass(slots=True)
class ShopbangBrowserSession:
    """封装上品帮浏览器会话，兼容本地启动和 CDP 连接模式。"""

    context: BrowserContext
    browser: Browser | None = None
    owns_context: bool = True
    cleanup_path: Path | None = None

    def close(self) -> None:
        """关闭当前会话。"""

        try:
            if self.owns_context:
                self.context.close()
                return

            # CDP 模式下不要关闭用户本机 Chrome，只让进程自然释放连接。
            return
        finally:
            if self.cleanup_path and self.cleanup_path.exists():
                shutil.rmtree(self.cleanup_path, ignore_errors=True)


class ShopbangExtensionManager:
    """负责下载和解包上品帮插件。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def download_url(self) -> str:
        """构造 Chrome Web Store CRX 下载地址。"""

        extension_id = self.settings.shopbang_extension_id
        chrome_version = self.settings.shopbang_chrome_version
        return (
            "https://clients2.google.com/service/update2/crx"
            f"?response=redirect&prodversion={chrome_version}"
            f"&acceptformat=crx3&x=id%3D{extension_id}%26uc"
        )

    def download_extension(self) -> Path:
        """下载 CRX 插件文件到本地。"""

        destination = self.settings.shopbang_extension_crx_file
        destination.parent.mkdir(parents=True, exist_ok=True)

        with requests.get(
            self.download_url,
            stream=True,
            timeout=self.settings.shopbang_download_timeout_seconds,
            allow_redirects=True,
        ) as response:
            response.raise_for_status()
            with destination.open("wb") as output_file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        output_file.write(chunk)

        return destination

    def unpack_extension(self, source_file: Path | None = None, target_dir: Path | None = None) -> Path:
        """解包本地 ZIP 或 CRX3 文件。"""

        source_path = source_file or self.settings.shopbang_extension_crx_file
        unpack_dir = target_dir or self.settings.shopbang_extension_unpack_path

        if not source_path.exists():
            raise FileNotFoundError(f"未找到上品帮插件包: {source_path}")

        if unpack_dir.exists():
            shutil.rmtree(unpack_dir)
        unpack_dir.mkdir(parents=True, exist_ok=True)

        if source_path.suffix.lower() == ".zip":
            with zipfile.ZipFile(source_path) as zip_file:
                zip_file.extractall(unpack_dir)
            return unpack_dir

        with source_path.open("rb") as input_file:
            crx_data = input_file.read()

        zip_bytes = self._extract_zip_payload(crx_data)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zip_file:
            zip_file.extractall(unpack_dir)

        return unpack_dir

    def download_and_unpack(self) -> Path:
        """优先使用本地 ZIP，一键完成插件准备。"""

        local_zip = self.find_local_zip_package()
        if local_zip is not None:
            return self.unpack_extension(local_zip)

        crx_file = self.download_extension()
        return self.unpack_extension(crx_file)

    def find_local_zip_package(self) -> Path | None:
        """查找本地离线 ZIP 插件包。"""

        configured_zip = self.settings.shopbang_extension_zip_file
        if configured_zip is not None and configured_zip.exists():
            return configured_zip

        candidates = sorted(self.settings.shopbang_extension_root.glob("*.zip"))
        if not candidates:
            return None

        versioned_candidates = [path for path in candidates if "v" in path.stem.lower() or "插件" in path.stem]
        return versioned_candidates[-1] if versioned_candidates else candidates[-1]

    @staticmethod
    def _extract_zip_payload(crx_data: bytes) -> bytes:
        """从 CRX3 二进制中抽出 ZIP 载荷。"""

        if len(crx_data) < 12:
            raise ShopbangExtensionError("CRX 文件过短，无法解析。")

        magic = crx_data[:4]
        version = struct.unpack("<I", crx_data[4:8])[0]
        header_size = struct.unpack("<I", crx_data[8:12])[0]

        if magic != b"Cr24":
            raise ShopbangExtensionError("不是有效的 CRX 文件。")
        if version != 3:
            raise ShopbangExtensionError(f"仅支持 CRX3，当前版本为: {version}")

        zip_offset = 12 + header_size
        if zip_offset >= len(crx_data):
            raise ShopbangExtensionError("CRX 文件头异常，未找到 ZIP 数据。")

        return crx_data[zip_offset:]


class ShopbangLoginManager:
    """负责上品帮登录态与浏览器上下文。"""

    USERNAME_PLACEHOLDER = "请输入您的手机号/子账号"
    PASSWORD_PLACEHOLDER = "请输入您的密码"
    LOGIN_BUTTON_PATTERN = re.compile(r"登\s*录")

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def validate_extension_assets(self) -> None:
        """校验插件解包目录是否可用。"""

        unpack_path = self.settings.shopbang_extension_unpack_path
        if not unpack_path.exists():
            raise FileNotFoundError(
                f"未找到上品帮解包插件目录: {unpack_path}。"
                "请先执行 `python scripts/download_shopbang_extension.py`。"
            )

    def validate_collection_prerequisites(self) -> None:
        """校验采集前必须存在的插件和浏览器登录态资产。"""

        if self.should_use_cdp():
            return

        self.validate_extension_assets()

        if not self.settings.shopbang_user_data_path.exists():
            raise FileNotFoundError(
                f"未找到浏览器持久化 profile 目录: {self.settings.shopbang_user_data_path}。"
                "请先执行 `python scripts/login_shopbang.py` 生成浏览器登录环境。"
            )
        if not self.settings.shopbang_auth_state_path.exists():
            raise FileNotFoundError(
                f"未找到上品帮登录态文件: {self.settings.shopbang_auth_state_path}。"
                "请先执行 `python scripts/login_shopbang.py` 保存登录态。"
            )

    @property
    def has_login_credentials(self) -> bool:
        """判断是否已配置自动登录所需账号密码。"""

        return bool(self.settings.shopbang_username and self.settings.shopbang_password)

    def should_use_cdp(self) -> bool:
        """是否启用连接本机 Chrome 的 CDP 模式。"""

        return bool(self.settings.shopbang_cdp_url.strip())

    def ensure_logged_in(self, playwright: Playwright, allow_manual_fallback: bool = True) -> Path:
        """确保当前浏览器 profile 对应的上品帮登录态有效。"""

        session = self.open_browser_session(playwright=playwright)
        context = session.context

        try:
            if self.is_session_valid(context):
                return self.save_auth_state(context)

            if self.has_login_credentials:
                self.login_with_credentials(context)
                if self.is_session_valid(context):
                    return self.save_auth_state(context)
                raise RuntimeError("已执行自动登录，但上品帮 token 校验仍未通过。")

            if not allow_manual_fallback:
                raise RuntimeError(
                    "上品帮登录态无效，且未配置 `SHOPBANG_USERNAME` / `SHOPBANG_PASSWORD`，无法自动登录。"
                )

            if self.settings.shopbang_headless:
                raise RuntimeError(
                    "当前启用了后台模式，但上品帮登录态无效。"
                    " 后台模式下无法手动登录，请先前台执行登录或配置 `SHOPBANG_USERNAME` / `SHOPBANG_PASSWORD`。"
                )

            self.login_manually(context)
            if not self.is_session_valid(context):
                raise RuntimeError("手动登录完成后，仍未检测到有效的上品帮登录态。")
            return self.save_auth_state(context)
        finally:
            session.close()

    def login_and_save(self, playwright: Playwright) -> Path:
        """兼容旧脚本入口，内部统一走 ensure_logged_in。"""

        return self.ensure_logged_in(playwright=playwright, allow_manual_fallback=True)

    def is_session_valid(self, context: BrowserContext) -> bool:
        """检查当前浏览器上下文中是否存在上品帮 token cookie。"""

        return self.get_token_cookie_value(context) is not None

    def login_with_credentials(self, context: BrowserContext) -> None:
        """使用配置文件中的账号密码自动登录。"""

        page = context.new_page()

        try:
            page.goto(
                self.settings.shopbang_erp_login_url,
                wait_until="networkidle",
                timeout=self.settings.playwright_timeout_ms,
            )
            page.get_by_placeholder(self.USERNAME_PLACEHOLDER).fill(self.settings.shopbang_username)
            page.get_by_placeholder(self.PASSWORD_PLACEHOLDER).fill(self.settings.shopbang_password)
            page.get_by_role("button", name=self.LOGIN_BUTTON_PATTERN).click()
            self.wait_until_login_completed(context, page)
        finally:
            page.close()

    def login_manually(self, context: BrowserContext) -> None:
        """打开登录页，等待用户手动完成登录。"""

        page = context.new_page()

        try:
            page.goto(
                self.settings.shopbang_erp_login_url,
                wait_until="domcontentloaded",
                timeout=self.settings.playwright_timeout_ms,
            )
            print("请在浏览器中手动完成上品帮登录。")
            input("登录完成后按 Enter 保存 session...")
            self.wait_until_login_completed(context, page)
        finally:
            page.close()

    def wait_until_login_completed(self, context: BrowserContext, page) -> None:
        """等待登录完成并确认 token cookie 已就绪。"""

        deadline = time.time() + self.settings.shopbang_login_timeout_seconds
        while time.time() < deadline:
            page.wait_for_timeout(1000)
            current_url = page.url
            body_text = page.locator("body").inner_text()
            if self.has_token_cookie(context) and not self.is_login_page(current_url, body_text):
                return

        raise TimeoutError("等待上品帮登录完成超时。")

    def save_auth_state(self, context: BrowserContext) -> Path:
        """把当前浏览器状态保存为 storage state 文件。"""

        auth_state_path = self.settings.shopbang_auth_state_path
        auth_state_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(auth_state_path))
        return auth_state_path

    def has_token_cookie(self, context: BrowserContext) -> bool:
        """判断当前浏览器上下文中是否存在上品帮 token cookie。"""

        return self.get_token_cookie_value(context) is not None

    def get_token_cookie_value(self, context: BrowserContext) -> str | None:
        """读取上品帮 token cookie 值。"""

        cookies = context.cookies(self.settings.shopbang_login_url)
        for cookie in cookies:
            if cookie.get("name") == "token":
                return cookie.get("value") or None
        return None

    @classmethod
    def is_login_page(cls, current_url: str, body_text: str) -> bool:
        """基于 URL 和页面文本判断当前是否仍处于登录页。"""

        normalized_text = "".join(body_text.split())
        return (
            "#/login" in current_url
            or cls.USERNAME_PLACEHOLDER in body_text
            or cls.PASSWORD_PLACEHOLDER in body_text
            or "没有账号？去注册" in normalized_text
        )

    def launch_persistent_context(self, playwright: Playwright) -> BrowserContext:
        """启动带上品帮插件的持久化浏览器上下文。"""

        session = self.open_browser_session(playwright=playwright)
        return session.context

    def open_browser_session(self, playwright: Playwright) -> ShopbangBrowserSession:
        """返回上品帮浏览器会话，支持 CDP 连接本机 Chrome。"""

        if self.should_use_cdp():
            browser = playwright.chromium.connect_over_cdp(
                self.settings.shopbang_cdp_url,
                timeout=self.settings.playwright_timeout_ms,
            )
            if not browser.contexts:
                raise RuntimeError(
                    "已连接到本机 Chrome，但未发现可用浏览器上下文。"
                    " 请用带用户资料的 Chrome 启动远程调试，再重试。"
                )
            return ShopbangBrowserSession(
                context=browser.contexts[0],
                browser=browser,
                owns_context=False,
            )

        self.validate_extension_assets()

        if self.settings.playwright_browser != "chromium":
            raise ValueError("加载 Chrome 扩展时必须使用 chromium 浏览器。")

        browser_type: BrowserType = playwright.chromium
        launch_kwargs: dict = {
            "headless": self.settings.shopbang_headless,
            "args": self._build_extension_args(),
            "locale": self.settings.ozon_browser_locale,
            "timezone_id": self.settings.ozon_browser_timezone,
            "viewport": {"width": 1440, "height": 900},
            "slow_mo": self.settings.playwright_slow_mo_ms,
        }

        if self.settings.ozon_user_agent:
            launch_kwargs["user_agent"] = self.settings.ozon_user_agent

        if self.settings.playwright_proxy_url:
            launch_kwargs["proxy"] = {"server": self.settings.playwright_proxy_url}

        if self.settings.playwright_channel:
            launch_kwargs["channel"] = self.settings.playwright_channel
        if self.settings.playwright_executable_file:
            launch_kwargs["executable_path"] = str(self.settings.playwright_executable_file)

        context = browser_type.launch_persistent_context(
            user_data_dir=str(self.settings.shopbang_user_data_path),
            **launch_kwargs,
        )
        return ShopbangBrowserSession(context=context, owns_context=True)

    def _build_extension_args(self) -> list[str]:
        """构建 Chromium 插件加载参数。"""

        unpack_path = self.settings.shopbang_extension_unpack_path
        return [
            f"--disable-extensions-except={unpack_path}",
            f"--load-extension={unpack_path}",
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ]
