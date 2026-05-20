"""Ozon 主图 -> 1688 图搜图联调脚本。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings
from ozon_selection.api.clients.api1688_client import Api1688Client
from ozon_selection.collectors.ozon.product_collector import ProductCollector
from scripts.test_ozon_openai_parse import collect_one_product


def main() -> None:
    """抓取一个 Ozon 商品，并用主图做 1688 图搜图。"""

    settings = get_settings()
    output_dir = settings.product_parser_test_output_path
    output_dir.mkdir(parents=True, exist_ok=True)

    collector = ProductCollector(settings=settings)
    client = Api1688Client(settings=settings)

    with sync_playwright() as playwright:
        product = collect_one_product(collector, playwright)

    image_url = product.get("imageUrl")
    if not image_url:
        raise RuntimeError("未获取到 Ozon 商品主图 URL。")

    image_bytes = download_image_bytes(image_url, settings)
    result = client.search_products_by_image(
        image_url=image_url,
        image_bytes=image_bytes,
    )

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
    }

    output_path = output_dir / f"ozon_1688_image_search_{product.get('sku', 'unknown')}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Ozon product: {product.get('name')}")
    print(f"Ozon image: {image_url}")
    print(f"1688 hits: {len(result['items'])}")
    if result["items"]:
        print("Top 3 1688 matches:")
        for index, item in enumerate(result["items"][:3], start=1):
            print(f"{index}. {item.get('title')} | {item.get('price')} | {item.get('detail_url')}")
    print(f"Saved result to: {output_path}")


def download_image_bytes(image_url: str, settings) -> bytes:
    """下载 Ozon 主图，供需要 base64 传图的 1688 API 使用。"""

    headers = {
        "User-Agent": settings.product_parser_image_user_agent or settings.ozon_user_agent,
        "Referer": settings.product_parser_image_referer or settings.ozon_base_url,
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": settings.product_parser_image_accept_language,
    }
    headers = {key: value for key, value in headers.items() if value}

    with httpx.Client(
        timeout=settings.product_parser_image_download_timeout_seconds,
        follow_redirects=True,
        headers=headers,
    ) as client:
        response = client.get(image_url)
        response.raise_for_status()
        return response.content


if __name__ == "__main__":
    main()
