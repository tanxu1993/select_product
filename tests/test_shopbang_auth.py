"""上品帮插件与登录态管理测试。"""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

import pytest

from config.settings import Settings
from ozon_selection.collectors.ozon.shopbang_auth import ShopbangExtensionManager, ShopbangLoginManager


def build_fake_crx3(zip_payload: bytes) -> bytes:
    """构造最小可解析的 CRX3 测试数据。"""

    header = b"test-header"
    return b"Cr24" + struct.pack("<I", 3) + struct.pack("<I", len(header)) + header + zip_payload


def build_zip_payload() -> bytes:
    """构造内存 ZIP 内容。"""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zip_file:
        zip_file.writestr("manifest.json", '{"name":"shopbang"}')
    return buffer.getvalue()


def test_extract_zip_payload_from_crx3() -> None:
    """确保能从 CRX3 中提取 ZIP 载荷。"""

    zip_payload = build_zip_payload()
    crx_payload = build_fake_crx3(zip_payload)
    extracted = ShopbangExtensionManager._extract_zip_payload(crx_payload)
    assert extracted == zip_payload


def test_unpack_extension_extracts_manifest(tmp_path: Path) -> None:
    """确保 CRX 解包后能产出 manifest。"""

    zip_payload = build_zip_payload()
    crx_payload = build_fake_crx3(zip_payload)
    crx_file = tmp_path / "test.crx"
    target_dir = tmp_path / "unpacked"
    crx_file.write_bytes(crx_payload)

    settings = Settings(
        SHOPBANG_EXTENSION_CRX_PATH=str(crx_file),
        SHOPBANG_EXTENSION_UNPACK_DIR=str(target_dir),
    )
    manager = ShopbangExtensionManager(settings)
    unpack_path = manager.unpack_extension()

    assert (unpack_path / "manifest.json").exists()


def test_unpack_extension_extracts_manifest_from_zip(tmp_path: Path) -> None:
    """确保本地 ZIP 插件包也能正常解包。"""

    zip_file = tmp_path / "plugin.zip"
    target_dir = tmp_path / "unpacked"
    with zipfile.ZipFile(zip_file, "w") as archive:
        archive.writestr("manifest.json", '{"name":"shopbang-zip"}')

    settings = Settings(
        SHOPBANG_EXTENSION_ZIP_PATH=str(zip_file),
        SHOPBANG_EXTENSION_UNPACK_DIR=str(target_dir),
    )
    manager = ShopbangExtensionManager(settings)
    unpack_path = manager.unpack_extension(zip_file)

    assert (unpack_path / "manifest.json").exists()


def test_download_and_unpack_prefers_local_zip(tmp_path: Path) -> None:
    """有本地 ZIP 时应优先使用本地 ZIP，而不是下载 CRX。"""

    zip_file = tmp_path / "offline.zip"
    target_dir = tmp_path / "unpacked"
    with zipfile.ZipFile(zip_file, "w") as archive:
        archive.writestr("manifest.json", '{"name":"offline"}')

    settings = Settings(
        SHOPBANG_EXTENSION_ZIP_PATH=str(zip_file),
        SHOPBANG_EXTENSION_UNPACK_DIR=str(target_dir),
    )
    manager = ShopbangExtensionManager(settings)
    unpack_path = manager.download_and_unpack()

    assert (unpack_path / "manifest.json").exists()


def test_validate_collection_prerequisites_requires_auth_file(tmp_path: Path) -> None:
    """确保没有登录态时会阻止采集。"""

    unpack_dir = tmp_path / "unpacked"
    unpack_dir.mkdir(parents=True, exist_ok=True)
    user_data_dir = tmp_path / "browser-profile"
    user_data_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        SHOPBANG_CDP_URL="",
        SHOPBANG_EXTENSION_UNPACK_DIR=str(unpack_dir),
        SHOPBANG_USER_DATA_DIR=str(user_data_dir),
        SHOPBANG_AUTH_STATE_FILE=str(tmp_path / "auth-state.json"),
    )
    manager = ShopbangLoginManager(settings)

    with pytest.raises(FileNotFoundError):
        manager.validate_collection_prerequisites()


def test_validate_collection_prerequisites_skips_local_assets_in_cdp_mode(tmp_path: Path) -> None:
    """CDP 模式下不应强依赖本地 profile 和 auth-state 文件。"""

    settings = Settings(
        SHOPBANG_CDP_URL="http://127.0.0.1:9222",
        SHOPBANG_EXTENSION_UNPACK_DIR=str(tmp_path / "missing-unpacked"),
        SHOPBANG_USER_DATA_DIR=str(tmp_path / "missing-profile"),
        SHOPBANG_AUTH_STATE_FILE=str(tmp_path / "missing-auth.json"),
    )
    manager = ShopbangLoginManager(settings)

    manager.validate_collection_prerequisites()


def test_should_use_cdp_reads_setting() -> None:
    """确保能正确识别 CDP 模式开关。"""

    manager = ShopbangLoginManager(Settings(SHOPBANG_CDP_URL="http://127.0.0.1:9222"))
    assert manager.should_use_cdp() is True


def test_has_login_credentials_property() -> None:
    """确保自动登录凭据判断正确。"""

    manager_without_credentials = ShopbangLoginManager(
        Settings(
            SHOPBANG_USERNAME="",
            SHOPBANG_PASSWORD="",
        )
    )
    assert manager_without_credentials.has_login_credentials is False

    manager_with_credentials = ShopbangLoginManager(
        Settings(
            SHOPBANG_USERNAME="demo_user",
            SHOPBANG_PASSWORD="demo_password",
        )
    )
    assert manager_with_credentials.has_login_credentials is True


def test_is_login_page_detects_login_route() -> None:
    """确保登录页识别逻辑可用。"""

    assert ShopbangLoginManager.is_login_page(
        "https://shopbang.cn/erp/#/login",
        "登录 没有账号？去注册",
    )
    assert not ShopbangLoginManager.is_login_page(
        "https://shopbang.cn/erp/#/index",
        "上品帮 控制台",
    )


class DummyContext:
    """用于测试 cookie 读取的最小上下文对象。"""

    def __init__(self, cookies: list[dict]) -> None:
        self._cookies = cookies

    def cookies(self, *_args, **_kwargs) -> list[dict]:
        return self._cookies


def test_get_token_cookie_value_reads_cookie() -> None:
    """确保能从浏览器上下文里读到 token cookie。"""

    manager = ShopbangLoginManager(Settings())
    context = DummyContext(
        [
            {"name": "imgAccData", "value": "true"},
            {"name": "token", "value": "abc123"},
        ]
    )
    assert manager.get_token_cookie_value(context) == "abc123"
    assert manager.has_token_cookie(context) is True


def test_get_token_cookie_value_returns_none_when_missing() -> None:
    """确保缺少 token 时返回 None。"""

    manager = ShopbangLoginManager(Settings())
    context = DummyContext([{"name": "imgAccData", "value": "true"}])
    assert manager.get_token_cookie_value(context) is None
    assert manager.has_token_cookie(context) is False
