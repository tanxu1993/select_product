"""读取 CSV 主图，直接执行 1688 以图搜图，并仅保存本地结果文件。"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment
import requests
from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings
from ozon_selection.services.alibaba_image_search_pipeline import AlibabaImageSearchPipeline


DEFAULT_CSV_NAME = "Seerfar-Product20260706_2000.csv"
DEFAULT_IMAGE_COLUMN = "主图"
DEFAULT_TITLE_COLUMN = "标题"
DEFAULT_URL_COLUMN = "详情页地址"
DEFAULT_SKU_COLUMN = "SKU"
DEFAULT_SALES_COLUMN = "销量"
DEFAULT_PRICE_COLUMN = "售价"
DEFAULT_WEIGHT_COLUMN = "重量"
DEFAULT_SHOP_COLUMN = "店铺"
DEFAULT_BRAND_COLUMN = "品牌"
DEFAULT_CATEGORY_COLUMN = "类目"
DEFAULT_RESUME_STATE_NAME = "csv_1688_image_search_resume_state.json"
IMAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.ozon.ru/",
}


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="读取 CSV 主图，直接执行 1688 以图搜图，并保存本地 xlsx/json。")
    parser.add_argument(
        "--csv",
        type=str,
        default=DEFAULT_CSV_NAME,
        help=f"CSV 文件路径，默认 `{DEFAULT_CSV_NAME}`。",
    )
    parser.add_argument(
        "--max-products",
        type=int,
        default=None,
        help="只处理前 N 个有效商品。",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=None,
        help="每个商品最多抓前 N 个 1688 结果。",
    )
    parser.add_argument(
        "--download-dir",
        type=str,
        default="data/raw/csv_source_images",
        help="下载 CSV 主图到本地的根目录。",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="后台模式运行 1688 浏览器。",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="忽略上次进度，从头开始处理当前 CSV。",
    )
    return parser.parse_args()


def sanitize_filename(value: str, *, fallback: str) -> str:
    """把任意文本清洗为安全文件名。"""

    normalized = re.sub(r"\s+", "_", str(value or "").strip())
    normalized = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "", normalized)
    normalized = normalized.strip("_")
    return normalized or fallback


def parse_numeric(value: Any) -> int | None:
    """从字符串中提取整数。"""

    text = str(value or "").strip()
    if not text:
        return None
    matched = re.search(r"(\d+(?:[.,]\d+)?)", text.replace(" ", ""))
    if not matched:
        return None
    raw = matched.group(1).replace(",", ".")
    try:
        return int(float(raw))
    except ValueError:
        return None


def resolve_csv_path(raw_path: str) -> Path:
    """解析 CSV 路径。"""

    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"未找到 CSV 文件: {path}")
    return path


def resolve_download_root(raw_path: str) -> Path:
    """解析图片下载根目录。"""

    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def download_image(image_url: str, destination: Path) -> None:
    """下载主图到本地。"""

    if destination.exists():
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(
        image_url,
        headers=IMAGE_HEADERS,
        timeout=20,
        allow_redirects=True,
    )
    response.raise_for_status()
    destination.write_bytes(response.content)


def get_resume_state_path(settings) -> Path:
    """返回 CSV 图搜图续跑状态文件路径。"""

    return settings.processed_data_path / DEFAULT_RESUME_STATE_NAME


def load_resume_state(state_path: Path) -> dict[str, Any]:
    """读取续跑状态文件。"""

    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_resume_state(state_path: Path, payload: dict[str, Any]) -> None:
    """写入续跑状态文件。"""

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_resume_key(csv_path: Path) -> str:
    """生成续跑状态中的 CSV 唯一键。"""

    return str(csv_path.resolve())


def get_resume_checkpoint(state_path: Path, csv_path: Path) -> dict[str, Any]:
    """获取指定 CSV 的续跑断点。"""

    state = load_resume_state(state_path)
    checkpoint = state.get(build_resume_key(csv_path))
    return checkpoint if isinstance(checkpoint, dict) else {}


def update_resume_checkpoint(
    state_path: Path,
    *,
    csv_path: Path,
    processed_valid_products: int,
    last_completed_sku: str,
    last_run_id: str,
) -> None:
    """更新指定 CSV 的续跑断点。"""

    state = load_resume_state(state_path)
    state[build_resume_key(csv_path)] = {
        "source_reference": str(csv_path),
        "csv_mtime_ns": csv_path.stat().st_mtime_ns,
        "processed_valid_products": int(processed_valid_products),
        "last_completed_sku": last_completed_sku,
        "last_run_id": last_run_id,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_resume_state(state_path, state)


def reset_resume_checkpoint(state_path: Path, *, csv_path: Path) -> None:
    """清空指定 CSV 的续跑断点。"""

    state = load_resume_state(state_path)
    state.pop(build_resume_key(csv_path), None)
    save_resume_state(state_path, state)


def build_csv_products(
    csv_path: Path,
    download_root: Path,
    *,
    max_products: int | None,
    skip_valid_products: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """从 CSV 构造 1688 图搜图所需的 Ozon 商品结构。"""

    products: list[dict[str, Any]] = []
    seen_skus: set[str] = set()
    stats = {
        "total_rows": 0,
        "valid_rows": 0,
        "scanned_valid_products": 0,
        "skipped_resumed_products": 0,
        "downloaded_images": 0,
        "skipped_missing_image": 0,
        "skipped_missing_sales": 0,
        "skipped_duplicate_sku": 0,
        "download_failed": 0,
    }

    batch_keyword = sanitize_filename(csv_path.stem, fallback="csv_source")
    target_dir = download_root / batch_keyword
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row_index, row in enumerate(reader, start=1):
            stats["total_rows"] += 1

            image_url = str(row.get(DEFAULT_IMAGE_COLUMN) or "").strip()
            if not image_url:
                stats["skipped_missing_image"] += 1
                continue

            monthly_sales = parse_numeric(row.get(DEFAULT_SALES_COLUMN))
            if monthly_sales is None or monthly_sales <= 0:
                stats["skipped_missing_sales"] += 1
                continue

            raw_sku = str(row.get(DEFAULT_SKU_COLUMN) or "").strip()
            sku = sanitize_filename(raw_sku, fallback=f"csv_{row_index}")
            if sku in seen_skus:
                stats["skipped_duplicate_sku"] += 1
                continue

            seen_skus.add(sku)
            stats["scanned_valid_products"] += 1
            if stats["scanned_valid_products"] <= skip_valid_products:
                stats["skipped_resumed_products"] += 1
                continue

            image_path = target_dir / sku / "1.jpg"
            try:
                before_exists = image_path.exists()
                download_image(image_url, image_path)
                if not before_exists and image_path.exists():
                    stats["downloaded_images"] += 1
            except Exception as exc:
                stats["download_failed"] += 1
                print(f"[csv-1688] image download failed sku={sku} url={image_url} error={exc}", flush=True)
                continue

            stats["valid_rows"] += 1
            products.append(
                {
                    "validProductIndex": stats["scanned_valid_products"],
                    "sku": sku,
                    "name": str(row.get(DEFAULT_TITLE_COLUMN) or "").strip(),
                    "url": str(row.get(DEFAULT_URL_COLUMN) or "").strip(),
                    "imageUrl": image_url,
                    "localImagePath": str(image_path),
                    "price": parse_numeric(row.get(DEFAULT_PRICE_COLUMN)),
                    "csvPriceText": str(row.get(DEFAULT_PRICE_COLUMN) or "").strip(),
                    "csvWeightText": str(row.get(DEFAULT_WEIGHT_COLUMN) or "").strip(),
                    "monthlySales": monthly_sales,
                    "dailySales": None,
                    "batchKeyword": batch_keyword,
                    "brand": str(row.get(DEFAULT_BRAND_COLUMN) or "").strip(),
                    "category": str(row.get(DEFAULT_CATEGORY_COLUMN) or "").strip(),
                    "shop": str(row.get(DEFAULT_SHOP_COLUMN) or "").strip(),
                    "attributes": [],
                    "rawPayload": row,
                }
            )
            if max_products is not None and max_products > 0 and len(products) >= max_products:
                break

    return products, stats


def build_csv_excel_rows(
    pipeline: AlibabaImageSearchPipeline,
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """构造 CSV 主图搜图专用 Excel 行，保留原表售价和重量。"""

    rows: list[dict[str, Any]] = []
    for result in results:
        ozon_product = result["ozon_product"]
        items = result["image_search"]["items"]
        for index, item in enumerate(items, start=1):
            image_comparison = item.get("ai_image_comparison") or {}
            rows.append(
                {
                    "Ozon SKU": ozon_product.get("sku"),
                    "Ozon商品": ozon_product.get("name"),
                    "原表售价": ozon_product.get("csvPriceText"),
                    "原表重量": ozon_product.get("csvWeightText"),
                    "Ozon月销量": ozon_product.get("monthlySales"),
                    "Ozon日销量": ozon_product.get("dailySales"),
                    "Ozon属性数": len(ozon_product.get("attributes") or []),
                    "Ozon商品属性": pipeline.format_attributes(ozon_product.get("attributes")),
                    "Ozon链接": ozon_product.get("url"),
                    "Ozon主图路径": ozon_product.get("localImagePath"),
                    "1688序号": index,
                    "1688标题": item.get("title"),
                    "1688链接": item.get("detail_url"),
                    "1688价格": item.get("price"),
                    "1688价格文案": item.get("price_text"),
                    "1688单价": item.get("unit_price"),
                    "1688单价文案": item.get("unit_price_text"),
                    "1688重量文案": item.get("weight_text"),
                    "1688重量(g)": item.get("weight_grams"),
                    "1688属性数": len(item.get("attributes") or []),
                    "1688商品属性": pipeline.format_attributes(item.get("attributes")),
                    "1688卖家": item.get("seller"),
                    "GPT主图状态": image_comparison.get("status"),
                    "GPT主图是否同款": pipeline.format_bool_value(image_comparison.get("same_product")),
                    "GPT主图同款分": image_comparison.get("image_match_score"),
                    "GPT主图置信度": image_comparison.get("confidence"),
                    "GPT主图说明": image_comparison.get("summary"),
                    "详情抓取异常": item.get("detail_error"),
                }
            )
    return rows


def export_csv_excel(settings, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """导出 CSV 主图搜图 Excel。"""

    if not rows:
        return {
            "status": "skipped",
            "count": 0,
            "reason": "empty_rows",
        }

    output_dir = settings.ozon_scrape_output_path
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"alibaba1688_image_search_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
    frame = pd.DataFrame(rows)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="1688图搜图", index=False)

    workbook = load_workbook(output_path)
    sheet = workbook["1688图搜图"]
    widths = [14, 30, 12, 14, 12, 12, 10, 42, 40, 42, 10, 42, 42, 12, 16, 12, 16, 16, 14, 10, 42, 18, 12, 12, 10, 40, 12, 24]
    for column_index, width in enumerate(widths, start=1):
        sheet.column_dimensions[AlibabaImageSearchPipeline.column_letter(column_index)].width = width

    if sheet.max_row > 2:
        group_start = 2
        previous_key = tuple(sheet.cell(2, column_index).value for column_index in range(1, 11))
        for row_index in range(3, sheet.max_row + 1):
            current_key = tuple(sheet.cell(row_index, column_index).value for column_index in range(1, 11))
            if current_key != previous_key:
                merge_csv_excel_group(sheet, group_start, row_index - 1)
                group_start = row_index
                previous_key = current_key
        merge_csv_excel_group(sheet, group_start, sheet.max_row)

    workbook.save(output_path)
    return {
        "status": "saved",
        "count": len(rows),
        "path": str(output_path),
    }


def merge_csv_excel_group(sheet, start_row: int, end_row: int) -> None:
    """按 Ozon 商品分组合并 CSV 专用导出中的 Ozon 列。"""

    if end_row <= start_row:
        return

    for column_index in range(1, 11):
        sheet.merge_cells(
            start_row=start_row,
            start_column=column_index,
            end_row=end_row,
            end_column=column_index,
        )
        cell = sheet.cell(start_row, column_index)
        cell.alignment = Alignment(vertical="center", wrap_text=True)


def save_report_json(
    *,
    output_dir: Path,
    run_id: str,
    csv_path: Path,
    auth_state_path: Path,
    excel_path: str,
    stats: dict[str, Any],
    results: list[dict[str, Any]],
) -> Path:
    """保存 JSON 报告。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"alibaba1688_image_search_{run_id}.json"
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_type": "csv_main_images",
        "source_reference": str(csv_path),
        "auth_state_path": str(auth_state_path),
        "excel_path": excel_path,
        "stats": stats,
        "results": results,
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def main() -> None:
    """读取 CSV 主图，执行 1688 图搜图。"""

    args = parse_args()
    csv_path = resolve_csv_path(args.csv)
    download_root = resolve_download_root(args.download_dir)
    settings = get_settings().model_copy(
        deep=True,
        update={"alibaba1688_headless": bool(args.background)},
    )
    resume_state_path = get_resume_state_path(settings)
    if args.no_resume:
        reset_resume_checkpoint(resume_state_path, csv_path=csv_path)
    resume_checkpoint = {} if args.no_resume else get_resume_checkpoint(resume_state_path, csv_path)
    checkpoint_mtime_ns = int(resume_checkpoint.get("csv_mtime_ns") or 0)
    if resume_checkpoint and checkpoint_mtime_ns and checkpoint_mtime_ns != csv_path.stat().st_mtime_ns:
        print("[csv-1688] csv file changed, ignoring old resume checkpoint.", flush=True)
        reset_resume_checkpoint(resume_state_path, csv_path=csv_path)
        resume_checkpoint = {}
    resume_offset = int(resume_checkpoint.get("processed_valid_products") or 0)
    if args.no_resume:
        resume_offset = 0

    pipeline = AlibabaImageSearchPipeline(settings=settings)
    run_id = time.strftime("%Y%m%d_%H%M%S")
    products, load_stats = build_csv_products(
        csv_path,
        download_root,
        max_products=args.max_products,
        skip_valid_products=resume_offset,
    )
    if not products:
        if resume_offset > 0 and load_stats["scanned_valid_products"] <= resume_offset:
            print(f"[csv-1688] source csv: {csv_path}", flush=True)
            print(f"[csv-1688] resume checkpoint: already processed {resume_offset} valid products", flush=True)
            print("[csv-1688] no remaining valid products to process.", flush=True)
            return
        raise RuntimeError("CSV 中没有可用于 1688 图搜图的有效商品。")

    results: list[dict[str, Any]] = []
    completed_results: list[dict[str, Any]] = []
    image_prefilter_result = {
        "status": "skipped",
        "evaluated_items": 0,
        "passed_items": 0,
        "failed_items": 0,
        "selected_items": 0,
    }
    search_result = {
        "status": "completed",
        "failed_products": 0,
    }
    detail_result = {
        "status": "skipped",
        "enriched_items": 0,
        "failed_items": 0,
    }

    with sync_playwright() as playwright:
        session, auth_state_path = pipeline.browser_search.ensure_ready_context(playwright)
        context = session.context
        working_page = context.new_page()
        try:
            total_products = len(products)
            print(f"[csv-1688] source csv: {csv_path}", flush=True)
            print(f"[csv-1688] queued valid products: {total_products}", flush=True)
            if resume_offset > 0:
                print(
                    f"[csv-1688] resume from valid product #{resume_offset + 1} "
                    f"(already processed: {resume_offset})",
                    flush=True,
                )

            for index, product in enumerate(products, start=1):
                global_index = int(product.get("validProductIndex") or (resume_offset + index))
                print(
                    f"[csv-1688] progress: {index}/{total_products} global_valid_index={global_index} "
                    f"SKU={product.get('sku')} name={product.get('name') or 'unknown'}",
                    flush=True,
                )
                try:
                    image_result = pipeline.browser_search.search_by_uploaded_image_in_context(
                        context,
                        image_path=product["localImagePath"],
                        page=working_page,
                        max_results=args.max_results,
                        enrich_details=False,
                    )
                except Exception as exc:
                    search_result["status"] = "partial_failed"
                    search_result["failed_products"] += 1
                    print(
                        f"[csv-1688] search failed for SKU={product.get('sku')}: {exc}",
                        flush=True,
                    )
                    results.append(
                        pipeline.build_failed_search_result(product=product, error=str(exc))
                    )
                    update_resume_checkpoint(
                        resume_state_path,
                        csv_path=csv_path,
                        processed_valid_products=global_index,
                        last_completed_sku=str(product.get("sku") or ""),
                        last_run_id=run_id,
                    )
                    continue

                next_working_page = image_result.pop("_active_page", None)
                if next_working_page is not None and next_working_page is not working_page:
                    try:
                        if not working_page.is_closed():
                            working_page.close()
                    except Exception:
                        pass
                    working_page = next_working_page

                prefilter = pipeline.compare_result_images_with_ai(product, image_result["items"])
                image_prefilter_result["status"] = prefilter["status"]
                image_prefilter_result["evaluated_items"] += prefilter.get("evaluated_items", 0)
                image_prefilter_result["passed_items"] += prefilter.get("passed_items", 0)
                image_prefilter_result["failed_items"] += prefilter.get("failed_items", 0)

                best_items = pipeline.select_best_image_match_items(image_result["items"])
                image_prefilter_result["selected_items"] += len(best_items)
                if best_items:
                    try:
                        pipeline.browser_search.enrich_results_with_detail(context, best_items)
                        detail_result["status"] = "completed"
                        detail_result["enriched_items"] += len(best_items)
                    except Exception as exc:
                        detail_result["status"] = "partial_failed"
                        detail_result["failed_items"] += len(best_items)
                        for item in best_items:
                            item["detail_error"] = str(exc)

                image_result["items"] = best_items
                result_entry = {
                    "ozon_product": product,
                    "image_search": image_result,
                }
                results.append(result_entry)
                if best_items:
                    completed_results.append(result_entry)
                update_resume_checkpoint(
                    resume_state_path,
                    csv_path=csv_path,
                    processed_valid_products=global_index,
                    last_completed_sku=str(product.get("sku") or ""),
                    last_run_id=run_id,
                )
        finally:
            try:
                working_page.close()
            except Exception:
                pass
            session.close()

    excel_rows = build_csv_excel_rows(pipeline, completed_results)
    excel_result = export_csv_excel(settings, excel_rows)
    report_stats = {
        **load_stats,
        "resume_offset": resume_offset,
        "processed_products": len(completed_results),
        "processed_attempts": len(results),
        "matched_items": sum(len(result["image_search"]["items"]) for result in completed_results),
        "search_failed_products": search_result["failed_products"],
        "image_prefilter_result": image_prefilter_result,
        "detail_result": detail_result,
    }
    report_path = save_report_json(
        output_dir=settings.ozon_scrape_output_path,
        run_id=run_id,
        csv_path=csv_path,
        auth_state_path=auth_state_path,
        excel_path=str(excel_result.get("path") or ""),
        stats=report_stats,
        results=results,
    )

    print("1688 csv image search: completed")
    print(f"source_type: csv_main_images")
    print(f"source_reference: {csv_path}")
    print(f"resume_offset: {resume_offset}")
    print(f"valid_products: {load_stats['valid_rows']}")
    print(f"processed_attempts: {len(results)}")
    print(f"processed_products: {len(completed_results)}")
    print(f"matched_items: {report_stats['matched_items']}")
    print(f"downloaded_images: {load_stats['downloaded_images']}")
    print(f"search_status: {search_result['status']}")
    print(f"search_failed_products: {search_result['failed_products']}")
    print(f"image_prefilter_status: {image_prefilter_result['status']}")
    print(f"image_prefilter_items: {image_prefilter_result['evaluated_items']}")
    print(f"image_prefilter_passed: {image_prefilter_result['passed_items']}")
    print(f"image_prefilter_selected: {image_prefilter_result['selected_items']}")
    print(f"detail_status: {detail_result['status']}")
    print(f"detail_enriched_items: {detail_result['enriched_items']}")
    print(f"excel_status: {excel_result['status']}")
    if excel_result.get("path"):
        print(f"excel_path: {excel_result['path']}")
    if excel_result.get("reason"):
        print(f"excel_note: {excel_result['reason']}")
    if excel_result.get("error"):
        print(f"excel_error: {excel_result['error']}")
    print(f"json_path: {report_path}")


if __name__ == "__main__":
    main()
