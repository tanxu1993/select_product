"""打开 1688 搜索首页，完成以图搜图上传并确认。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings
from ozon_selection.collectors.alibaba.image_search import Alibaba1688ImageSearchBrowser


SEARCH_HOME_URL = "https://s.1688.com/"
DEFAULT_CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="打开 1688 首页并执行一次以图搜图上传。")
    parser.add_argument(
        "--image",
        type=str,
        default="",
        help="待上传图片路径。默认优先使用当前目录下的 ./1688/1.jpg。",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="提交后保持浏览器打开，按回车再关闭。",
    )
    return parser.parse_args()


def resolve_image_path(raw_image_path: str) -> Path:
    """解析待上传图片路径。"""

    if raw_image_path.strip():
        image_path = Path(raw_image_path).expanduser()
        if not image_path.is_absolute():
            image_path = Path.cwd() / image_path
        image_path = image_path.resolve()
        if not image_path.exists():
            raise FileNotFoundError(f"未找到待上传图片: {image_path}")
        return image_path

    preferred_path = (Path.cwd() / "1688" / "1.jpg").resolve()
    if preferred_path.exists():
        return preferred_path

    direct_images = sorted(
        path.resolve()
        for path in Path.cwd().iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if len(direct_images) == 1:
        return direct_images[0]

    raise FileNotFoundError(
        "未自动找到唯一图片。请通过 --image 指定；默认只会优先选择当前目录下的 ./1688/1.jpg。"
    )


def main() -> None:
    """打开 1688 搜索首页，上传本地图片并点击确认。"""

    args = parse_args()
    image_path = resolve_image_path(args.image)
    base_settings = get_settings()
    settings = base_settings.model_copy(
        deep=True,
        update={
            "alibaba1688_cdp_url": "",
            "shopbang_cdp_url": "",
            "alibaba1688_headless": False,
            "playwright_channel": base_settings.playwright_channel or "chrome",
            "playwright_executable_path": base_settings.playwright_executable_path or DEFAULT_CHROME_PATH,
            "playwright_slow_mo_ms": max(base_settings.playwright_slow_mo_ms, 150),
        },
    )
    browser = Alibaba1688ImageSearchBrowser(settings=settings)

    with sync_playwright() as playwright:
        session = browser.open_browser_session(
            playwright,
            headless=False,
            user_data_dir=browser.prepare_profile_copy(),
        )
        context = session.context
        page = context.new_page()

        try:
            print(f"[1688] opening search home: {SEARCH_HOME_URL}", flush=True)
            page.goto(
                SEARCH_HOME_URL,
                wait_until="domcontentloaded",
                timeout=settings.playwright_timeout_ms,
            )
            page.wait_for_timeout(1_500)
            auth_state_path = browser.ensure_logged_in(context, page)
            print(f"[1688] auth state saved: {auth_state_path}", flush=True)

            upload_input = browser.find_upload_input(page)
            print(f"[1688] uploading local image: {image_path}", flush=True)
            upload_input.set_input_files(str(image_path))
            browser.click_search_image_button(page)
            browser.wait_for_search_results(page)

            print(f"[1688] submitted image search successfully: {page.url}", flush=True)
            if args.keep_open:
                input("浏览器保持打开中，按回车关闭脚本。")
        except Exception:
            browser.print_debug_state(page)
            raise
        finally:
            page.close()
            session.close()


if __name__ == "__main__":
    main()
