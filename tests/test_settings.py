"""配置模块测试。"""

from config.settings import Settings
from config.settings import get_settings


def test_settings_has_app_name() -> None:
    """确保配置对象能够正常创建。"""

    settings = get_settings()
    assert settings.app_name


def test_ozon_start_url_list_is_parsed() -> None:
    """确保逗号分隔的入口 URL 能被正确解析。"""

    settings = get_settings()
    assert isinstance(settings.ozon_start_url_list, list)


def test_shopbang_login_urls_exist() -> None:
    """确保上品帮自动登录相关配置可读取。"""

    settings = get_settings()
    assert settings.shopbang_erp_url
    assert settings.shopbang_erp_login_url
    assert settings.shopbang_remai_url
    assert settings.shopbang_auth_check_url


def test_ozon_scrape_paths_exist() -> None:
    """确保 Ozon 抓取配置路径属性可读取。"""

    settings = get_settings()
    assert settings.ozon_scrape_keyword
    assert settings.ozon_scrape_output_path
    assert settings.ozon_scrape_image_path
    assert settings.ozon_keyword_timeout_seconds > 0


def test_ozon_scrape_keyword_list_is_parsed() -> None:
    """确保关键词配置可解析为列表。"""

    settings = Settings(OZON_SCRAPE_KEYWORD="关键词A,关键词B\n关键词C；关键词D")

    assert settings.ozon_scrape_keyword_list == ["关键词A", "关键词B", "关键词C", "关键词D"]


def test_playwright_executable_settings_are_readable() -> None:
    """确保可读取本地 Chrome 启动配置。"""

    settings = Settings(
        PLAYWRIGHT_CHANNEL="chrome",
        PLAYWRIGHT_EXECUTABLE_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )

    assert settings.playwright_channel == "chrome"
    assert settings.playwright_executable_path.endswith("Google Chrome")


def test_shopbang_cdp_setting_is_readable() -> None:
    """确保可读取上品帮 CDP 连接地址。"""

    settings = Settings(SHOPBANG_CDP_URL="http://127.0.0.1:9222")

    assert settings.shopbang_cdp_url == "http://127.0.0.1:9222"


def test_shopbang_cdp_launch_settings_are_readable() -> None:
    """确保可读取隔离 Chrome 启动配置。"""

    settings = Settings(
        SHOPBANG_CDP_PORT=9333,
        SHOPBANG_CDP_BROWSER_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        SHOPBANG_CDP_USER_DATA_DIR="browser-profile-cdp-test",
    )

    assert settings.shopbang_cdp_port == 9333
    assert settings.shopbang_cdp_browser_path.endswith("Google Chrome")
    assert settings.shopbang_cdp_user_data_path.name == "browser-profile-cdp-test"


def test_alibaba1688_cdp_setting_falls_back_to_shopbang_cdp() -> None:
    """1688 未单独配置 CDP 时，应可复用上品帮 CDP 地址。"""

    settings = Settings(SHOPBANG_CDP_URL="http://127.0.0.1:9222")

    assert settings.alibaba1688_cdp_url == ""
    assert settings.shopbang_cdp_url == "http://127.0.0.1:9222"
