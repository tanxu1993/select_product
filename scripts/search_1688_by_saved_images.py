"""第 3 步：读取人工审核完成的 Ozon 主图并批量执行 1688 图搜图。"""

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

from ozon_selection.services.alibaba_image_search_pipeline import AlibabaImageSearchPipeline
from config.settings import get_settings


def parse_args() -> argparse.Namespace:
    """解析调试参数。"""

    parser = argparse.ArgumentParser(description="读取人工审核完成的 Ozon 主图并批量执行 1688 图搜图。")
    parser.add_argument(
        "--max-products",
        type=int,
        default=None,
        help="只处理前 N 个 Ozon 商品。",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=None,
        help="每个 Ozon 商品只抓前 N 个 1688 结果。",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="后台模式：使用无界面浏览器运行，避免抢占鼠标和键盘焦点。",
    )
    return parser.parse_args()


def main() -> None:
    """读取人工审核完成的 Ozon 主图，并执行 1688 图搜图。"""

    args = parse_args()
    settings = get_settings().model_copy(
        deep=True,
        update={
            "alibaba1688_headless": bool(args.background),
        },
    )
    result = AlibabaImageSearchPipeline(settings=settings).run(
        max_products=args.max_products,
        max_results=args.max_results,
    )
    print("1688 browser image search: completed")
    print(f"source_type: {result['source_type']}")
    print(f"source_reference: {result['source_reference']}")
    print(f"processed_products: {result['processed_products']}")
    print(f"matched_items: {result['matched_items']}")
    print(f"auth_state_path: {result['auth_state_path']}")
    print(f"login_status: {result['login_result']['status']}")
    if result["login_result"].get("auth_state_path"):
        print(f"login_auth_state_path: {result['login_result']['auth_state_path']}")
    print(f"excel_status: {result['excel_result']['status']}")
    if result["excel_result"].get("count") is not None:
        print(f"excel_rows: {result['excel_result']['count']}")
    if result["excel_result"].get("path"):
        print(f"excel_path: {result['excel_result']['path']}")
    if result["excel_result"].get("reason"):
        print(f"excel_note: {result['excel_result']['reason']}")
    if result["excel_result"].get("error"):
        print(f"excel_error: {result['excel_result']['error']}")
    print(f"processed_status: {result['processed_result']['status']}")
    if result["processed_result"].get("updated_count") is not None:
        print(f"processed_products_marked: {result['processed_result']['updated_count']}")
    if result["processed_result"].get("reason"):
        print(f"processed_note: {result['processed_result']['reason']}")
    print(f"sqlite_status: {result['sqlite_result']['status']}")
    if result["sqlite_result"].get("count") is not None:
        print(f"sqlite_saved_count: {result['sqlite_result']['count']}")
    if result["sqlite_result"].get("reason"):
        print(f"sqlite_note: {result['sqlite_result']['reason']}")
    if result["sqlite_result"].get("error"):
        print(f"sqlite_error: {result['sqlite_result']['error']}")
    print(f"search_status: {result['search_result']['status']}")
    print(f"search_failed_products: {result['search_result'].get('failed_products', 0)}")
    print(f"image_prefilter_status: {result['image_prefilter_result']['status']}")
    print(f"image_prefilter_items: {result['image_prefilter_result'].get('evaluated_items', 0)}")
    print(f"image_prefilter_passed: {result['image_prefilter_result'].get('passed_items', 0)}")
    print(f"image_prefilter_selected: {result['image_prefilter_result'].get('selected_items', 0)}")
    if result["image_prefilter_result"].get("reason"):
        print(f"image_prefilter_note: {result['image_prefilter_result']['reason']}")
    if result["image_prefilter_result"].get("failed_items"):
        print(f"image_prefilter_failed_items: {result['image_prefilter_result']['failed_items']}")
    print(f"detail_status: {result['detail_result']['status']}")
    print(f"detail_enriched_items: {result['detail_result'].get('enriched_items', 0)}")
    if result["detail_result"].get("failed_items"):
        print(f"detail_failed_items: {result['detail_result']['failed_items']}")
    print(f"database_status: {result['database_result']['status']}")
    if result["database_result"].get("reason"):
        print(f"database_note: {result['database_result']['reason']}")
    if result["database_result"].get("missing_fields"):
        print(f"database_missing_fields: {','.join(result['database_result']['missing_fields'])}")
    if result["database_result"].get("placeholder_fields"):
        print(f"database_placeholder_fields: {','.join(result['database_result']['placeholder_fields'])}")
    if result["database_result"].get("error"):
        print(f"database_error: {result['database_result']['error']}")


if __name__ == "__main__":
    main()
