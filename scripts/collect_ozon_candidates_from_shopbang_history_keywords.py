"""从 Shopbang 历史关键词表读取未爬关键词，并采集 Ozon 候选商品。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


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

    parser = argparse.ArgumentParser(description="从 Shopbang 历史关键词表读取未爬关键词并采集 Ozon 候选商品。")
    parser.add_argument(
        "--target-products",
        type=int,
        default=0,
        help="单个关键词目标抓取商品数；大于 0 时覆盖 `.env` 中的 OZON_SCRAPE_TARGET_PRODUCTS。",
    )
    parser.add_argument(
        "--take-count",
        type=int,
        default=0,
        help="本次最多处理多少个未爬关键词；大于 0 时会覆盖 --pool-count。",
    )
    parser.add_argument(
        "--pool-count",
        type=int,
        default=5,
        help="从 Shopbang 历史关键词表中随机抽取多少个未爬关键词，默认 5。",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="后台模式：使用无界面浏览器运行，避免抢占鼠标和键盘焦点。",
    )
    return parser.parse_args()


def print_single_result(result: dict, sqlite_db_path: str) -> None:
    """输出单个关键词的采集结果。"""

    print("Ozon candidate collection: completed")
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
    """执行 Shopbang 历史关键词到 Ozon 候选商品的采集流程。"""

    args = parse_args()
    target_products = max(int(args.target_products or 0), 0)
    take_count = max(int(args.take_count or 0), 0)
    pool_count = max(int(args.pool_count or 0), 0)
    if take_count > 0:
        pool_count = take_count

    settings_update = {
        "shopbang_headless": bool(args.background),
    }
    if target_products > 0:
        settings_update["ozon_scrape_target_products"] = target_products

    settings = get_settings().model_copy(
        deep=True,
        update=settings_update,
    )
    pipeline = OzonCandidatePipeline(settings=settings)
    batch_result = pipeline.run_for_shopbang_history_keywords(pool_count=pool_count)

    print("Ozon multi-keyword collection: completed")
    print(f"keyword_source: {batch_result['keyword_source']}")
    print(f"target_products: {settings.ozon_scrape_target_products}")
    print(f"take_count: {take_count if take_count > 0 else pool_count}")
    print(f"pool_count: {batch_result['pool_count']}")
    print(f"keywords: {', '.join(batch_result['keywords'])}")
    print(f"success_count: {batch_result['success_count']}")
    print(f"failure_count: {batch_result['failure_count']}")
    print(f"skipped_count: {batch_result['skipped_count']}")
    print(f"sqlite_db_path: {batch_result['sqlite_db_path']}")

    for index, result in enumerate(batch_result["results"], start=1):
        print(f"--- keyword_result_{index} ---")
        print_single_result(result, batch_result["sqlite_db_path"])

    for failure in batch_result["failures"]:
        print(f"failed_keyword: {failure['keyword']}")
        print(f"failed_error: {failure['error']}")


if __name__ == "__main__":
    main()
