"""从 Shopbang 历史页抓取价格区间关键词并写入 SQLite。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings
from ozon_selection.collectors.ozon.shopbang_history_keyword_collector import ShopbangHistoryKeywordCollector
from ozon_selection.repositories.shopbang_history_keyword_repository import ShopbangHistoryKeywordRepository


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="从 Shopbang 历史页抓取 500-20000 价格区间关键词并写入 SQLite。")
    parser.add_argument("--min-avg-price", type=float, default=500, help="商品平均价格下限，默认 500。")
    parser.add_argument("--max-avg-price", type=float, default=20000, help="商品平均价格上限，默认 20000。")
    parser.add_argument("--max-pages", type=int, default=100, help="最多抓取多少页，默认 100。")
    parser.add_argument(
        "--exclude-keywords",
        type=str,
        default="",
        help="额外排除的关键词片段，支持逗号分隔；默认已内置服饰、鞋靴、药品、手机类过滤。",
    )
    parser.add_argument("--background", action="store_true", help="后台模式运行浏览器。")
    return parser.parse_args()


def parse_keyword_tokens(raw_value: str) -> list[str]:
    """解析排除关键词片段。"""

    return [item.strip() for item in str(raw_value or "").replace("，", ",").split(",") if item.strip()]


def build_export_rows(records: list[dict]) -> list[dict]:
    """构造导出行。"""

    rows: list[dict] = []
    for item in records:
        rows.append(
            {
                "关键词": item.get("keyword"),
                "平均价格": item.get("avg_price"),
                "来源页码": item.get("source_page"),
                "价格下限": item.get("price_min"),
                "价格上限": item.get("price_max"),
                "来源接口": item.get("source_endpoint"),
                "原始数据": json.dumps(item.get("raw_payload") or {}, ensure_ascii=False, sort_keys=True),
            }
        )
    return rows


def write_excel(path: Path, rows: list[dict]) -> None:
    """导出 xlsx。"""

    frame = pd.DataFrame(rows)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="shopbang_history_keywords")


def write_json(path: Path, payload: dict) -> None:
    """导出 json。"""

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    """执行抓取并保存。"""

    args = parse_args()
    excluded_keywords = parse_keyword_tokens(args.exclude_keywords)
    settings = get_settings().model_copy(
        deep=True,
        update={
            "shopbang_headless": bool(args.background),
        },
    )

    collector = ShopbangHistoryKeywordCollector(settings=settings)
    repository = ShopbangHistoryKeywordRepository(settings=settings)
    effective_excluded_keywords = collector.build_excluded_keyword_fragments(excluded_keywords)

    pass_specs = [
        {
            "label": f"商品平均价格 > {int(args.min_avg_price)}",
            "min_avg_price": float(args.min_avg_price),
            "max_avg_price": None,
        },
        {
            "label": f"商品平均价格 < {int(args.max_avg_price)}",
            "min_avg_price": None,
            "max_avg_price": float(args.max_avg_price),
        },
    ]

    pass_results: list[dict] = []
    all_keyword_records: list[dict] = []
    with sync_playwright() as playwright:
        for spec in pass_specs:
            result = collector.collect_keywords(
                playwright,
                min_avg_price=spec["min_avg_price"],
                max_avg_price=spec["max_avg_price"],
                max_pages=max(int(args.max_pages or 0), 1),
                excluded_keywords=effective_excluded_keywords,
                condition_label=str(spec["label"]),
            )
            pass_results.append(result)
            all_keyword_records.extend(list(result.get("keyword_records") or []))

    export_keyword_records, export_filtered_out_count = repository.filter_records_by_avg_price(
        repository._dedupe_records(all_keyword_records)
    )
    sqlite_result = repository.upsert_keywords(all_keyword_records)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_base = settings.export_path / f"shopbang_history_keywords_{timestamp}"
    export_base.parent.mkdir(parents=True, exist_ok=True)
    json_path = export_base.with_suffix(".json")
    xlsx_path = export_base.with_suffix(".xlsx")
    export_rows = build_export_rows(export_keyword_records)
    write_json(
        json_path,
        {
            "history_url": settings.shopbang_history_url,
            "min_avg_price": args.min_avg_price,
            "max_avg_price": args.max_avg_price,
            "max_pages": args.max_pages,
            "excluded_keywords": effective_excluded_keywords,
            "keyword_count": len(export_keyword_records),
            "keyword_records": export_keyword_records,
            "raw_keyword_count": len(all_keyword_records),
            "filtered_out_by_avg_price_count": export_filtered_out_count,
            "pass_results": [
                {
                    "condition_label": item.get("filter_result", {}).get("condition_label"),
                    "request_endpoint": item.get("request_endpoint"),
                    "request_method": item.get("request_method"),
                    "request_body": item.get("request_body") or {},
                    "keyword_count": item.get("keyword_count"),
                }
                for item in pass_results
            ],
            "sqlite_result": sqlite_result,
        },
    )
    write_excel(xlsx_path, export_rows)

    print("shopbang history keyword collection: completed")
    print(f"history_url: {settings.shopbang_history_url}")
    print(f"min_avg_price: {args.min_avg_price}")
    print(f"max_avg_price: {args.max_avg_price}")
    print(f"max_pages: {args.max_pages}")
    print(f"excluded_keywords: {', '.join(effective_excluded_keywords)}")
    for index, item in enumerate(pass_results, start=1):
        print(f"pass_{index}_condition: {item.get('filter_result', {}).get('condition_label')}")
        print(f"pass_{index}_request_endpoint: {item.get('request_endpoint')}")
        print(f"pass_{index}_request_method: {item.get('request_method')}")
        print(f"pass_{index}_keyword_count: {item.get('keyword_count')}")
    print(f"raw_keyword_count: {len(all_keyword_records)}")
    print(f"keyword_count: {len(export_keyword_records)}")
    print(f"filtered_out_by_avg_price_count: {export_filtered_out_count}")
    print(f"sqlite_status: {sqlite_result.get('status')}")
    print(f"sqlite_saved_count: {sqlite_result.get('saved_count', 0)}")
    if sqlite_result.get("reason"):
        print(f"sqlite_note: {sqlite_result.get('reason')}")
    print(f"sqlite_db_path: {settings.sqlite_db_path}")
    print(f"json_path: {json_path}")
    print(f"excel_path: {xlsx_path}")


if __name__ == "__main__":
    main()
