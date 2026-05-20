"""对 Ozon 关键词做完全重复或语义重复去重。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ozon_selection.services.keyword_deduper import KeywordDeduper


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="对关键词列表做完全重复或启发式语义去重。")
    parser.add_argument("--keywords", type=str, default="", help="关键词文本，支持逗号、分号、换行分隔。")
    parser.add_argument("--input-file", type=str, default="", help="可选：从文本文件读取关键词。")
    parser.add_argument(
        "--mode",
        choices=["exact", "semantic", "both"],
        default="both",
        help="去重模式。`exact` 只做完全重复，`semantic` 做启发式父子类目去重，`both` 同时输出两套结果。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出结果。",
    )
    return parser.parse_args()


def load_keywords(args: argparse.Namespace) -> list[str]:
    """从参数或文件读取关键词列表。"""

    raw_value = args.keywords
    if args.input_file:
        raw_value = Path(args.input_file).read_text(encoding="utf-8")
    return KeywordDeduper.parse_keywords(raw_value)


def format_result(keywords: list[str], mode: str) -> dict[str, object]:
    """生成结构化输出。"""

    deduped = (
        KeywordDeduper.dedupe_exact(keywords)
        if mode == "exact"
        else KeywordDeduper.dedupe_semantic(keywords)
    )
    return {
        "mode": mode,
        "input_count": len(keywords),
        "kept_count": len(deduped),
        "removed_count": len(keywords) - len(deduped),
        "kept_keywords": [item.keyword for item in deduped],
        "groups": [
            {
                "kept_keyword": item.keyword,
                "removed_keywords": item.removed_keywords,
                "reason": item.reason,
            }
            for item in deduped
        ],
    }


def print_text_result(result: dict[str, object]) -> None:
    """输出文本结果。"""

    print(f"mode: {result['mode']}")
    print(f"input_count: {result['input_count']}")
    print(f"kept_count: {result['kept_count']}")
    print(f"removed_count: {result['removed_count']}")
    print(f"kept_keywords: {', '.join(result['kept_keywords'])}")
    print("groups:")
    for group in result["groups"]:
        removed = ", ".join(group["removed_keywords"]) if group["removed_keywords"] else "-"
        print(f"  kept: {group['kept_keyword']}")
        print(f"  removed: {removed}")
        print(f"  reason: {group['reason']}")


def main() -> None:
    """执行关键词去重。"""

    args = parse_args()
    keywords = load_keywords(args)
    if not keywords:
        raise SystemExit("未提供关键词。请使用 --keywords 或 --input-file。")

    modes = [args.mode] if args.mode != "both" else ["exact", "semantic"]
    results = [format_result(keywords, mode) for mode in modes]

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], ensure_ascii=False, indent=2))
        return

    for index, result in enumerate(results, start=1):
        if index > 1:
            print("")
        print_text_result(result)


if __name__ == "__main__":
    main()
