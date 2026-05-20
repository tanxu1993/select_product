"""项目健康检查脚本。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings


def main() -> None:
    """打印核心配置，便于快速检查环境是否可用。"""

    settings = get_settings()
    print("Application:", settings.app_name)
    print("Environment:", settings.app_env)
    print("Timezone:", settings.timezone)
    print("Anthropic configured:", bool(settings.anthropic_api_key))
    print("1688 API configured:", bool(settings.api1688_app_key and settings.api1688_app_secret))
    print("Supabase configured:", bool(settings.supabase_url and settings.supabase_service_role_key))
    print("Shopbang extension unpacked:", settings.shopbang_extension_unpack_path.exists())
    print("Shopbang credentials configured:", bool(settings.shopbang_username and settings.shopbang_password))
    print("Shopbang auth state exists:", settings.shopbang_auth_state_path.exists())
    print("Ozon scrape keyword:", settings.ozon_scrape_keyword)
    print("Ozon scrape keyword list:", settings.ozon_scrape_keyword_list)
    print("Ozon scrape target:", settings.ozon_scrape_target_products)


if __name__ == "__main__":
    main()
