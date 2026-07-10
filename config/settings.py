"""项目统一配置入口。

该文件的职责只有一件事：从环境变量读取配置，并把配置转换成
业务代码可直接使用的强类型对象。
"""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import re
import shutil
import sys

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置对象。

    所有配置都集中定义在这里，方便：
    1. 统一维护环境变量名
    2. 为每个配置提供默认值
    3. 在启动阶段尽早暴露缺失配置
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # =========================
    # 基础运行配置
    # =========================
    app_name: str = Field(default="ozon-ai-selection-system", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    debug: bool = Field(default=True, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    timezone: str = Field(default="Asia/Shanghai", alias="TIMEZONE")

    # =========================
    # 本地路径配置
    # =========================
    data_dir: str = Field(default="data", alias="DATA_DIR")
    raw_data_dir: str = Field(default="data/raw", alias="RAW_DATA_DIR")
    processed_data_dir: str = Field(default="data/processed", alias="PROCESSED_DATA_DIR")
    export_dir: str = Field(default="data/exports", alias="EXPORT_DIR")
    log_dir: str = Field(default="logs", alias="LOG_DIR")

    # =========================
    # Anthropic Claude API
    # =========================
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = Field(default="https://api.anthropic.com", alias="ANTHROPIC_BASE_URL")
    anthropic_model: str = Field(default="claude-sonnet-4-20250514", alias="ANTHROPIC_MODEL")
    anthropic_timeout_seconds: int = Field(default=60, alias="ANTHROPIC_TIMEOUT_SECONDS")

    # =========================
    # OpenAI / GPT-5.4 商品解析
    # =========================
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_product_parse_model: str = Field(default="gpt-5.4", alias="OPENAI_PRODUCT_PARSE_MODEL")
    openai_timeout_seconds: int = Field(default=60, alias="OPENAI_TIMEOUT_SECONDS")
    product_parser_json_retry_count: int = Field(default=2, alias="PRODUCT_PARSER_JSON_RETRY_COUNT")
    product_parser_image_download_timeout_seconds: int = Field(
        default=20,
        alias="PRODUCT_PARSER_IMAGE_DOWNLOAD_TIMEOUT_SECONDS",
    )
    product_parser_image_referer: str = Field(default="https://www.ozon.ru/", alias="PRODUCT_PARSER_IMAGE_REFERER")
    product_parser_image_user_agent: str = Field(default="", alias="PRODUCT_PARSER_IMAGE_USER_AGENT")
    product_parser_image_accept_language: str = Field(
        default="ru-RU,ru;q=0.9,en;q=0.8,zh-CN;q=0.7",
        alias="PRODUCT_PARSER_IMAGE_ACCEPT_LANGUAGE",
    )
    product_parser_test_output_dir: str = Field(
        default="data/exports/product_parser",
        alias="PRODUCT_PARSER_TEST_OUTPUT_DIR",
    )
    product_parser_test_image_dir: str = Field(
        default="data/raw/product_parser_test_images",
        alias="PRODUCT_PARSER_TEST_IMAGE_DIR",
    )

    # =========================
    # 1688 第三方 API
    # =========================
    api1688_base_url: str = Field(default="", alias="API1688_BASE_URL")
    api1688_app_key: str = Field(default="", alias="API1688_APP_KEY")
    api1688_app_secret: str = Field(default="", alias="API1688_APP_SECRET")
    api1688_timeout_seconds: int = Field(default=60, alias="API1688_TIMEOUT_SECONDS")
    api1688_auth_mode: str = Field(default="query", alias="API1688_AUTH_MODE")
    api1688_image_search_path: str = Field(default="/image_search", alias="API1688_IMAGE_SEARCH_PATH")
    api1688_image_search_method: str = Field(default="POST", alias="API1688_IMAGE_SEARCH_METHOD")
    api1688_image_search_payload_mode: str = Field(
        default="image_url",
        alias="API1688_IMAGE_SEARCH_PAYLOAD_MODE",
    )
    api1688_image_search_image_url_field: str = Field(
        default="image_url",
        alias="API1688_IMAGE_SEARCH_IMAGE_URL_FIELD",
    )
    api1688_image_search_image_base64_field: str = Field(
        default="image_base64",
        alias="API1688_IMAGE_SEARCH_IMAGE_BASE64_FIELD",
    )
    api1688_image_search_page_field: str = Field(default="page", alias="API1688_IMAGE_SEARCH_PAGE_FIELD")
    api1688_image_search_page_size_field: str = Field(
        default="page_size",
        alias="API1688_IMAGE_SEARCH_PAGE_SIZE_FIELD",
    )
    api1688_image_search_default_page_size: int = Field(
        default=20,
        alias="API1688_IMAGE_SEARCH_DEFAULT_PAGE_SIZE",
    )
    api1688_image_search_results_path: str = Field(
        default="data.items",
        alias="API1688_IMAGE_SEARCH_RESULTS_PATH",
    )

    # =========================
    # 1688 浏览器图搜图配置
    # =========================
    alibaba1688_base_url: str = Field(default="https://www.1688.com", alias="ALIBABA1688_BASE_URL")
    alibaba1688_login_url: str = Field(default="https://login.1688.com/", alias="ALIBABA1688_LOGIN_URL")
    alibaba1688_image_search_url: str = Field(
        default="https://s.1688.com/youyuan/index.htm",
        alias="ALIBABA1688_IMAGE_SEARCH_URL",
    )
    alibaba1688_image_search_tab: str = Field(default="imageSearch", alias="ALIBABA1688_IMAGE_SEARCH_TAB")
    alibaba1688_bitbrowser_api_url: str = Field(
        default="",
        alias="ALIBABA1688_BITBROWSER_API_URL",
    )
    alibaba1688_bitbrowser_browser_id: str = Field(
        default="",
        alias="ALIBABA1688_BITBROWSER_BROWSER_ID",
    )
    alibaba1688_adspower_api_url: str = Field(
        default="",
        alias="ALIBABA1688_ADSPOWER_API_URL",
    )
    alibaba1688_adspower_api_key: str = Field(
        default="",
        alias="ALIBABA1688_ADSPOWER_API_KEY",
    )
    alibaba1688_adspower_profile_id: str = Field(
        default="",
        alias="ALIBABA1688_ADSPOWER_PROFILE_ID",
    )
    alibaba1688_cdp_url: str = Field(default="", alias="ALIBABA1688_CDP_URL")
    alibaba1688_user_data_dir: str = Field(default="browser-profile-1688", alias="ALIBABA1688_USER_DATA_DIR")
    alibaba1688_auth_state_file: str = Field(default="auth-state-1688.json", alias="ALIBABA1688_AUTH_STATE_FILE")
    alibaba1688_wait_after_login_ms: int = Field(default=3000, alias="ALIBABA1688_WAIT_AFTER_LOGIN_MS")
    alibaba1688_login_timeout_seconds: int = Field(default=180, alias="ALIBABA1688_LOGIN_TIMEOUT_SECONDS")
    alibaba1688_result_wait_ms: int = Field(default=5000, alias="ALIBABA1688_RESULT_WAIT_MS")
    alibaba1688_max_results: int = Field(default=20, alias="ALIBABA1688_MAX_RESULTS")
    alibaba1688_open_retry_count: int = Field(default=3, alias="ALIBABA1688_OPEN_RETRY_COUNT")
    alibaba1688_retry_wait_ms: int = Field(default=3000, alias="ALIBABA1688_RETRY_WAIT_MS")
    alibaba1688_image_compare_pass_score: int = Field(default=60, alias="ALIBABA1688_IMAGE_COMPARE_PASS_SCORE")
    alibaba1688_detail_rate_limit_count: int = Field(
        default=6,
        alias="ALIBABA1688_DETAIL_RATE_LIMIT_COUNT",
    )
    alibaba1688_detail_rate_limit_window_seconds: int = Field(
        default=60,
        alias="ALIBABA1688_DETAIL_RATE_LIMIT_WINDOW_SECONDS",
    )

    # =========================
    # SQLite 配置
    # =========================
    sqlite_path: str = Field(default="data/processed/ozon_selection.db", alias="SQLITE_PATH")

    # =========================
    # Supabase 配置
    # =========================
    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    supabase_anon_key: str = Field(default="", alias="SUPABASE_ANON_KEY")
    supabase_service_role_key: str = Field(default="", alias="SUPABASE_SERVICE_ROLE_KEY")
    supabase_schema: str = Field(default="public", alias="SUPABASE_SCHEMA")

    # =========================
    # Ozon / Playwright 采集配置
    # =========================
    ozon_base_url: str = Field(default="https://www.ozon.ru", alias="OZON_BASE_URL")
    ozon_start_urls: str = Field(default="https://www.ozon.ru/", alias="OZON_START_URLS")
    ozon_market: str = Field(default="RU", alias="OZON_MARKET")
    ozon_language: str = Field(default="ru", alias="OZON_LANGUAGE")
    ozon_user_agent: str = Field(default="", alias="OZON_USER_AGENT")
    playwright_browser: str = Field(default="chromium", alias="PLAYWRIGHT_BROWSER")
    playwright_channel: str = Field(default="", alias="PLAYWRIGHT_CHANNEL")
    playwright_executable_path: str = Field(default="", alias="PLAYWRIGHT_EXECUTABLE_PATH")
    playwright_headless: bool = Field(default=True, alias="PLAYWRIGHT_HEADLESS")
    playwright_timeout_ms: int = Field(default=45_000, alias="PLAYWRIGHT_TIMEOUT_MS")
    playwright_slow_mo_ms: int = Field(default=0, alias="PLAYWRIGHT_SLOW_MO_MS")
    playwright_proxy_url: str = Field(default="", alias="PLAYWRIGHT_PROXY_URL")
    ozon_browser_locale: str = Field(default="ru-RU", alias="OZON_BROWSER_LOCALE")
    ozon_browser_timezone: str = Field(default="Europe/Moscow", alias="OZON_BROWSER_TIMEZONE")
    shopbang_headless: bool = Field(default=False, alias="SHOPBANG_HEADLESS")
    alibaba1688_headless: bool = Field(default=True, alias="ALIBABA1688_HEADLESS")

    # =========================
    # Ozon 选品抓取配置
    # =========================
    ozon_scrape_keyword: str = Field(default="Виброхвост", alias="OZON_SCRAPE_KEYWORD")
    ozon_scrape_target_products: int = Field(default=2000, alias="OZON_SCRAPE_TARGET_PRODUCTS")
    ozon_scrape_sorting: str = Field(default="rating", alias="OZON_SCRAPE_SORTING")
    ozon_scrape_from_global: bool = Field(default=True, alias="OZON_SCRAPE_FROM_GLOBAL")
    ozon_scrape_scroll_step_px: int = Field(default=800, alias="OZON_SCRAPE_SCROLL_STEP_PX")
    ozon_scrape_scroll_pause_ms: int = Field(default=800, alias="OZON_SCRAPE_SCROLL_PAUSE_MS")
    ozon_scrape_stale_limit: int = Field(default=6, alias="OZON_SCRAPE_STALE_LIMIT")
    ozon_scrape_plugin_wait_ms: int = Field(default=4_000, alias="OZON_SCRAPE_PLUGIN_WAIT_MS")
    ozon_scrape_image_wait_ms: int = Field(default=1_000, alias="OZON_SCRAPE_IMAGE_WAIT_MS")
    ozon_scrape_download_images: bool = Field(default=True, alias="OZON_SCRAPE_DOWNLOAD_IMAGES")
    ozon_detail_concurrency: int = Field(default=3, alias="OZON_DETAIL_CONCURRENCY")
    ozon_scrape_output_dir: str = Field(default="data/exports", alias="OZON_SCRAPE_OUTPUT_DIR")
    ozon_scrape_image_dir: str = Field(default="data/raw/product_images", alias="OZON_SCRAPE_IMAGE_DIR")
    ozon_keyword_timeout_seconds: int = Field(
        default=600,
        alias="OZON_KEYWORD_TIMEOUT_SECONDS",
    )

    # =========================
    # 上品帮插件 / 登录态配置
    # =========================
    shopbang_extension_id: str = Field(default="ffnehecempjlbkejkmmdeenbodnafjdj", alias="SHOPBANG_EXTENSION_ID")
    shopbang_chrome_version: str = Field(default="120.0.0.0", alias="SHOPBANG_CHROME_VERSION")
    shopbang_extension_dir: str = Field(default="extensions", alias="SHOPBANG_EXTENSION_DIR")
    shopbang_extension_crx_path: str = Field(
        default="extensions/ffnehecempjlbkejkmmdeenbodnafjdj.crx",
        alias="SHOPBANG_EXTENSION_CRX_PATH",
    )
    shopbang_extension_zip_path: str = Field(default="", alias="SHOPBANG_EXTENSION_ZIP_PATH")
    shopbang_extension_unpack_dir: str = Field(default="extensions/unpacked", alias="SHOPBANG_EXTENSION_UNPACK_DIR")
    shopbang_login_url: str = Field(default="https://shopbang.cn/", alias="SHOPBANG_LOGIN_URL")
    shopbang_erp_url: str = Field(default="https://shopbang.cn/erp/#/index", alias="SHOPBANG_ERP_URL")
    shopbang_erp_login_url: str = Field(default="https://shopbang.cn/erp/#/login", alias="SHOPBANG_ERP_LOGIN_URL")
    shopbang_remai_url: str = Field(default="https://shopbang.cn/erp/#/remai", alias="SHOPBANG_REMAI_URL")
    shopbang_history_url: str = Field(default="https://shopbang.cn/erp/#/history", alias="SHOPBANG_HISTORY_URL")
    shopbang_cdp_url: str = Field(default="", alias="SHOPBANG_CDP_URL")
    shopbang_cdp_port: int = Field(default=9222, alias="SHOPBANG_CDP_PORT")
    shopbang_cdp_browser_path: str = Field(default="", alias="SHOPBANG_CDP_BROWSER_PATH")
    shopbang_cdp_user_data_dir: str = Field(default="browser-profile-cdp", alias="SHOPBANG_CDP_USER_DATA_DIR")
    shopbang_auth_check_url: str = Field(
        default="https://plus.shopbang.cn/api/order/ozon/getLocalNewOrder",
        alias="SHOPBANG_AUTH_CHECK_URL",
    )
    shopbang_username: str = Field(default="", alias="SHOPBANG_USERNAME")
    shopbang_password: str = Field(default="", alias="SHOPBANG_PASSWORD")
    shopbang_user_data_dir: str = Field(default="browser-profile", alias="SHOPBANG_USER_DATA_DIR")
    shopbang_auth_state_file: str = Field(default="auth-state.json", alias="SHOPBANG_AUTH_STATE_FILE")
    shopbang_download_timeout_seconds: int = Field(default=120, alias="SHOPBANG_DOWNLOAD_TIMEOUT_SECONDS")
    shopbang_login_timeout_seconds: int = Field(default=30, alias="SHOPBANG_LOGIN_TIMEOUT_SECONDS")

    # =========================
    # Streamlit 审核面板
    # =========================
    streamlit_host: str = Field(default="0.0.0.0", alias="STREAMLIT_HOST")
    streamlit_port: int = Field(default=8501, alias="STREAMLIT_PORT")
    streamlit_username: str = Field(default="admin", alias="STREAMLIT_USERNAME")
    streamlit_password: str = Field(default="change_me", alias="STREAMLIT_PASSWORD")

    # =========================
    # APScheduler 调度配置
    # =========================
    scheduler_timezone: str = Field(default="Asia/Shanghai", alias="SCHEDULER_TIMEZONE")
    collect_ozon_cron: str = Field(default="*/30 * * * *", alias="COLLECT_OZON_CRON")
    sync_1688_cron: str = Field(default="0 */2 * * *", alias="SYNC_1688_CRON")
    run_ai_analysis_cron: str = Field(default="15 */2 * * *", alias="RUN_AI_ANALYSIS_CRON")
    push_review_queue_cron: str = Field(default="30 */2 * * *", alias="PUSH_REVIEW_QUEUE_CRON")

    # =========================
    # 业务规则配置
    # =========================
    default_exchange_rate_cny_to_rub: float = Field(default=12.80, alias="DEFAULT_EXCHANGE_RATE_CNY_TO_RUB")
    min_expected_margin: float = Field(default=0.25, alias="MIN_EXPECTED_MARGIN")
    max_1688_purchase_price_cny: float = Field(default=300.0, alias="MAX_1688_PURCHASE_PRICE_CNY")
    min_ozon_monthly_orders: int = Field(default=30, alias="MIN_OZON_MONTHLY_ORDERS")

    @property
    def project_root(self) -> Path:
        """返回项目根目录。"""

        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent.parent

    def resolve_local_path(self, raw_path: str) -> Path:
        """把配置中的本地路径解析为绝对路径。

        支持两种写法：
        1. 相对项目根目录的相对路径
        2. 操作系统原生绝对路径
        """

        normalized = str(raw_path or "").strip()
        if not normalized:
            return self.project_root

        candidate = Path(normalized).expanduser()
        if candidate.is_absolute():
            return candidate
        return self.project_root / candidate

    @staticmethod
    def detect_default_chrome_path() -> str:
        """按当前操作系统探测本机 Chrome/Chromium 可执行文件。"""

        command_candidates = (
            "google-chrome",
            "chrome",
            "chromium",
            "chromium-browser",
            "msedge",
        )
        for command in command_candidates:
            executable = shutil.which(command)
            if executable:
                return executable

        if sys.platform.startswith("win"):
            env_candidates = [
                os.environ.get("LOCALAPPDATA", ""),
                os.environ.get("PROGRAMFILES", ""),
                os.environ.get("PROGRAMFILES(X86)", ""),
            ]
            suffixes = [
                Path("Google/Chrome/Application/chrome.exe"),
                Path("Chromium/Application/chrome.exe"),
                Path("Microsoft/Edge/Application/msedge.exe"),
            ]
            for base_dir in env_candidates:
                if not base_dir:
                    continue
                for suffix in suffixes:
                    candidate = Path(base_dir) / suffix
                    if candidate.exists():
                        return str(candidate)
            return ""

        macos_candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
        for candidate in macos_candidates:
            if Path(candidate).exists():
                return candidate
        return ""

    @property
    def raw_data_path(self) -> Path:
        """返回原始采集数据目录。"""

        return self.resolve_local_path(self.raw_data_dir)

    @property
    def processed_data_path(self) -> Path:
        """返回处理后数据目录。"""

        return self.resolve_local_path(self.processed_data_dir)

    @property
    def export_path(self) -> Path:
        """返回导出文件目录。"""

        return self.resolve_local_path(self.export_dir)

    @property
    def log_path(self) -> Path:
        """返回日志目录。"""

        return self.resolve_local_path(self.log_dir)

    @property
    def shopbang_extension_root(self) -> Path:
        """返回上品帮插件目录。"""

        return self.resolve_local_path(self.shopbang_extension_dir)

    @property
    def shopbang_extension_crx_file(self) -> Path:
        """返回上品帮插件压缩包路径。"""

        return self.resolve_local_path(self.shopbang_extension_crx_path)

    @property
    def shopbang_extension_zip_file(self) -> Path | None:
        """返回显式配置的本地 ZIP 路径。"""

        if not self.shopbang_extension_zip_path.strip():
            return None
        return self.resolve_local_path(self.shopbang_extension_zip_path)

    @property
    def shopbang_extension_unpack_path(self) -> Path:
        """返回上品帮插件解包目录。"""

        return self.resolve_local_path(self.shopbang_extension_unpack_dir)

    @property
    def shopbang_user_data_path(self) -> Path:
        """返回浏览器持久化 profile 目录。"""

        return self.resolve_local_path(self.shopbang_user_data_dir)

    @property
    def shopbang_auth_state_path(self) -> Path:
        """返回 storage state 文件路径。"""

        return self.resolve_local_path(self.shopbang_auth_state_file)

    @property
    def shopbang_cdp_user_data_path(self) -> Path:
        """返回隔离的 CDP Chrome 用户目录。"""

        return self.resolve_local_path(self.shopbang_cdp_user_data_dir)

    @property
    def shopbang_cdp_browser_executable_path(self) -> Path | None:
        """返回上品帮 CDP Chrome 可执行文件路径。"""

        configured = str(self.shopbang_cdp_browser_path or "").strip()
        resolved = configured or self.detect_default_chrome_path()
        if not resolved:
            return None
        return Path(resolved).expanduser()

    @property
    def alibaba1688_user_data_path(self) -> Path:
        """返回 1688 浏览器持久化 profile 目录。"""

        return self.resolve_local_path(self.alibaba1688_user_data_dir)

    @property
    def alibaba1688_auth_state_path(self) -> Path:
        """返回 1688 storage state 文件路径。"""

        return self.resolve_local_path(self.alibaba1688_auth_state_file)

    @property
    def ozon_start_url_list(self) -> list[str]:
        """把逗号分隔的入口 URL 转成列表。"""

        return [item.strip() for item in self.ozon_start_urls.split(",") if item.strip()]

    @property
    def ozon_scrape_keyword_list(self) -> list[str]:
        """把关键词配置解析成列表，支持逗号、分号和换行分隔。"""

        raw_value = self.ozon_scrape_keyword or ""
        keywords = [item.strip() for item in re.split(r"[\n,;，；]+", raw_value) if item.strip()]
        return keywords

    @property
    def ozon_scrape_output_path(self) -> Path:
        """返回 Ozon 选品结果导出目录。"""

        return self.resolve_local_path(self.ozon_scrape_output_dir)

    @property
    def ozon_scrape_image_path(self) -> Path:
        """返回 Ozon 商品图片保存目录。"""

        return self.resolve_local_path(self.ozon_scrape_image_dir)

    @property
    def product_parser_test_output_path(self) -> Path:
        """返回商品解析测试结果目录。"""

        return self.resolve_local_path(self.product_parser_test_output_dir)

    @property
    def product_parser_test_image_path(self) -> Path:
        """返回商品解析测试图片目录。"""

        return self.resolve_local_path(self.product_parser_test_image_dir)

    @property
    def sqlite_db_path(self) -> Path:
        """返回 SQLite 数据库文件路径。"""

        return self.resolve_local_path(self.sqlite_path)

    @property
    def playwright_executable_file(self) -> Path | None:
        """返回 Playwright 浏览器 executable 路径。"""

        configured = str(self.playwright_executable_path or "").strip()
        if configured:
            return Path(configured).expanduser()

        detected = self.detect_default_chrome_path()
        if not detected:
            return None
        return Path(detected).expanduser()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回单例配置对象。

    通过缓存避免在一个进程里多次重复读取 `.env` 文件。
    """

    return Settings()
