"""手动准备 Ozon 列表页后，接管执行候选商品采集。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import BrowserContext, Page, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings
from ozon_selection.services.ozon_candidate_pipeline import OzonCandidatePipeline


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    settings = get_settings()
    parser = argparse.ArgumentParser(description="手动准备 Ozon 列表页后，接管执行候选商品采集。")
    parser.add_argument(
        "--start-url",
        type=str,
        default=settings.ozon_base_url,
        help="脚本打开的新标签页默认入口 URL。",
    )
    parser.add_argument(
        "--keyword",
        type=str,
        default="",
        help="可选：导出和入库时使用的关键词标签。不传则尝试从当前 URL 自动提取。",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="后台模式：使用无界面浏览器运行。该脚本通常需要人工操作页面，不建议开启。",
    )
    return parser.parse_args()


def derive_keyword_from_url(url: str) -> str:
    """尽量从 Ozon URL 中提取关键词。"""

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    text_values = query.get("text") or []
    if text_values:
        candidate = str(text_values[0]).strip()
        if candidate:
            return candidate

    path = parsed.path.strip("/").replace("/", "_")
    return path or "manual_page"


def inspect_page(page: Page) -> dict:
    """采样单个标签页状态，用于挑选人工准备好的目标页。"""

    try:
        url = page.url or ""
    except Exception:
        url = ""

    tile_count = 0
    product_link_count = 0
    try:
        tile_count = page.locator(".tile-root[data-index]").count()
    except Exception:
        tile_count = 0
    try:
        product_link_count = page.locator('a[href*="/product/"]').count()
    except Exception:
        product_link_count = 0

    is_ozon = "ozon." in url
    has_search_hint = any(marker in url for marker in ("/search/", "text=", "from_global=", "sorting=", "page="))

    score = 0
    if is_ozon:
        score += 100
    if has_search_hint:
        score += 50
    score += min(tile_count, 200)
    score += min(product_link_count, 100)

    return {
        "page": page,
        "url": url,
        "tile_count": tile_count,
        "product_link_count": product_link_count,
        "has_search_hint": has_search_hint,
        "is_ozon": is_ozon,
        "score": score,
    }


def select_target_page(context: BrowserContext, fallback_page: Page) -> tuple[Page, list[dict]]:
    """从当前上下文所有标签页中挑选最像目标列表页的那个。"""

    candidates: list[dict] = []
    for page in context.pages:
        candidates.append(inspect_page(page))

    if not candidates:
        return fallback_page, []

    ozon_candidates = [item for item in candidates if item["is_ozon"]]
    if not ozon_candidates:
        return fallback_page, candidates

    selected = max(
        ozon_candidates,
        key=lambda item: (
            item["score"],
            item["tile_count"],
            item["product_link_count"],
            item["has_search_hint"],
        ),
    )
    return selected["page"], candidates


def print_result(result: dict, sqlite_db_path: str) -> None:
    """打印采集结果。"""

    print("Ozon manual page collection: completed")
    print(f"keyword: {result['keyword']}")
    print(f"search_url: {result['search_url']}")
    print(f"total_collected: {result['total_collected']}")
    print(f"qualified_count: {result['qualified_count']}")
    print(f"rejected_count: {result['rejected_count']}")
    print(f"image_dir: {result['image_dir']}")
    print(f"excel_path: {result['excel_path']}")
    print(f"sqlite_status: {result['sqlite_result']['status']}")
    print(f"sqlite_db_path: {sqlite_db_path}")
    if result["sqlite_result"].get("batch_id") is not None:
        print(f"sqlite_batch_id: {result['sqlite_result']['batch_id']}")
    if result["sqlite_result"].get("source_ref"):
        print(f"sqlite_source_ref: {result['sqlite_result']['source_ref']}")
    if result["sqlite_result"].get("reason"):
        print(f"sqlite_note: {result['sqlite_result']['reason']}")
    if result["sqlite_result"].get("error"):
        print(f"sqlite_error: {result['sqlite_result']['error']}")
    print(f"database_status: {result['database_result']['status']}")
    if result["database_result"].get("reason"):
        print(f"database_note: {result['database_result']['reason']}")
    if result["database_result"].get("missing_fields"):
        print(f"database_missing_fields: {','.join(result['database_result']['missing_fields'])}")
    if result["database_result"].get("placeholder_fields"):
        print(f"database_placeholder_fields: {','.join(result['database_result']['placeholder_fields'])}")
    if result["database_result"].get("error"):
        print(f"database_error: {result['database_result']['error']}")


def main() -> None:
    """打开浏览器，等待人工准备页面后接管采集。"""

    args = parse_args()
    settings = get_settings().model_copy(
        deep=True,
        update={
            "shopbang_headless": bool(args.background),
        },
    )

    pipeline = OzonCandidatePipeline(settings=settings)
    collector = pipeline.collector

    with sync_playwright() as playwright:
        collector.login_manager.validate_collection_prerequisites()
        session = collector.login_manager.open_browser_session(playwright=playwright)
        context = session.context
        created_page = context.new_page()

        try:
            created_page.goto(
                args.start_url,
                wait_until="domcontentloaded",
                timeout=settings.playwright_timeout_ms,
            )
            print(f"manual_start_url: {args.start_url}")
            print("请在当前浏览器页手动完成以下操作：", flush=True)
            print("1. 输入目标 Ozon 列表页 URL 并回车", flush=True)
            print("2. 手动把价格设置为 500-8000 并确认", flush=True)
            print("3. 确认页面已加载出商品列表后，回到终端按 Enter 开始采集", flush=True)
            input("页面准备完成后按 Enter 继续...")

            page, page_candidates = select_target_page(context, created_page)
            print("已检测当前浏览器标签页：", flush=True)
            for index, item in enumerate(page_candidates, start=1):
                print(
                    f"  [{index}] score={item['score']} tiles={item['tile_count']} "
                    f"product_links={item['product_link_count']} url={item['url']}",
                    flush=True,
                )
            print(f"选中采集页: {page.url}", flush=True)

            current_url = page.url
            if "ozon." not in current_url:
                raise RuntimeError(f"当前页不是 Ozon 页面，无法采集：{current_url}")

            keyword = args.keyword.strip() or derive_keyword_from_url(current_url)
            keyword = re.sub(r"\s+", " ", keyword).strip() or "manual_page"
            products = collector.scrape_products_from_current_page(context=context, page=page)
            result = pipeline.finalize_collected_products(
                keyword=keyword,
                search_url=current_url,
                products=products,
                detail_context=context,
            )
            print_result(result, str(settings.sqlite_db_path))
        finally:
            try:
                if not created_page.is_closed() and created_page is not page:
                    created_page.close()
            except Exception:
                pass
            session.close()


if __name__ == "__main__":
    main()
