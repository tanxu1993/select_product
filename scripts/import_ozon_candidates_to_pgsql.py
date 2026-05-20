"""兼容入口：把 Ozon 候选清单导入 SQLite。"""

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
from ozon_selection.repositories.ozon_batch_repository import OzonBatchRepository
from ozon_selection.services.ozon_batch_importer import OzonBatchImporter


def parse_args() -> argparse.Namespace:
    """解析参数。"""

    parser = argparse.ArgumentParser(description="把 Ozon 候选清单导入 SQLite。")
    parser.add_argument(
        "--manifest",
        default=None,
        help="可选：指定要导入的 ozon_candidates_*.json；默认导入最新文件。",
    )
    parser.add_argument(
        "--init-schema",
        action="store_true",
        help="导入前先初始化 SQLite schema。",
    )
    return parser.parse_args()


def main() -> None:
    """脚本入口。"""

    args = parse_args()
    settings = get_settings()
    repository = OzonBatchRepository(settings=settings)
    importer = OzonBatchImporter(settings=settings, repository=repository)

    if args.init_schema:
        repository.ensure_schema()
        print("sqlite schema: initialized")

    result = importer.import_manifest(args.manifest)
    print(f"manifest_path: {result['manifest_path']}")
    print(f"keyword: {result['keyword']}")
    print(f"total_products: {result['total_products']}")
    print(f"database_status: {result['status']}")
    if result.get("batch_id") is not None:
        print(f"batch_id: {result['batch_id']}")


if __name__ == "__main__":
    main()
