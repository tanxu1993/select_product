"""从 reviewed seller 店铺列表批量采集 Ozon 候选商品。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Page, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings
from ozon_selection.repositories.ozon_reviewed_seller_repository import OzonReviewedSellerRepository
from ozon_selection.services.ozon_candidate_pipeline import OzonCandidatePipeline


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="从 reviewed seller 店铺列表批量采集 Ozon 候选商品。")
    parser.add_argument(
        "--max-shops",
        type=int,
        default=0,
        help="最多处理多少家未完成店铺，0 表示处理全部。",
    )
    parser.add_argument(
        "--max-products-per-shop",
        type=int,
        default=0,
        help="单店最多抓多少个商品，0 表示不限制，直到把当前店铺商品抓完。",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="后台模式运行浏览器。",
    )
    return parser.parse_args()


def normalize_seller_url(url: str) -> str:
    """归一化店铺 URL。"""

    normalized = str(url or "").strip()
    if not normalized:
        return ""
    parts = urlsplit(normalized)
    path = parts.path.rstrip("/") + "/"
    return f"{parts.scheme}://{parts.netloc}{path}"


def build_shop_keyword(*, seller_name: str, seller_url: str) -> str:
    """为单店铺导出生成稳定标签。"""

    cleaned_name = re.sub(r"\s+", "_", str(seller_name or "").strip())
    cleaned_name = re.sub(r"[^0-9A-Za-zA-Z\u4e00-\u9fff_\-]+", "", cleaned_name).strip("_")

    path_parts = [item for item in urlsplit(seller_url).path.strip("/").split("/") if item]
    seller_slug = path_parts[-1] if path_parts else "seller"
    seller_slug = re.sub(r"[^0-9A-Za-z_\-]+", "", seller_slug) or "seller"

    if cleaned_name:
        return f"seller_{cleaned_name}_{seller_slug}"
    return f"seller_{seller_slug}"


def load_pending_seller_rows(repository: OzonReviewedSellerRepository) -> tuple[list[dict[str, Any]], int]:
    """从 SQLite 读取未完成的杂货铺店铺，并过滤空 URL。"""

    all_shops = repository.list_shops(crawl_status="all", shop_type="杂货铺")
    pending_rows: list[dict[str, Any]] = []
    skipped_completed_shop_count = 0

    for row in all_shops:
        seller_url = normalize_seller_url(str(row.get("seller_url") or ""))
        if not seller_url:
            continue
        crawl_status = str(row.get("crawl_status") or "pending").strip().lower()
        if crawl_status == "completed":
            skipped_completed_shop_count += 1
            continue
        pending_rows.append(
            {
                "seller_url": seller_url,
                "seller_name": str(row.get("seller_name") or "").strip(),
                "review_count": int(row.get("review_count") or 0),
                "source_product_sku": str(row.get("last_source_product_id") or "").strip(),
                "source_product_url": str(row.get("last_source_product_url") or "").strip(),
                "crawl_status": crawl_status,
            }
        )

    return pending_rows, skipped_completed_shop_count


def print_single_result(*, index: int, total: int, seller_name: str, seller_url: str, result: dict[str, Any]) -> None:
    """输出单店铺采集结果。"""

    print(f"[seller-shop] {index}/{total} completed")
    print(f"seller_name: {seller_name}")
    print(f"seller_url: {seller_url}")
    print(f"keyword: {result['keyword']}")
    print(f"search_url: {result['search_url']}")
    print(f"total_collected: {result['total_collected']}")
    print(f"qualified_count: {result['qualified_count']}")
    print(f"rejected_count: {result['rejected_count']}")
    print(f"image_dir: {result['image_dir']}")
    print(f"excel_path: {result['excel_path']}")
    print(f"sqlite_status: {result['sqlite_result']['status']}")
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
    if result["database_result"].get("error"):
        print(f"database_error: {result['database_result']['error']}")


def open_shop_page(page: Page, url: str, timeout_ms: int) -> None:
    """打开单个店铺页。"""

    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(2_500)


def main() -> None:
    """执行批量店铺采集。"""

    args = parse_args()
    settings = get_settings().model_copy(
        deep=True,
        update={
            "shopbang_headless": bool(args.background),
            **(
                {"ozon_scrape_target_products": int(args.max_products_per_shop)}
                if int(args.max_products_per_shop or 0) > 0
                else {}
            ),
        },
    )
    pipeline = OzonCandidatePipeline(settings=settings)
    collector = pipeline.collector
    repository = OzonReviewedSellerRepository(settings=settings)

    seller_rows, skipped_completed_shop_count = load_pending_seller_rows(repository)
    total_pending_shop_count = len(seller_rows)
    if args.max_shops and int(args.max_shops) > 0:
        seller_rows = seller_rows[: int(args.max_shops)]

    if not seller_rows:
        print("Ozon reviewed seller shop collection: skipped")
        print("shop_source: sqlite.ozon_reviewed_seller_shops[shop_type=杂货铺]")
        print(f"shop_count: 0")
        print(f"pending_shop_count: {total_pending_shop_count}")
        print(f"skipped_completed_shop_count: {skipped_completed_shop_count}")
        print(f"sqlite_db_path: {settings.sqlite_db_path}")
        return

    with sync_playwright() as playwright:
        collector.login_manager.validate_collection_prerequisites()
        session = collector.login_manager.open_browser_session(playwright=playwright)
        context = session.context
        page = context.new_page()

        try:
            results: list[dict[str, Any]] = []
            failures: list[dict[str, str]] = []

            for index, seller in enumerate(seller_rows, start=1):
                seller_url = seller["seller_url"]
                seller_name = seller["seller_name"]
                keyword = build_shop_keyword(seller_name=seller_name, seller_url=seller_url)

                print(
                    f"[seller-shop] {index}/{len(seller_rows)} start "
                    f"seller_name={seller_name or '-'} seller_url={seller_url}",
                    flush=True,
                )

                try:
                    repository.mark_shop_crawl_started(seller_url)
                    open_shop_page(page, seller_url, settings.playwright_timeout_ms)
                    products = collector.scrape_products_from_current_page(
                        context=context,
                        page=page,
                        target_count=int(args.max_products_per_shop) if int(args.max_products_per_shop or 0) > 0 else None,
                    )
                    result = pipeline.finalize_collected_products(
                        keyword=keyword,
                        search_url=seller_url,
                        products=products,
                        detail_context=context,
                    )
                    repository.mark_shop_crawl_completed(
                        seller_url=seller_url,
                        product_count=int(result.get("total_collected") or 0),
                        qualified_count=int(result.get("qualified_count") or 0),
                        rejected_count=int(result.get("rejected_count") or 0),
                    )
                    results.append(result)
                    print_single_result(
                        index=index,
                        total=len(seller_rows),
                        seller_name=seller_name,
                        seller_url=seller_url,
                        result=result,
                    )
                except Exception as exc:
                    print(
                        f"[seller-shop] {index}/{len(seller_rows)} failed "
                        f"seller_name={seller_name or '-'} seller_url={seller_url} error={exc}",
                        flush=True,
                    )
                    repository.mark_shop_crawl_failed(seller_url=seller_url, error=str(exc))
                    failures.append(
                        {
                            "seller_name": seller_name,
                            "seller_url": seller_url,
                            "error": str(exc),
                        }
                    )

            print("Ozon reviewed seller shop collection: completed")
            print("shop_source: sqlite.ozon_reviewed_seller_shops[shop_type=杂货铺]")
            print(f"shop_count: {len(seller_rows)}")
            print(f"pending_shop_count: {total_pending_shop_count}")
            print(f"skipped_completed_shop_count: {skipped_completed_shop_count}")
            if int(args.max_products_per_shop or 0) > 0:
                print(f"target_products_per_shop: {int(args.max_products_per_shop)}")
            else:
                print("target_products_per_shop: all")
            print(f"success_count: {len(results)}")
            print(f"failure_count: {len(failures)}")
            print(f"sqlite_db_path: {settings.sqlite_db_path}")

            for failure in failures:
                print("--- seller_failure ---")
                print(f"seller_name: {failure['seller_name']}")
                print(f"seller_url: {failure['seller_url']}")
                print(f"error: {failure['error']}")
        finally:
            try:
                if not page.is_closed():
                    page.close()
            except Exception:
                pass
            session.close()


if __name__ == "__main__":
    main()
