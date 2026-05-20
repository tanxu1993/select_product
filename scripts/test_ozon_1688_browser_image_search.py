"""Ozon 主图 -> 1688 浏览器以图搜图联调脚本。"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import load_workbook
from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings
from ozon_selection.collectors.alibaba.image_search import Alibaba1688ImageSearchBrowser
from ozon_selection.collectors.ozon.product_collector import ProductCollector
from ozon_selection.repositories.supplier_link_repository import SupplierLinkRepository
from scripts.test_ozon_openai_parse import collect_one_product


def main() -> None:
    """抓一个 Ozon 商品，并把 1688 首屏图搜图结果落到 Excel 和数据库。"""

    settings = get_settings()
    output_dir = settings.product_parser_test_output_path
    output_dir.mkdir(parents=True, exist_ok=True)

    collector = ProductCollector(settings=settings)
    browser_search = Alibaba1688ImageSearchBrowser(settings=settings)
    repository = SupplierLinkRepository(settings=settings)

    with sync_playwright() as playwright:
        product = collect_one_product(collector, playwright)
        image_url = product.get("imageUrl")
        if not image_url:
            raise RuntimeError("未获取到 Ozon 商品主图 URL。")
        result = browser_search.search_by_image(
            playwright=playwright,
            image_url=image_url,
        )

    excel_rows = build_excel_rows(product, result["items"])
    excel_path = export_results_to_excel(
        rows=excel_rows,
        output_dir=output_dir,
        sku=str(product.get("sku") or "unknown"),
    )

    db_payloads = build_database_rows(product, result["items"])
    db_result = save_results_to_database(repository, db_payloads)

    output = {
        "tested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ozon_product": {
            "sku": product.get("sku"),
            "title": product.get("name"),
            "url": product.get("url"),
            "image_url": image_url,
            "price": product.get("price"),
        },
        "image_search": result,
        "excel_path": str(excel_path),
        "database_result": db_result,
    }

    output_path = (
        output_dir
        / f"ozon_1688_browser_image_search_{product.get('sku', 'unknown')}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Ozon product: {product.get('name')}")
    print(f"Ozon image: {image_url}")
    print(f"1688 final url: {result.get('final_url')}")
    print(f"1688 hits: {len(result['items'])}")
    print(f"Excel saved to: {excel_path}")
    print(f"Database status: {db_result['status']}")
    if db_result.get("reason"):
        print(f"Database note: {db_result['reason']}")
    if db_result.get("error"):
        print(f"Database error: {db_result['error']}")
    if result["items"]:
        print("Top 3 1688 matches:")
        for index, item in enumerate(result["items"][:3], start=1):
            print(
                f"{index}. {item.get('title')} | unit={item.get('unit_price')} | "
                f"weight={item.get('weight_grams')}g | {item.get('detail_url')}"
            )
    print(f"Saved result to: {output_path}")


def build_excel_rows(ozon_product: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把结果转换成 Excel 行。"""

    rows: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        rows.append(
            {
                "#": index,
                "Ozon SKU": ozon_product.get("sku"),
                "Ozon商品": ozon_product.get("name"),
                "Ozon链接": ozon_product.get("url"),
                "Ozon主图": ozon_product.get("imageUrl"),
                "Ozon价格": ozon_product.get("price"),
                "1688标题": item.get("title"),
                "1688链接": item.get("detail_url"),
                "1688列表价": item.get("price"),
                "1688价格文案": item.get("price_text"),
                "1688单价": item.get("unit_price"),
                "1688单价文案": item.get("unit_price_text"),
                "1688重量文案": item.get("weight_text"),
                "1688重量(g)": item.get("weight_grams"),
                "1688卖家": item.get("seller"),
                "1688图片": item.get("image_url"),
                "详情抓取异常": item.get("detail_error"),
            }
        )
    return rows


def build_database_rows(ozon_product: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把匹配结果转换成数据库记录。"""

    rows: list[dict[str, Any]] = []
    for item in items:
        rows.append(
            {
                "source_platform": "ozon",
                "source_product_id": str(ozon_product.get("sku") or ""),
                "source_title": ozon_product.get("name"),
                "source_product_url": ozon_product.get("url"),
                "source_image_url": ozon_product.get("imageUrl"),
                "source_price": normalize_number(ozon_product.get("price")),
                "supplier_platform": "1688",
                "supplier_title": item.get("title"),
                "supplier_product_url": item.get("detail_url"),
                "supplier_image_url": item.get("image_url"),
                "supplier_price": normalize_number(item.get("price")),
                "supplier_price_text": item.get("price_text"),
                "supplier_unit_price": normalize_number(item.get("unit_price")),
                "supplier_unit_price_text": item.get("unit_price_text"),
                "supplier_weight_text": item.get("weight_text"),
                "supplier_weight_grams": normalize_number(item.get("weight_grams")),
                "supplier_seller": item.get("seller"),
                "search_method": "1688_browser_image_search",
                "raw_payload": item,
            }
        )
    return rows


def save_results_to_database(
    repository: SupplierLinkRepository,
    payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    """保存结果到 Supabase，失败时保留错误信息。"""

    try:
        return repository.save_many(payloads)
    except Exception as exc:
        return {
            "status": "failed",
            "count": 0,
            "table": repository.table_name,
            "error": str(exc),
        }


def export_results_to_excel(*, rows: list[dict[str, Any]], output_dir: Path, sku: str) -> Path:
    """把图搜图结果导出成 Excel。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"ozon_1688_browser_image_search_{sku}_{timestamp}.xlsx"
    frame = pd.DataFrame(rows)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="1688图搜图", index=False)

    workbook = load_workbook(output_path)
    sheet = workbook["1688图搜图"]
    widths = [6, 14, 30, 42, 42, 12, 42, 42, 14, 18, 14, 18, 18, 14, 18, 42, 24]
    for column_index, width in enumerate(widths, start=1):
        sheet.column_dimensions[column_letter(column_index)].width = width
    workbook.save(output_path)
    return output_path


def normalize_number(value: Any) -> float | None:
    """把字符串或数字统一转成浮点数。"""

    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    matched = re.search(r"(\d+(?:\.\d+)?)", str(value).replace(",", ""))
    if not matched:
        return None
    return float(matched.group(1))


def column_letter(index: int) -> str:
    """把列号转换成 Excel 列字母。"""

    letters = ""
    current = index
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


if __name__ == "__main__":
    main()
