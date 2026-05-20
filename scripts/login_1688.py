"""登录 1688 并保存浏览器会话。"""

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

from ozon_selection.collectors.alibaba.image_search import Alibaba1688ImageSearchBrowser


def main() -> None:
    """打开 1688 图搜图页面并保存登录态。"""

    browser = Alibaba1688ImageSearchBrowser()
    with sync_playwright() as playwright:
        auth_state_path = browser.login_and_save(playwright)
    print(f"1688 登录态已保存到: {auth_state_path}")


if __name__ == "__main__":
    main()
