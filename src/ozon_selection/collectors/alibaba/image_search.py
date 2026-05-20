"""1688 浏览器版以图搜图。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import re
import shutil
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import Browser, BrowserContext, Locator, Page, Playwright
import requests

from config.settings import Settings, get_settings


class Alibaba1688ImageSearchError(RuntimeError):
    """1688 图搜图异常。"""


@dataclass(slots=True)
class Alibaba1688BrowserSession:
    """封装 1688 浏览器会话，兼容本地启动和 CDP 连接模式。"""

    context: BrowserContext
    browser: Browser | None = None
    owns_context: bool = True
    on_close: Callable[[], None] | None = None

    def close(self) -> None:
        """关闭当前会话。"""

        if self.owns_context:
            self.context.close()
        if self.on_close is not None:
            self.on_close()
        return


class Alibaba1688ImageSearchBrowser:
    """使用 Playwright 打开 1688 结果页，并提取图搜图商品。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._detail_request_timestamps: deque[float] = deque()
        self._time_func = time.monotonic
        self._sleep_func = time.sleep

    def get_cdp_url(self) -> str:
        """返回 1688 可用的 CDP 地址，优先用专属配置，其次复用上品帮 CDP。"""

        if self.settings.alibaba1688_cdp_url.strip():
            return self.settings.alibaba1688_cdp_url.strip()
        return self.settings.shopbang_cdp_url.strip()

    def get_adspower_api_url(self) -> str:
        """返回 AdsPower Local API 地址，并兼容 localhost 回退。"""

        api_url = self.settings.alibaba1688_adspower_api_url.strip().rstrip("/")
        if not api_url:
            return ""
        return api_url.replace("://local.adspower.net", "://localhost")

    def should_use_adspower(self) -> bool:
        """判断是否启用 AdsPower Local API。"""

        return bool(
            self.get_adspower_api_url()
            and self.settings.alibaba1688_adspower_profile_id.strip()
        )

    def should_use_cdp(self) -> bool:
        """判断是否启用 CDP 连接到本机 Chrome。"""

        return bool(self.get_cdp_url())

    def open_browser_session(
        self,
        playwright: Playwright,
        *,
        headless: bool | None = None,
        user_data_dir: Path | None = None,
    ) -> Alibaba1688BrowserSession:
        """打开 1688 浏览器会话，支持 CDP 连接。"""

        if self.should_use_adspower():
            return self.open_adspower_session(playwright, headless=headless)

        if self.should_use_cdp():
            cdp_url = self.get_cdp_url()
            print(f"[1688] connecting to CDP browser: {cdp_url}", flush=True)
            browser = playwright.chromium.connect_over_cdp(
                cdp_url,
                timeout=self.settings.playwright_timeout_ms,
            )
            if not browser.contexts:
                raise Alibaba1688ImageSearchError(
                    "已连接到 CDP 浏览器，但未发现可用浏览器上下文。"
                    " 请确认 http://127.0.0.1:9222 对应的 Chrome 已正常打开。"
                )
            return Alibaba1688BrowserSession(
                context=browser.contexts[0],
                browser=browser,
                owns_context=False,
            )

        context = self.launch_persistent_context(
            playwright,
            headless=headless,
            user_data_dir=user_data_dir,
        )
        return Alibaba1688BrowserSession(context=context, owns_context=True)

    def open_adspower_session(
        self,
        playwright: Playwright,
        *,
        headless: bool | None = None,
    ) -> Alibaba1688BrowserSession:
        """通过 AdsPower Local API 启动指定 profile，并连接到返回的 CDP 端点。"""

        profile_id = self.settings.alibaba1688_adspower_profile_id.strip()
        if not profile_id:
            raise Alibaba1688ImageSearchError(
                "已配置 AdsPower API，但缺少 `ALIBABA1688_ADSPOWER_PROFILE_ID`。"
            )

        response = self.start_adspower_browser(profile_id=profile_id, headless=headless)
        ws_endpoint = (
            (((response.get("data") or {}).get("ws") or {}).get("puppeteer") or "").strip()
        )
        debug_port = str((response.get("data") or {}).get("debug_port") or "").strip()
        cdp_endpoint = ws_endpoint or (f"http://127.0.0.1:{debug_port}" if debug_port else "")
        if not cdp_endpoint:
            raise Alibaba1688ImageSearchError(
                f"AdsPower 已启动 profile={profile_id}，但返回里缺少可用的 CDP 地址。"
            )

        print(f"[1688] connecting to AdsPower browser: {cdp_endpoint}", flush=True)
        browser = playwright.chromium.connect_over_cdp(
            cdp_endpoint,
            timeout=self.settings.playwright_timeout_ms,
        )
        if not browser.contexts:
            self.stop_adspower_browser(profile_id=profile_id)
            raise Alibaba1688ImageSearchError(
                "已连接到 AdsPower 浏览器，但未发现可用浏览器上下文。"
            )
        return Alibaba1688BrowserSession(
            context=browser.contexts[0],
            browser=browser,
            owns_context=False,
            on_close=lambda: self.stop_adspower_browser(profile_id=profile_id),
        )

    def start_adspower_browser(self, *, profile_id: str, headless: bool | None = None) -> dict[str, Any]:
        """调用 AdsPower Local API 启动浏览器 profile。"""

        endpoint = f"{self.get_adspower_api_url()}/api/v1/browser/start"
        headless_flag = int(self.settings.alibaba1688_headless if headless is None else headless)
        try:
            response = requests.get(
                endpoint,
                headers=self.build_adspower_headers(),
                params={"user_id": profile_id, "headless": headless_flag},
                timeout=self.settings.openai_timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise Alibaba1688ImageSearchError(f"调用 AdsPower 启动接口失败: {exc}") from exc

        payload = response.json()
        if int(payload.get("code", -1)) != 0:
            raise Alibaba1688ImageSearchError(
                f"AdsPower 启动失败: {payload.get('msg') or payload}"
            )
        return payload

    def stop_adspower_browser(self, *, profile_id: str) -> None:
        """调用 AdsPower Local API 停止浏览器 profile。"""

        endpoint = f"{self.get_adspower_api_url()}/api/v1/browser/stop"
        try:
            response = requests.get(
                endpoint,
                headers=self.build_adspower_headers(),
                params={"user_id": profile_id},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            if int(payload.get("code", -1)) != 0:
                print(
                    f"[1688] AdsPower stop warning for profile={profile_id}: {payload.get('msg') or payload}",
                    flush=True,
                )
        except Exception as exc:
            print(
                f"[1688] AdsPower stop warning for profile={profile_id}: {exc}",
                flush=True,
            )

    def build_adspower_headers(self) -> dict[str, str]:
        """构造 AdsPower Local API 请求头。"""

        api_key = self.settings.alibaba1688_adspower_api_key.strip()
        if not api_key:
            return {}
        return {"Authorization": f"Bearer {api_key}"}

    def launch_persistent_context(
        self,
        playwright: Playwright,
        *,
        headless: bool | None = None,
        user_data_dir: Path | None = None,
    ) -> BrowserContext:
        """启动带持久化 profile 的 Chromium。"""

        print("[1688] starting Chromium persistent context...", flush=True)
        launch_kwargs: dict[str, Any] = {
            "headless": self.settings.alibaba1688_headless if headless is None else headless,
            "locale": "zh-CN",
            "viewport": {"width": 1440, "height": 900},
            "slow_mo": self.settings.playwright_slow_mo_ms,
        }
        if self.settings.ozon_user_agent:
            launch_kwargs["user_agent"] = self.settings.ozon_user_agent
        if self.settings.playwright_proxy_url:
            launch_kwargs["proxy"] = {"server": self.settings.playwright_proxy_url}
        if self.settings.playwright_channel:
            launch_kwargs["channel"] = self.settings.playwright_channel
        if self.settings.playwright_executable_path:
            launch_kwargs["executable_path"] = self.settings.playwright_executable_path

        profile_path = user_data_dir or self.settings.alibaba1688_user_data_path
        profile_path.mkdir(parents=True, exist_ok=True)
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            **launch_kwargs,
        )
        print("[1688] Chromium context started.", flush=True)
        return context

    def build_search_url(self, image_url: str) -> str:
        """构造 1688 图搜图结果页 URL。"""

        base = self.settings.alibaba1688_image_search_url.strip().rstrip("?")
        params = {
            "tab": self.settings.alibaba1688_image_search_tab,
            "imageAddress": image_url,
        }
        return f"{base}?{urllib.parse.urlencode(params)}"

    def ensure_logged_in(self, context: BrowserContext, page: Page) -> Path:
        """如果被重定向到登录页，则等待用户在浏览器里手动登录并保存状态。"""

        if self.is_login_page(page):
            print("[1688] login status: not_logged_in", flush=True)
            if self.settings.alibaba1688_headless:
                raise Alibaba1688ImageSearchError(
                    "当前启用了后台模式，但 1688 登录态已失效。"
                    " 后台模式下无法手动登录，请先前台执行 `python scripts/login_1688.py`。"
                )
            print("检测到 1688 登录页，请在浏览器中完成登录，脚本会自动继续。", flush=True)
            deadline = time.time() + self.settings.alibaba1688_login_timeout_seconds
            while time.time() < deadline:
                page.wait_for_timeout(1000)
                if not self.is_login_page(page):
                    page.wait_for_timeout(self.settings.alibaba1688_wait_after_login_ms)
                    print("[1688] login status: logged_in_after_manual_auth", flush=True)
                    return self.save_auth_state(context)
            raise Alibaba1688ImageSearchError("等待 1688 登录完成超时。")

        print("[1688] login status: already_logged_in", flush=True)
        return self.save_auth_state(context)

    def is_login_page(self, page: Page) -> bool:
        """判断当前是否在登录页。"""

        current_url = page.url
        return "login.taobao.com" in current_url or "login.1688.com" in current_url

    @staticmethod
    def is_punish_page(page: Page) -> bool:
        """判断当前是否命中 1688 风控页。"""

        current_url = page.url.lower()
        return "_____tmd_____" in current_url or "x5secdata=" in current_url or "/punish?" in current_url

    def save_auth_state(self, context: BrowserContext) -> Path:
        """把 1688 浏览器状态保存成 storage state 文件。"""

        auth_state_path = self.settings.alibaba1688_auth_state_path
        auth_state_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(auth_state_path))
        return auth_state_path

    def login_and_save(self, playwright: Playwright) -> Path:
        """显式打开 1688 登录页，等待用户手动登录并保存状态。"""

        session = self.open_browser_session(playwright, headless=False)
        context = session.context
        page = context.new_page()

        try:
            print(f"[1688] opening login page: {self.settings.alibaba1688_login_url}", flush=True)
            page.goto(
                self.settings.alibaba1688_login_url,
                wait_until="domcontentloaded",
                timeout=self.settings.playwright_timeout_ms,
            )
            print("请在浏览器中完成 1688 登录。")
            deadline = time.time() + self.settings.alibaba1688_login_timeout_seconds
            while time.time() < deadline:
                page.wait_for_timeout(1000)
                if not self.is_login_page(page):
                    page.wait_for_timeout(self.settings.alibaba1688_wait_after_login_ms)
                    return self.save_auth_state(context)

            raise Alibaba1688ImageSearchError("等待 1688 登录完成超时。")
        finally:
            page.close()
            session.close()

    def ensure_ready_context(self, playwright: Playwright) -> tuple[Alibaba1688BrowserSession, Path]:
        """启动浏览器并确保 1688 登录态有效。"""

        print("[1688] checking account login status...", flush=True)
        session = self.open_browser_session(
            playwright,
            headless=self.settings.alibaba1688_headless,
            user_data_dir=self.prepare_profile_copy(),
        )
        context = session.context
        page = context.new_page()

        try:
            auth_state_path = self.open_image_search_entry(context, page, log_prefix="[1688] opening image search page")
            print(f"[1688] auth state saved: {auth_state_path}", flush=True)
            return session, auth_state_path
        except Exception:
            session.close()
            raise
        finally:
            try:
                page.close()
            except Exception:
                pass

    def search_by_image(
        self,
        *,
        playwright: Playwright,
        image_url: str,
        max_results: int | None = None,
        enrich_details: bool = True,
    ) -> dict[str, Any]:
        """执行 1688 浏览器图搜图。"""

        session = self.open_browser_session(playwright)
        context = session.context
        page = context.new_page()
        max_count = max_results or self.settings.alibaba1688_max_results
        search_url = self.build_search_url(image_url)

        try:
            page.goto(
                search_url,
                wait_until="domcontentloaded",
                timeout=self.settings.playwright_timeout_ms,
            )
            self.ensure_logged_in(context, page)
            if self.is_login_page(page):
                page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=self.settings.playwright_timeout_ms,
                )

            page.wait_for_timeout(self.settings.alibaba1688_result_wait_ms)
            results = self.extract_results(page, max_count)
            if enrich_details and results:
                self.enrich_results_with_detail(context, results)
            return {
                "search_url": search_url,
                "final_url": page.url,
                "logged_in": not self.is_login_page(page),
                "items": results,
            }
        finally:
            page.close()
            session.close()

    def search_by_uploaded_image(
        self,
        *,
        playwright: Playwright,
        image_path: str | Path,
        max_results: int | None = None,
        enrich_details: bool = True,
    ) -> dict[str, Any]:
        """上传本地图片到 1688，执行图搜图。"""

        session, auth_state_path = self.ensure_ready_context(playwright)
        context = session.context
        try:
            result = self.search_by_uploaded_image_in_context(
                context,
                image_path=image_path,
                max_results=max_results,
                enrich_details=enrich_details,
            )
            result["auth_state_path"] = str(auth_state_path)
            return result
        finally:
            session.close()

    def search_by_uploaded_image_in_context(
        self,
        context: BrowserContext,
        *,
        image_path: str | Path,
        page: Page | None = None,
        max_results: int | None = None,
        enrich_details: bool = True,
    ) -> dict[str, Any]:
        """在已有 1688 会话中上传本地图片，并抓取第一页结果。"""

        image_file = Path(image_path)
        if not image_file.exists():
            raise FileNotFoundError(f"未找到待上传图片: {image_file}")

        owns_page = page is None
        page = page or context.new_page()
        max_count = max_results or self.settings.alibaba1688_max_results

        try:
            self.ensure_upload_page_ready(
                context,
                page,
                log_prefix="[1688] preparing image search page for upload",
            )
            page.wait_for_timeout(1_500)
            upload_input = self.find_upload_input(page)
            self.clear_previous_uploaded_image(page, upload_input)
            print(f"[1688] uploading local image: {image_file}", flush=True)
            upload_input.set_input_files(str(image_file))
            existing_pages = list(context.pages)
            self.click_search_image_button(page)
            active_page = self.resolve_result_page_after_submit(
                context,
                current_page=page,
                existing_pages=existing_pages,
            )
            self.wait_for_search_results(active_page)
            print(f"[1688] results page reached: {active_page.url}", flush=True)
            results = self.extract_results(active_page, max_count)
            if not results:
                self.print_debug_state(active_page)
            if enrich_details and results:
                print(f"[1688] enriching {len(results)} detail pages...", flush=True)
                self.enrich_results_with_detail(context, results)
            return {
                "search_url": self.settings.alibaba1688_image_search_url,
                "final_url": active_page.url,
                "logged_in": not self.is_login_page(active_page),
                "image_path": str(image_file),
                "_active_page": active_page,
                "items": results,
            }
        finally:
            if owns_page and not page.is_closed():
                page.close()

    def ensure_upload_page_ready(self, context: BrowserContext, page: Page, *, log_prefix: str) -> Path:
        """优先复用当前页；只有找不到上传控件时才重新打开首页入口。"""

        last_auth_state_path = self.save_auth_state(context)
        current_url = (page.url or "").strip()

        if current_url:
            if self.is_login_page(page):
                return self.ensure_logged_in(context, page)
            if self.is_punish_page(page):
                if not self.settings.alibaba1688_headless:
                    return self.recover_from_punish_page(context, page)
                return self.open_image_search_entry(context, page, log_prefix=log_prefix)

            try:
                self.find_upload_input(page)
                print(f"[1688] reusing current page for next upload: {page.url}", flush=True)
                return last_auth_state_path
            except Alibaba1688ImageSearchError:
                print("[1688] current page has no upload control, reopening entry page...", flush=True)

        return self.open_image_search_entry(context, page, log_prefix=log_prefix)

    def clear_previous_uploaded_image(self, page: Page, upload_input: Locator) -> None:
        """复用当前页时尽量清掉上一张已上传图片，避免第二次上传仍沿用旧图。"""

        cleared = False
        try:
            upload_input.set_input_files([])
            cleared = True
        except Exception:
            pass

        try:
            dom_cleared = bool(
                page.evaluate(
                    """
                    () => {
                      const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                      const clickableSelectors = [
                        'div[class*="close"]',
                        'div[class*="Close"]',
                        'button[class*="close"]',
                        'button[class*="Close"]',
                        'span[class*="close"]',
                        'span[class*="Close"]',
                        'i[class*="close"]',
                        'i[class*="Close"]',
                        'svg[class*="close"]',
                        '[aria-label*="删除"]',
                        '[aria-label*="关闭"]',
                      ];
                      const textHints = ['删除', '移除', '关闭', '清空', '重选', '重新上传', '换一张', '重新选择'];
                      const nodes = Array.from(document.querySelectorAll(clickableSelectors.join(',')));
                      const textNodes = Array.from(document.querySelectorAll('div,span,button,a')).filter((node) => {
                        const text = normalize(node.innerText || '');
                        return textHints.some((hint) => text.includes(hint));
                      });
                      const candidates = [...nodes, ...textNodes];
                      for (const node of candidates) {
                        if (!(node instanceof HTMLElement)) continue;
                        const style = window.getComputedStyle(node);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        const rect = node.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) continue;
                        node.click();
                        return true;
                      }
                      return false;
                    }
                    """
                )
            )
            cleared = dom_cleared or cleared
        except Exception:
            pass

        if cleared:
            print("[1688] cleared previous uploaded image on current page.", flush=True)
            page.wait_for_timeout(600)

    def resolve_result_page_after_submit(
        self,
        context: BrowserContext,
        *,
        current_page: Page,
        existing_pages: list[Page],
    ) -> Page:
        """点击“搜索图片”后，如果新开了结果页标签，则切换到新标签继续。"""

        deadline = time.time() + 8
        known_ids = {id(page) for page in existing_pages}
        while time.time() < deadline:
            for page in context.pages:
                if id(page) in known_ids:
                    continue
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=3_000)
                except Exception:
                    pass
                print(f"[1688] switched to newly opened result page: {page.url}", flush=True)
                return page

            if "youyuan/index.htm" in current_page.url or "imageSearch" in current_page.url:
                return current_page
            current_page.wait_for_timeout(300)

        return current_page

    def open_image_search_entry(self, context: BrowserContext, page: Page, *, log_prefix: str) -> Path:
        """先打开 1688 首页，再在首页内进入以图搜图入口。"""

        last_auth_state_path: Path | None = None
        for attempt in range(1, self.settings.alibaba1688_open_retry_count + 1):
            print(f"{log_prefix}: {self.settings.alibaba1688_base_url}", flush=True)
            page.goto(
                self.settings.alibaba1688_base_url,
                wait_until="domcontentloaded",
                timeout=self.settings.playwright_timeout_ms,
            )
            print(f"[1688] homepage before image search: {page.url}", flush=True)
            last_auth_state_path = self.ensure_logged_in(context, page)
            if not self.is_punish_page(page):
                return last_auth_state_path

            if not self.settings.alibaba1688_headless:
                print("[1688] punish page detected, opening homepage for manual recovery...", flush=True)
                last_auth_state_path = self.recover_from_punish_page(context, page)
                continue

            print(
                f"[1688] punish page detected, retrying open ({attempt}/{self.settings.alibaba1688_open_retry_count})",
                flush=True,
            )
            page.wait_for_timeout(self.settings.alibaba1688_retry_wait_ms)

        raise Alibaba1688ImageSearchError("打开 1688 首页时触发风控页，重试后仍未恢复。")

    def recover_from_punish_page(self, context: BrowserContext, page: Page) -> Path:
        """前台模式下跳到 1688 首页，等待用户手动完成风控或重新登录。"""

        if self.settings.alibaba1688_headless:
            raise Alibaba1688ImageSearchError(
                "当前启用了后台模式，但 1688 图搜入口触发了风控页。"
                " 后台模式下无法手动过验证，请先前台运行并处理风控。"
            )

        print(f"[1688] opening homepage for manual recovery: {self.settings.alibaba1688_base_url}", flush=True)
        page.goto(
            self.settings.alibaba1688_base_url,
            wait_until="domcontentloaded",
            timeout=self.settings.playwright_timeout_ms,
        )
        print(
            "检测到 1688 风控页。请在浏览器中手动完成验证；如被要求重新登录，也请一并完成。"
            " 验证恢复后脚本会自动继续。",
            flush=True,
        )

        deadline = time.time() + self.settings.alibaba1688_login_timeout_seconds
        while time.time() < deadline:
            page.wait_for_timeout(1000)
            if self.is_login_page(page):
                continue
            if self.is_punish_page(page):
                continue

            page.wait_for_timeout(self.settings.alibaba1688_wait_after_login_ms)
            print(f"[1688] manual recovery completed: {page.url}", flush=True)
            return self.save_auth_state(context)

        raise Alibaba1688ImageSearchError("等待手动处理 1688 风控页超时。")

    def prepare_profile_copy(self) -> Path:
        """创建 1688 浏览器 profile 的可复用副本。"""

        source_profile = self.settings.alibaba1688_user_data_path
        if not source_profile.exists():
            source_profile.mkdir(parents=True, exist_ok=True)
            return source_profile

        target_dir = self.settings.project_root / "browser-profile-1688-e2e"
        if target_dir.exists():
            shutil.rmtree(target_dir)

        shutil.copytree(
            source_profile,
            target_dir,
            ignore=shutil.ignore_patterns(
                "SingletonLock",
                "SingletonSocket",
                "SingletonCookie",
                "RunningChromeVersion",
            ),
        )

        for pattern in (
            "SingletonLock",
            "SingletonSocket",
            "SingletonCookie",
            "lockfile",
            "Default/LOCK",
            "Default/Code Cache/js/index.lock",
        ):
            for path in target_dir.glob(pattern):
                if path.is_file() or path.is_symlink():
                    path.unlink(missing_ok=True)

        return target_dir

    def find_upload_input(self, page: Page) -> Locator:
        """定位 1688 图搜图上传控件。"""

        selectors = [
            'input[type="file"]',
            'input[accept*="image"]',
            'input[name*="file"]',
            'input[class*="upload"]',
        ]

        locator = self.find_locator_in_page(page, selectors)
        if locator is not None:
            return locator

        for text in ("以图搜货", "搜同款", "上传图片", "图片搜索", "拍立淘"):
            try:
                trigger = page.get_by_text(text, exact=False).first
                if trigger.count() > 0:
                    trigger.click(timeout=1500)
                    page.wait_for_timeout(1000)
                    locator = self.find_locator_in_page(page, selectors)
                    if locator is not None:
                        return locator
            except Exception:
                continue

        raise Alibaba1688ImageSearchError("未找到 1688 图搜图上传控件，无法上传本地主图。")

    @staticmethod
    def find_locator_in_page(page: Page, selectors: list[str]) -> Locator | None:
        """在页面和 iframe 中查找匹配任一 selector 的节点。"""

        for root in [page, *page.frames]:
            for selector in selectors:
                try:
                    locator = root.locator(selector).first
                    if locator.count() > 0:
                        return locator
                except Exception:
                    continue
        return None

    def wait_for_search_results(self, page: Page) -> None:
        """等待图搜图结果页渲染完成。"""

        try:
            page.wait_for_function(
                """
                () => document.querySelectorAll('a[href*="detail.1688.com/offer/"]').length > 0
                """,
                timeout=self.settings.playwright_timeout_ms,
            )
        except Exception:
            page.wait_for_timeout(self.settings.alibaba1688_result_wait_ms)

    def click_search_image_button(self, page: Page) -> None:
        """上传图片后点击 1688 的“搜索图片”按钮。"""

        selectors = [
            'div.search-btn[data-tracker="pasteImagePreview"]',
            'div.search-btn[data-trackercn="粘贴图片预览"]',
            'div.search-btn',
        ]

        deadline = time.time() + 8
        while time.time() < deadline:
            button = self.find_locator_in_page(page, selectors)
            if button is not None:
                try:
                    text = button.inner_text(timeout=1000).strip()
                except Exception:
                    text = ""
                try:
                    print(f"[1688] clicking search button: {text or 'search-btn'}", flush=True)
                    button.click(timeout=3_000, force=True)
                    page.wait_for_timeout(1_000)
                    return
                except Exception:
                    page.wait_for_timeout(500)

            for root in [page, *page.frames]:
                try:
                    text_button = root.get_by_text("搜索图片", exact=True).first
                    if text_button.count() > 0:
                        print("[1688] clicking search button by text: 搜索图片", flush=True)
                        text_button.click(timeout=3_000, force=True)
                        page.wait_for_timeout(1_000)
                        return
                except Exception:
                    continue

            page.wait_for_timeout(500)

        self.print_debug_state(page)

        raise Alibaba1688ImageSearchError("上传图片后未找到“搜索图片”按钮，无法提交 1688 图搜图。")


    def print_debug_state(self, page: Page) -> None:
        """打印当前页面与 iframe 的简要调试信息。"""

        title = page.title()
        body_text = page.locator("body").inner_text()[:800]
        page_links = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('a'))
              .slice(0, 30)
              .map((node) => ({
                href: node.href || node.getAttribute('href') || '',
                text: (node.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
                className: node.className || '',
              }))
            """
        )
        print(f"[1688][debug] page title: {title}", flush=True)
        print(f"[1688][debug] page url: {page.url}", flush=True)
        print(f"[1688][debug] body excerpt: {body_text}", flush=True)
        print(f"[1688][debug] first page links: {page_links}", flush=True)

        for index, frame in enumerate(page.frames):
            try:
                link_count = frame.locator('a[href*="detail.1688.com/offer/"]').count()
            except Exception:
                link_count = -1
            try:
                frame_links = frame.evaluate(
                    """
                    () => Array.from(document.querySelectorAll('a'))
                      .slice(0, 10)
                      .map((node) => ({
                        href: node.href || node.getAttribute('href') || '',
                        text: (node.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
                        className: node.className || '',
                      }))
                    """
                )
            except Exception:
                frame_links = []
            print(
                f"[1688][debug] frame[{index}] url={frame.url} offer_links={link_count} links={frame_links}",
                flush=True,
            )

    def extract_results(self, page: Page, max_results: int) -> list[dict[str, Any]]:
        """从 1688 图搜图结果页提取商品。"""

        results: list[dict[str, Any]] = page.evaluate(
            f"""
            () => {{
              const items = [];
              const seen = new Set();
              const limit = {max_results};

              const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
              const normalizePrice = (value) => (value || '').replace(/\\s+/g, '');

              const pushItem = (item) => {{
                if (!item.detail_url) return;
                if (seen.has(item.detail_url)) return;
                seen.add(item.detail_url);
                items.push(item);
              }};

              const findCard = (node) => {{
                let current = node;
                while (current && current !== document.body) {{
                  const text = normalize(current.innerText);
                  if (
                    text &&
                    text.includes('起批') &&
                    (text.includes('¥') || text.includes('￥')) &&
                    text.length <= 1200
                  ) {{
                    return current;
                  }}
                  current = current.parentElement;
                }}
                return node.parentElement;
              }};

              const readCard = (card, detailUrl, titleHint = '') => {{
                const cardText = normalize(card ? card.innerText : '');
                const lines = (cardText || '')
                  .split(/\\n+/)
                  .map((line) => normalize(line))
                  .filter(Boolean);
                const filteredTitleLines = lines.filter((line) => !(
                  line === '旺旺在线' ||
                  line === '先采后付' ||
                  line.includes('起批') ||
                  line.includes('运费') ||
                  line.includes('回头率') ||
                  line.includes('入驻') ||
                  /^¥|^￥/.test(line) ||
                  /件$/.test(line)
                ));
                const title =
                  normalize(titleHint) ||
                  filteredTitleLines[0] ||
                  normalize(card?.querySelector('img')?.alt) ||
                  cardText.slice(0, 120);

                const image = card?.querySelector('img');
                const imageUrl =
                  image?.src ||
                  image?.getAttribute('data-src') ||
                  image?.getAttribute('src') ||
                  '';
                const priceMatch = normalizePrice(cardText).match(/[¥￥]([0-9]+(?:\\.[0-9]+)?)/);
                const moqMatch = cardText.match(/(\\d+)\\s*(件|个|箱|包)起批/);
                const sellerLine =
                  lines.find((line) => line.includes('有限公司') || line.includes('厂') || line.includes('店')) || '';

                pushItem({{
                  title,
                  detail_url: detailUrl,
                  image_url: imageUrl,
                  price: priceMatch ? priceMatch[1] : null,
                  min_order: moqMatch ? moqMatch[0] : null,
                  seller: sellerLine,
                  raw_text: cardText,
                }});
              }};

              const detailLinks = Array.from(document.querySelectorAll('a[href*="detail.1688.com/offer/"]'));
              for (const link of detailLinks) {{
                if (items.length >= limit) break;
                const href = link.href || link.getAttribute('href') || '';
                const card = findCard(link);
                readCard(card, href, link.innerText);
              }}

              if (items.length < limit) {{
                const wwLinks = Array.from(document.querySelectorAll('a[href*="offerId="]'));
                for (const link of wwLinks) {{
                  if (items.length >= limit) break;
                  const href = link.href || link.getAttribute('href') || '';
                  let offerId = '';
                  try {{
                    offerId = new URL(href, window.location.href).searchParams.get('offerId') || '';
                  }} catch (error) {{
                    const matched = href.match(/[?&]offerId=(\\d+)/);
                    offerId = matched ? matched[1] : '';
                  }}
                  if (!offerId) continue;
                  const detailUrl = `https://detail.1688.com/offer/${{offerId}}.html`;
                  const card = findCard(link);
                  readCard(card, detailUrl);
                }}
              }}

              return items;
            }}
            """
        )
        return results

    def enrich_results_with_detail(self, context: BrowserContext, items: list[dict[str, Any]]) -> None:
        """逐个打开 1688 商品详情页，补抓价格和重量信息。"""

        for item in items:
            detail_url = (item.get("detail_url") or "").strip()
            if not detail_url:
                continue

            self.wait_for_detail_rate_limit()
            detail_page = context.new_page()
            try:
                detail_page.goto(
                    detail_url,
                    wait_until="domcontentloaded",
                    timeout=self.settings.playwright_timeout_ms,
                )
                detail_page.wait_for_timeout(2_500)
                detail_snapshot = self.extract_detail_snapshot(detail_page)
                item.update({key: value for key, value in detail_snapshot.items() if value not in (None, "", [])})
                item["detail_enriched"] = True
            except Exception as exc:
                item["detail_error"] = str(exc)
            finally:
                detail_page.close()

    def wait_for_detail_rate_limit(self) -> None:
        """控制 1688 详情页访问频率，避免短时间内打开过多详情页。"""

        limit_count = max(int(self.settings.alibaba1688_detail_rate_limit_count), 0)
        window_seconds = max(int(self.settings.alibaba1688_detail_rate_limit_window_seconds), 0)
        if limit_count == 0 or window_seconds == 0:
            return

        while True:
            now = self._time_func()
            self.evict_expired_detail_request_timestamps(now=now, window_seconds=window_seconds)
            if len(self._detail_request_timestamps) < limit_count:
                self._detail_request_timestamps.append(now)
                return

            oldest = self._detail_request_timestamps[0]
            wait_seconds = max(window_seconds - (now - oldest), 0.0)
            if wait_seconds <= 0:
                self._detail_request_timestamps.popleft()
                continue

            print(
                f"[1688] detail rate limit reached, sleeping {wait_seconds:.1f}s "
                f"({limit_count} requests/{window_seconds}s)",
                flush=True,
            )
            self._sleep_func(wait_seconds)

    def evict_expired_detail_request_timestamps(self, *, now: float, window_seconds: int) -> None:
        """移除滑动窗口外的详情页访问时间戳。"""

        while self._detail_request_timestamps and now - self._detail_request_timestamps[0] >= window_seconds:
            self._detail_request_timestamps.popleft()

    def extract_detail_snapshot(self, page: Page) -> dict[str, Any]:
        """从 1688 详情页提取标题、价格、单价、重量和属性。"""

        payload: dict[str, Any] = page.evaluate(
            """
            () => {
              const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
              const title =
                normalize(document.querySelector('h1')?.innerText) ||
                normalize(document.title);
              const bodyText = normalize(document.body?.innerText || '');
              const priceCandidates = Array.from(
                document.querySelectorAll(
                  '[class*="price"], [class*="Price"], [data-testid*="price"], .price, .price-text'
                )
              )
                .map((node) => normalize(node.innerText))
                .filter((value) => value && value.length <= 80);
              const attrs = [];
              const seen = new Set();
              const pushAttr = (key, value) => {
                const normalizedKey = normalize(key);
                const normalizedValue = normalize(value);
                if (!normalizedKey || !normalizedValue) return;
                if (normalizedKey.length > 60 || normalizedValue.length > 200) return;
                const composite = `${normalizedKey}::${normalizedValue}`;
                if (seen.has(composite)) return;
                seen.add(composite);
                attrs.push({ key: normalizedKey, value: normalizedValue });
              };

              document.querySelectorAll('dl').forEach((dl) => {
                const keys = Array.from(dl.querySelectorAll('dt'));
                const values = Array.from(dl.querySelectorAll('dd'));
                const count = Math.min(keys.length, values.length);
                for (let index = 0; index < count; index += 1) {
                  pushAttr(keys[index].innerText, values[index].innerText);
                }
              });

              document.querySelectorAll('table tr').forEach((row) => {
                const cells = row.querySelectorAll('th,td');
                if (cells.length >= 2) {
                  pushAttr(cells[0].innerText, cells[1].innerText);
                }
              });

              document.querySelectorAll('[data-widget], [class*="attr"], [class*="Attr"]').forEach((root) => {
                root.querySelectorAll('div').forEach((node) => {
                  const children = Array.from(node.children || []);
                  if (children.length === 2) {
                    const left = children[0].innerText || '';
                    const right = children[1].innerText || '';
                    if (left && right) {
                      pushAttr(left, right);
                    }
                  }
                });
              });

              return {
                title,
                body_text: bodyText,
                price_candidates: Array.from(new Set(priceCandidates)).slice(0, 30),
                attributes: attrs.slice(0, 40),
                final_url: window.location.href,
              };
            }
            """
        )

        body_text = str(payload.get("body_text") or "")
        price_candidates = [str(item) for item in payload.get("price_candidates") or []]

        price_text = self.pick_price_text(price_candidates, body_text)
        unit_price_text = self.pick_unit_price_text(price_candidates, body_text)
        weight_text = self.pick_weight_text(body_text)

        return {
            "title": payload.get("title"),
            "detail_url": payload.get("final_url") or page.url,
            "price_text": price_text,
            "price": self.parse_price_value(price_text),
            "unit_price_text": unit_price_text,
            "unit_price": self.parse_price_value(unit_price_text) or self.parse_price_value(price_text),
            "weight_text": weight_text,
            "weight_grams": self.parse_weight_grams(weight_text),
            "attributes": self.clean_attributes(payload.get("attributes")),
        }

    @classmethod
    def clean_attributes(cls, attributes: list[dict[str, Any]] | None) -> list[dict[str, str]]:
        """清洗详情页属性，过滤占位值、表头噪声和语义重复项。"""

        if not attributes:
            return []

        cleaned: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in attributes:
            if not isinstance(item, dict):
                continue

            key = cls.normalize_attribute_text(item.get("key"))
            value = cls.normalize_attribute_text(item.get("value"))
            if not key or not value:
                continue
            if len(key) > 60 or len(value) > 200:
                continue
            if cls.is_noise_attribute(key, value):
                continue

            fingerprint = f"{cls.normalize_attribute_compare_text(key)}::{cls.normalize_attribute_compare_text(value)}"
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            cleaned.append({"key": key, "value": value})

        return cleaned[:40]

    @staticmethod
    def normalize_attribute_text(value: Any) -> str:
        """统一属性文本格式，便于后续去重和导出。"""

        normalized = re.sub(r"\s+", " ", str(value or "")).strip()
        normalized = normalized.strip("：:|｜ ")
        return normalized

    @staticmethod
    def normalize_attribute_compare_text(value: str) -> str:
        """把等价的中英文分隔符归一，减少重复属性。"""

        normalized = value.strip().lower()
        normalized = re.sub(r"\s*[,，、;；|｜]\s*", ",", normalized)
        normalized = re.sub(r"\s*[:：]\s*", ":", normalized)
        return normalized

    @classmethod
    def is_noise_attribute(cls, key: str, value: str) -> bool:
        """判断属性是否属于占位值或页面表头噪声。"""

        placeholder_values = {"---", "--", "-", "/", "无", "暂无", "暂无数据", "不适用", "none", "n/a"}
        generic_headers = {"颜色", "规格", "尺码", "型号", "属性", "参数", "详情", "更多"}

        normalized_key = cls.normalize_attribute_compare_text(key)
        normalized_value = cls.normalize_attribute_compare_text(value)
        if normalized_key in placeholder_values or normalized_value in placeholder_values:
            return True
        if normalized_key == normalized_value:
            return True
        if normalized_key in generic_headers and normalized_value in generic_headers:
            return True
        return False

    @staticmethod
    def pick_price_text(price_candidates: list[str], body_text: str) -> str | None:
        """挑选最像商品价格的文本。"""

        patterns = [
            r"[¥￥]\s*\d+(?:\.\d+)?(?:\s*[-~]\s*[¥￥]?\s*\d+(?:\.\d+)?)?",
            r"\d+(?:\.\d+)?\s*[-~]\s*\d+(?:\.\d+)?\s*元",
            r"\d+(?:\.\d+)?\s*元",
        ]

        for candidate in price_candidates:
            for pattern in patterns:
                matched = re.search(pattern, candidate, re.IGNORECASE)
                if matched:
                    return matched.group(0).strip()

        for pattern in patterns:
            matched = re.search(pattern, body_text, re.IGNORECASE)
            if matched:
                return matched.group(0).strip()

        return None

    @staticmethod
    def pick_unit_price_text(price_candidates: list[str], body_text: str) -> str | None:
        """挑选最像单价的文本。"""

        patterns = [
            r"[¥￥]?\s*\d+(?:\.\d+)?\s*/\s*(件|个|袋|箱|只|套|双|包|公斤|千克|kg|g|克)",
            r"\d+(?:\.\d+)?\s*元\s*/\s*(件|个|袋|箱|只|套|双|包|公斤|千克|kg|g|克)",
        ]

        for candidate in price_candidates:
            for pattern in patterns:
                matched = re.search(pattern, candidate, re.IGNORECASE)
                if matched:
                    return matched.group(0).strip()

        for pattern in patterns:
            matched = re.search(pattern, body_text, re.IGNORECASE)
            if matched:
                return matched.group(0).strip()

        return None

    @staticmethod
    def pick_weight_text(body_text: str) -> str | None:
        """从详情页正文中挑选最像重量的文本。"""

        patterns = [
            r"(?:重量|毛重|净重|商品重量|产品重量|单件重量|包装重量)\s*[：: ]\s*(\d+(?:\.\d+)?\s*(?:kg|KG|Kg|公斤|千克|g|G|克))",
            r"(\d+(?:\.\d+)?\s*(?:kg|KG|Kg|公斤|千克|g|G|克))",
        ]

        for index, pattern in enumerate(patterns):
            matched = re.search(pattern, body_text, re.IGNORECASE)
            if matched:
                return matched.group(1 if index == 0 else 0).strip()
        return None

    @staticmethod
    def parse_price_value(value: str | None) -> float | None:
        """把价格文本转成数字。"""

        if not value:
            return None

        matched = re.search(r"(\d+(?:\.\d+)?)", value.replace(",", ""))
        if not matched:
            return None
        return float(matched.group(1))

    @staticmethod
    def parse_weight_grams(value: str | None) -> float | None:
        """把重量文本统一换算成克。"""

        if not value:
            return None

        matched = re.search(r"(\d+(?:\.\d+)?)\s*(kg|KG|Kg|公斤|千克|g|G|克)", value)
        if not matched:
            return None

        amount = float(matched.group(1))
        unit = matched.group(2).lower()
        if unit in {"kg", "公斤", "千克"}:
            return round(amount * 1000, 2)
        return round(amount, 2)
