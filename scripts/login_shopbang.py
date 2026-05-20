"""登录上品帮并保存浏览器会话。"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ozon_selection.collectors.ozon.shopbang_auth import ShopbangLoginManager


def main() -> None:
    """打开浏览器供人工登录，并保存 storage state。"""

    manager = ShopbangLoginManager()
    with sync_playwright() as playwright:
        auth_state_path = manager.login_and_save(playwright)
        print(f"上品帮登录态已保存到: {auth_state_path}")


if __name__ == "__main__":
    main()
