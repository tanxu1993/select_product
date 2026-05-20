"""Ozon 候选清单导入 SQLite 的服务。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import time
from typing import Any

from config.settings import Settings, get_settings
from ozon_selection.repositories.ozon_batch_repository import OzonBatchRepository
from ozon_selection.services.ozon_candidate_pipeline import OzonCandidatePipeline


class OzonBatchImporter:
    """把 Ozon 候选清单导入 SQLite。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        repository: OzonBatchRepository | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.repository = repository or OzonBatchRepository(settings=self.settings)

    def import_manifest(self, manifest_path: str | Path | None = None) -> dict[str, Any]:
        """导入指定或最新的 Ozon 候选清单。"""

        output_dir = self.settings.ozon_scrape_output_path
        path = Path(manifest_path) if manifest_path else OzonCandidatePipeline.find_latest_manifest(output_dir)
        payload = json.loads(path.read_text(encoding="utf-8"))
        products = payload.get("products") or []

        batch = {
            "keyword": payload.get("keyword") or "",
            "source_manifest_path": str(path),
            "source_excel_path": payload.get("excel_path"),
            "search_url": payload.get("search_url"),
            "generated_at": payload.get("generated_at"),
            "status": "pending",
            "total_products": len(products),
        }

        product_payloads = [self.build_product_payload(product) for product in products]
        result = self.repository.upsert_batch_with_products(batch=batch, products=product_payloads)
        return {
            **result,
            "manifest_path": str(path),
            "keyword": batch["keyword"],
            "total_products": len(product_payloads),
        }

    def import_products(
        self,
        *,
        keyword: str,
        search_url: str,
        products: list[dict[str, Any]],
        generated_at: str | None = None,
        source_ref: str | None = None,
    ) -> dict[str, Any]:
        """把采集到的 Ozon 商品直接写入 SQLite，不依赖 JSON manifest。"""

        if not products:
            return {
                "status": "skipped",
                "reason": "empty_products",
                "keyword": keyword,
                "total_products": 0,
            }

        batch = {
            "keyword": keyword,
            "source_manifest_path": source_ref or self.build_runtime_source_ref(keyword),
            "source_excel_path": None,
            "search_url": search_url,
            "generated_at": generated_at or time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "pending",
            "total_products": len(products),
        }
        product_payloads = [self.build_product_payload(product) for product in products]
        result = self.repository.upsert_batch_with_products(batch=batch, products=product_payloads)
        return {
            **result,
            "keyword": keyword,
            "total_products": len(product_payloads),
            "source_ref": batch["source_manifest_path"],
        }

    @staticmethod
    def build_runtime_source_ref(keyword: str) -> str:
        """为直接入库模式生成批次唯一标识。"""

        safe_keyword = re.sub(r"\s+", "_", keyword).strip("_") or "ozon"
        return f"sqlite://ozon_collect/{safe_keyword}/{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns()}"

    @staticmethod
    def build_product_payload(product: dict[str, Any]) -> dict[str, Any]:
        """把 manifest 商品映射成 SQLite 记录。"""

        return {
            "source_product_id": str(product.get("sku") or ""),
            "title": product.get("name"),
            "detail_title": product.get("detailTitle"),
            "source_url": product.get("url"),
            "image_url": product.get("imageUrl"),
            "image_path": product.get("localImagePath"),
            "detail_image_url": product.get("detailImageUrl"),
            "attributes": product.get("attributes") or [],
            "price": product.get("price"),
            "detail_price": product.get("detailPrice"),
            "category": product.get("category"),
            "brand": product.get("brand"),
            "monthly_sales": product.get("monthlySales"),
            "daily_sales": product.get("dailySales"),
            "growth_rate": product.get("growthRate"),
            "return_rate": product.get("returnRate"),
            "conversion_rate": product.get("conversionRate"),
            "ctr": product.get("ctr"),
            "cart_add_rate": product.get("cartAddRate"),
            "search_views": product.get("searchViews"),
            "ad_share": product.get("adShare"),
            "promotion_days": product.get("promotionDays"),
            "weight_grams": product.get("weight"),
            "shipping_mode": product.get("shippingMode"),
            "sellers": product.get("sellers"),
            "lowest_competitor": product.get("lowestCompetitor"),
            "listed_days": product.get("listedDays"),
            "avg_price": product.get("avgPrice"),
            "score": product.get("score"),
            "passed": bool(product.get("passed", True)),
            "warnings": product.get("warnings") or [],
            "fail_reasons": product.get("failReasons") or [],
            "delivery_info": product.get("deliveryInfo"),
            "return_info": product.get("returnInfo"),
            "warehouse_info": product.get("warehouseInfo"),
            "is_russian_local_warehouse": product.get("isRussianLocalWarehouse"),
            "raw_payload": product,
        }
