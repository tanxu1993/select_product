"""第 2 步：采集 Ozon 合格商品并保存结果。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ozon_selection.services.ozon_candidate_pipeline import OzonCandidatePipeline
from config.settings import get_settings


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="采集 Ozon 候选商品并写入 SQLite。")
    parser.add_argument(
        "--keywords",
        type=str,
        default="",
        help="可选：手动传入关键词列表，支持逗号、分号和换行分隔。",
    )
    parser.add_argument(
        "--take-count",
        type=int,
        default=0,
        help="本次最多处理多少个关键词。可用于控制一次只跑 1 个词或 5 个词；对手动关键词和 SQLite 关键词池都生效。",
    )
    parser.add_argument(
        "--pool-count",
        type=int,
        default=5,
        help="兼容旧参数。未手动传入关键词时，从 SQLite 关键词池随机抽取多少个未使用关键词。",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="后台模式：使用无界面浏览器运行，避免抢占鼠标和键盘焦点。",
    )
    return parser.parse_args()


def parse_keywords_input(raw_value: str) -> list[str]:
    """把输入关键词字符串解析成列表。"""

    return [item.strip() for item in re.split(r"[\n,;，；]+", raw_value or "") if item.strip()]


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
    """执行 Ozon 候选商品采集流程。"""

    args = parse_args()
    keywords = parse_keywords_input(args.keywords)
    take_count = max(int(args.take_count or 0), 0)
    if take_count > 0 and keywords:
        keywords = keywords[:take_count]

    pool_count = max(int(args.pool_count or 0), 0)
    if take_count > 0:
        pool_count = take_count

    settings = get_settings().model_copy(
        deep=True,
        update={
            "shopbang_headless": bool(args.background),
        },
    )
    pipeline = OzonCandidatePipeline(settings=settings)
    batch_result = pipeline.run_for_keywords(keywords or None, pool_count=pool_count)

    print("Ozon multi-keyword collection: completed")
    print(f"keyword_source: {batch_result['keyword_source']}")
    print(f"take_count: {take_count if take_count > 0 else pool_count}")
    print(f"pool_count: {batch_result['pool_count']}")
    print(f"keywords: {', '.join(batch_result['keywords'])}")
    print(f"success_count: {batch_result['success_count']}")
    print(f"failure_count: {batch_result['failure_count']}")
    print(f"skipped_count: {batch_result['skipped_count']}")
    print(f"sqlite_db_path: {batch_result['sqlite_db_path']}")
    if batch_result["checkpoint_path"]:
        print(f"checkpoint_path: {batch_result['checkpoint_path']}")
    if batch_result["skipped_keywords"]:
        print(f"skipped_keywords: {', '.join(batch_result['skipped_keywords'])}")

    for index, result in enumerate(batch_result["results"], start=1):
        print(f"--- keyword_result_{index} ---")
        print_single_result(result, batch_result["sqlite_db_path"])

    for failure in batch_result["failures"]:
        print(f"failed_keyword: {failure['keyword']}")
        print(f"failed_error: {failure['error']}")


if __name__ == "__main__":
    main()
