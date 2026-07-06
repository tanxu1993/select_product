"""清理历史爬虫数据：SQLite、本地导出/图片，以及 Supabase 商品数据。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
import shutil


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings
from ozon_selection.repositories.candidate_repository import CandidateRepository
from ozon_selection.repositories.supplier_link_repository import SupplierLinkRepository
from supabase import create_client
from supabase.lib.client_options import SyncClientOptions


SQLITE_TABLES_TO_CLEAR = [
    "alibaba_image_search_results",
    "ozon_batch_products",
    "ozon_keyword_batches",
    "ozon_keyword_pool",
    "shopbang_hot_category_progress",
    "shopbang_history_keywords",
    "ozon_reviewed_seller_products",
    "ozon_reviewed_seller_shops",
]

EXPORT_PATTERNS_TO_CLEAR = [
    "ozon_candidates_*.json",
    "ozon_evaluated_*.json",
    "alibaba1688_image_search_*.json",
    "alibaba1688_image_search_*.xlsx",
    "shopbang_history_keywords_*.json",
    "shopbang_history_keywords_*.xlsx",
    "ozon_reviewed_sellers_*.json",
    "ozon_reviewed_sellers_*.xlsx",
    "ozon_home_categories_*.xlsx",
    "选品_*.xlsx",
]

EXPORT_DIRS_TO_CLEAR = [
    "product_parser",
]


def parse_args() -> argparse.Namespace:
    """解析脚本参数。"""

    parser = argparse.ArgumentParser(description="清理历史爬虫获取的本地和数据库数据。")
    parser.add_argument(
        "--skip-supabase",
        action="store_true",
        help="只清理本地 SQLite、导出文件和图片，不清理 Supabase。",
    )
    return parser.parse_args()


def clear_sqlite_data() -> dict[str, int | str]:
    """清空本地 SQLite 业务表。"""

    settings = get_settings()
    database_path = settings.sqlite_db_path
    if not database_path.exists():
        return {"status": "skipped", "reason": "sqlite_db_not_found", "tables_cleared": 0}

    import sqlite3

    with sqlite3.connect(database_path) as connection:
        connection.execute("pragma foreign_keys = on")
        cursor = connection.cursor()
        cursor.execute("begin")
        for table_name in SQLITE_TABLES_TO_CLEAR:
            cursor.execute(f"delete from {table_name}")
        connection.commit()

    return {"status": "cleared", "tables_cleared": len(SQLITE_TABLES_TO_CLEAR)}


def clear_export_files() -> dict[str, int | str]:
    """删除历史导出文件与调试目录。"""

    settings = get_settings()
    export_dir = settings.export_path
    if not export_dir.exists():
        return {"status": "skipped", "reason": "export_dir_not_found", "removed_files": 0, "removed_dirs": 0}

    removed_files = 0
    removed_dirs = 0

    for pattern in EXPORT_PATTERNS_TO_CLEAR:
        for file_path in export_dir.glob(pattern):
            if not file_path.is_file():
                continue
            file_path.unlink(missing_ok=True)
            removed_files += 1

    for dir_name in EXPORT_DIRS_TO_CLEAR:
        dir_path = export_dir / dir_name
        if dir_path.exists():
            shutil.rmtree(dir_path, ignore_errors=True)
            removed_dirs += 1

    return {"status": "cleared", "removed_files": removed_files, "removed_dirs": removed_dirs}


def clear_raw_images() -> dict[str, int | str]:
    """删除历史下载的 Ozon 商品图片目录。"""

    settings = get_settings()
    image_root = settings.ozon_scrape_image_path
    if not image_root.exists():
        return {"status": "skipped", "reason": "image_dir_not_found", "removed_dirs": 0}

    removed_dirs = 0
    for child in image_root.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
            removed_dirs += 1
        elif child.is_file():
            child.unlink(missing_ok=True)

    return {"status": "cleared", "removed_dirs": removed_dirs}


def _build_supabase_client():
    """创建 Supabase 客户端。"""

    settings = get_settings()
    options = SyncClientOptions(schema=settings.supabase_schema)
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key,
        options=options,
    )


def _delete_all_rows(table_name: str) -> int:
    """删除 Supabase 表中的全部记录。"""

    client = _build_supabase_client()
    deleted_count = 0
    page_size = 1000

    while True:
        response = (
            client.table(table_name)
            .select("id")
            .limit(page_size)
            .execute()
        )
        rows = response.data or []
        if not rows:
            break

        ids = [row.get("id") for row in rows if row.get("id") is not None]
        if not ids:
            break

        (
            client.table(table_name)
            .delete()
            .in_("id", ids)
            .execute()
        )
        deleted_count += len(ids)

        if len(ids) < page_size:
            break

    return deleted_count


def clear_supabase_data() -> dict[str, int | str | list[str]]:
    """清空 Supabase 中的商品与货源映射。"""

    settings = get_settings()
    candidate_repository = CandidateRepository(settings=settings)
    supplier_repository = SupplierLinkRepository(settings=settings)

    diagnostics = candidate_repository.get_configuration_diagnostics()
    supplier_diagnostics = supplier_repository.get_configuration_diagnostics()
    missing_fields = sorted(set(diagnostics["missing_fields"] + supplier_diagnostics["missing_fields"]))
    placeholder_fields = sorted(set(diagnostics["placeholder_fields"] + supplier_diagnostics["placeholder_fields"]))
    if missing_fields or placeholder_fields:
        return {
            "status": "skipped",
            "reason": "supabase_not_configured",
            "missing_fields": missing_fields,
            "placeholder_fields": placeholder_fields,
        }

    product_count = _delete_all_rows("product_candidates")
    supplier_count = _delete_all_rows("supplier_links")
    return {
        "status": "cleared",
        "deleted_product_candidates": product_count,
        "deleted_supplier_links": supplier_count,
    }


def main() -> None:
    """脚本入口。"""

    args = parse_args()

    sqlite_result = clear_sqlite_data()
    export_result = clear_export_files()
    image_result = clear_raw_images()

    print(f"sqlite_status: {sqlite_result['status']}")
    if sqlite_result.get("reason"):
        print(f"sqlite_reason: {sqlite_result['reason']}")
    if sqlite_result.get("tables_cleared") is not None:
        print(f"sqlite_tables_cleared: {sqlite_result['tables_cleared']}")

    print(f"export_status: {export_result['status']}")
    if export_result.get("reason"):
        print(f"export_reason: {export_result['reason']}")
    if export_result.get("removed_files") is not None:
        print(f"export_removed_files: {export_result['removed_files']}")
    if export_result.get("removed_dirs") is not None:
        print(f"export_removed_dirs: {export_result['removed_dirs']}")

    print(f"image_status: {image_result['status']}")
    if image_result.get("reason"):
        print(f"image_reason: {image_result['reason']}")
    if image_result.get("removed_dirs") is not None:
        print(f"image_removed_dirs: {image_result['removed_dirs']}")

    if args.skip_supabase:
        print("supabase_status: skipped")
        print("supabase_reason: skip_supabase")
        return

    supabase_result = clear_supabase_data()
    print(f"supabase_status: {supabase_result['status']}")
    if supabase_result.get("reason"):
        print(f"supabase_reason: {supabase_result['reason']}")
    if supabase_result.get("missing_fields"):
        print(f"supabase_missing_fields: {','.join(supabase_result['missing_fields'])}")
    if supabase_result.get("placeholder_fields"):
        print(f"supabase_placeholder_fields: {','.join(supabase_result['placeholder_fields'])}")
    if supabase_result.get("deleted_product_candidates") is not None:
        print(f"supabase_deleted_product_candidates: {supabase_result['deleted_product_candidates']}")
    if supabase_result.get("deleted_supplier_links") is not None:
        print(f"supabase_deleted_supplier_links: {supabase_result['deleted_supplier_links']}")


if __name__ == "__main__":
    main()
