"""从上品帮热销页提取结构化关键词，并写入 SQLite 关键词池。"""

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

from ozon_selection.collectors.ozon.shopbang_hot_keyword_collector import ShopbangHotKeywordCollector
from ozon_selection.repositories.ozon_keyword_pool_repository import OzonKeywordPoolRepository
from ozon_selection.services.ozon_candidate_pipeline import OzonCandidatePipeline


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="从上品帮热销页提取上级关键词，并写入 SQLite 关键词池。")
    parser.add_argument("--max-pages", type=int, default=2, help="本轮最多处理多少页热销列表。")
    parser.add_argument(
        "--max-products",
        dest="max_pages_legacy",
        type=int,
        help="兼容旧参数，现已等同于 --max-pages。",
    )
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="只提取并输出关键词，不写入 SQLite，也不执行 Ozon 候选采集。",
    )
    parser.add_argument(
        "--run-ozon-after-save",
        action="store_true",
        help="提取并保存关键词后，立刻用这批关键词执行 Ozon 候选采集。",
    )
    return parser.parse_args()


def print_ozon_result(result: dict, sqlite_db_path: str) -> None:
    """输出单个关键词的 Ozon 采集结果。"""

    print("Ozon candidate collection: completed")
    print(f"keyword: {result['keyword']}")
    print(f"search_url: {result['search_url']}")
    print(f"total_collected: {result['total_collected']}")
    print(f"qualified_count: {result['qualified_count']}")
    print(f"rejected_count: {result['rejected_count']}")
    print(f"image_dir: {result['image_dir']}")
    print(f"sqlite_status: {result['sqlite_result']['status']}")
    print(f"sqlite_db_path: {sqlite_db_path}")
    if result["sqlite_result"].get("batch_id") is not None:
        print(f"sqlite_batch_id: {result['sqlite_result']['batch_id']}")


def main() -> None:
    """执行热销关键词提取，并按需启动 Ozon 候选采集。"""

    args = parse_args()
    max_pages = args.max_pages_legacy if args.max_pages_legacy is not None else args.max_pages
    collector = ShopbangHotKeywordCollector()

    with sync_playwright() as playwright:
        keyword_result = collector.collect_keywords(
            playwright,
            max_pages=max_pages,
        )

    print("shopbang hot keyword collection: completed")
    print(f"remai_url: {keyword_result['remai_url']}")
    if keyword_result.get("category_count") is not None:
        print(f"category_count: {keyword_result['category_count']}")
    if keyword_result.get("processed_url_count") is not None:
        print(f"processed_url_count: {keyword_result['processed_url_count']}")
    print(f"max_pages: {max_pages}")
    print(f"detail_candidates: {keyword_result['detail_candidates']}")
    print(f"skipped_entries: {keyword_result['skipped_entries']}")
    print(f"keyword_count: {keyword_result['keyword_count']}")
    print(f"keyword_record_count: {len(keyword_result.get('keyword_records') or [])}")
    print(f"keywords: {', '.join(keyword_result['keywords'])}")
    if keyword_result.get("processed_categories"):
        print(f"processed_categories: {', '.join(keyword_result['processed_categories'])}")
    if keyword_result.get("selected_category"):
        print(f"selected_category: {keyword_result['selected_category']}")
    if keyword_result.get("resume_from_page") is not None:
        print(f"resume_from_page: {keyword_result['resume_from_page']}")
    if keyword_result.get("last_completed_page") is not None:
        print(f"last_completed_page: {keyword_result['last_completed_page']}")
    if keyword_result.get("progress_status"):
        print(f"progress_status: {keyword_result['progress_status']}")
    filter_result = keyword_result.get("filter_result") or {}
    print(f"filter_status: {filter_result.get('status', 'skipped')}")
    if filter_result.get("clicked") is not None:
        print(f"filter_clicked: {filter_result['clicked']}")
    if filter_result.get("error"):
        print(f"filter_error: {filter_result['error']}")

    if args.extract_only:
        return

    keyword_repository = OzonKeywordPoolRepository()
    save_result = keyword_repository.upsert_keyword_records(keyword_result.get("keyword_records") or [])
    print(f"keyword_pool_status: {save_result.get('status')}")
    if save_result.get("reason"):
        print(f"keyword_pool_note: {save_result['reason']}")
    if save_result.get("input_keyword_count") is not None:
        print(f"keyword_pool_input_count: {save_result['input_keyword_count']}")
    print(f"keyword_pool_saved_count: {save_result.get('saved_count', 0)}")
    if save_result.get("removed_count") is not None:
        print(f"keyword_pool_removed_count: {save_result['removed_count']}")
    if save_result.get("removed_keywords"):
        print(f"keyword_pool_removed_keywords: {', '.join(save_result['removed_keywords'])}")
    print(f"sqlite_db_path: {keyword_repository.settings.sqlite_db_path}")

    if not args.run_ozon_after_save:
        return

    keywords = keyword_result["keywords"]
    if not keywords:
        print("ozon_collection_status: skipped")
        print("ozon_collection_note: no_keywords_extracted")
        return

    pipeline = OzonCandidatePipeline()
    batch_result = pipeline.run_for_keywords(keywords)
    print("Ozon multi-keyword collection: completed")
    print(f"keyword_source: {batch_result['keyword_source']}")
    print(f"success_count: {batch_result['success_count']}")
    print(f"failure_count: {batch_result['failure_count']}")
    print(f"skipped_count: {batch_result['skipped_count']}")
    print(f"sqlite_db_path: {batch_result['sqlite_db_path']}")
    print(f"checkpoint_path: {batch_result['checkpoint_path']}")

    for index, result in enumerate(batch_result["results"], start=1):
        print(f"--- keyword_result_{index} ---")
        print_ozon_result(result, batch_result["sqlite_db_path"])

    for failure in batch_result["failures"]:
        print(f"failed_keyword: {failure['keyword']}")
        print(f"failed_error: {failure['error']}")


if __name__ == "__main__":
    main()
