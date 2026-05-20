"""Ozon 有评论跟卖店铺 SQLite 仓储。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from config.settings import Settings, get_settings
from ozon_selection.api.clients.sqlite_client import SQLiteClient


class OzonReviewedSellerRepository:
    """负责 Ozon 已处理商品和有评论店铺的持久化。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = SQLiteClient(settings=self.settings)

    @property
    def is_configured(self) -> bool:
        """判断 SQLite 是否可用。"""

        return self.client.is_configured

    def ensure_schema(self, schema_path: Path | None = None) -> None:
        """初始化 SQLite schema。"""

        sql_path = schema_path or self.settings.project_root / "docs" / "sqlite_schema.sql"
        sql = sql_path.read_text(encoding="utf-8")
        with self.client.connect() as connection:
            connection.executescript(sql)
            self._ensure_shop_tracking_columns(connection)
            connection.commit()

    def list_processed_skus(self) -> set[str]:
        """返回已处理商品 SKU 集合。"""

        if not self.is_configured:
            return set()

        self.ensure_schema()
        with self.client.connect() as connection:
            rows = connection.execute(
                """
                select source_product_id
                from ozon_reviewed_seller_products
                where trim(coalesce(source_product_id, '')) <> ''
                """
            ).fetchall()
        return {str(row["source_product_id"]).strip() for row in rows if str(row["source_product_id"]).strip()}

    def list_completed_shop_urls(self) -> set[str]:
        """返回已完成商品采集的店铺 URL 集合。"""

        if not self.is_configured:
            return set()

        self.ensure_schema()
        with self.client.connect() as connection:
            rows = connection.execute(
                """
                select seller_url
                from ozon_reviewed_seller_shops
                where crawl_status = 'completed'
                  and trim(coalesce(seller_url, '')) <> ''
                """
            ).fetchall()
        return {str(row["seller_url"]).strip() for row in rows if str(row["seller_url"]).strip()}

    def list_shops(self, *, crawl_status: str = "all", shop_type: str = "all") -> list[dict[str, Any]]:
        """按抓取状态和店铺类型读取店铺列表。"""

        if not self.is_configured:
            return []

        self.ensure_schema()

        clauses: list[str] = []
        params: list[Any] = []
        normalized_status = str(crawl_status or "all").strip().lower()
        normalized_shop_type = str(shop_type or "all").strip()
        if normalized_status and normalized_status != "all":
            clauses.append("crawl_status = ?")
            params.append(normalized_status)
        if normalized_shop_type and normalized_shop_type != "all":
            clauses.append("shop_type = ?")
            params.append(normalized_shop_type)

        where_sql = f"where {' and '.join(clauses)}" if clauses else ""
        query = f"""
            select
                id,
                seller_url,
                seller_name,
                review_count,
                review_text,
                first_source_product_id,
                first_source_product_url,
                last_source_product_id,
                last_source_product_url,
                source_count,
                crawl_status,
                crawl_product_count,
                crawl_qualified_count,
                crawl_rejected_count,
                crawl_started_at,
                crawl_completed_at,
                crawl_failed_at,
                crawl_error,
                shop_type,
                shop_type_reason,
                shop_type_sample_size,
                shop_type_primary_category_count,
                shop_type_brand_count,
                shop_type_profile,
                shop_type_checked_at,
                first_seen_at,
                last_seen_at,
                created_at,
                updated_at
            from ozon_reviewed_seller_shops
            {where_sql}
            order by review_count desc, last_seen_at desc, id desc
        """

        with self.client.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_products(self, *, query: str = "", status: str = "all") -> list[dict[str, Any]]:
        """读取店铺来源商品列表。"""

        if not self.is_configured:
            return []

        self.ensure_schema()

        clauses: list[str] = []
        params: list[Any] = []
        normalized_query = str(query or "").strip()
        normalized_status = str(status or "all").strip().lower()

        if normalized_query:
            clauses.append(
                """
                (
                    source_product_id like ? collate nocase
                    or title like ? collate nocase
                    or source_url like ? collate nocase
                    or listing_url like ? collate nocase
                )
                """
            )
            like_value = f"%{normalized_query}%"
            params.extend([like_value, like_value, like_value, like_value])
        if normalized_status and normalized_status != "all":
            clauses.append("status = ?")
            params.append(normalized_status)

        where_sql = f"where {' and '.join(clauses)}" if clauses else ""
        query_sql = f"""
            select
                id,
                source_product_id,
                title,
                source_url,
                start_url,
                listing_url,
                offer_button_text,
                seller_count,
                status,
                note,
                processed_at,
                created_at,
                updated_at
            from ozon_reviewed_seller_products
            {where_sql}
            order by processed_at desc, id desc
        """

        with self.client.connect() as connection:
            rows = connection.execute(query_sql, params).fetchall()
        return [dict(row) for row in rows]

    def delete_shops(self, seller_urls: list[str]) -> dict[str, Any]:
        """按店铺 URL 删除店铺记录。"""

        normalized_urls = [str(url or "").strip() for url in seller_urls if str(url or "").strip()]
        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured", "deleted_count": 0}
        if not normalized_urls:
            return {"status": "skipped", "reason": "empty_seller_urls", "deleted_count": 0}

        self.ensure_schema()
        placeholders = ", ".join("?" for _ in normalized_urls)
        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"delete from ozon_reviewed_seller_shops where seller_url in ({placeholders})",
                normalized_urls,
            )
            deleted_count = int(cursor.rowcount or 0)
            connection.commit()

        return {
            "status": "deleted",
            "deleted_count": deleted_count,
            "seller_url_count": len(normalized_urls),
        }

    def delete_products(self, product_ids: list[int]) -> dict[str, Any]:
        """按主键删除店铺来源商品记录。"""

        normalized_ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured", "deleted_count": 0}
        if not normalized_ids:
            return {"status": "skipped", "reason": "empty_product_ids", "deleted_count": 0}

        self.ensure_schema()
        placeholders = ", ".join("?" for _ in normalized_ids)
        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"delete from ozon_reviewed_seller_products where id in ({placeholders})",
                normalized_ids,
            )
            deleted_count = int(cursor.rowcount or 0)
            connection.commit()

        return {
            "status": "deleted",
            "deleted_count": deleted_count,
            "product_id_count": len(normalized_ids),
        }

    def save_shop_rows(self, shop_rows: list[dict[str, Any]]) -> dict[str, Any]:
        """把店铺列表写入 SQLite；已存在店铺跳过，不重复写入。"""

        if not self.is_configured:
            return {
                "status": "skipped",
                "reason": "sqlite_not_configured",
                "input_shop_count": 0,
                "saved_shop_count": 0,
                "new_shop_count": 0,
                "existing_shop_count": 0,
            }

        self.ensure_schema()
        with self.client.connect() as connection:
            result = self._insert_shop_rows(connection, shop_rows)
            connection.commit()
        return result

    def mark_shop_crawl_started(self, seller_url: str) -> dict[str, Any]:
        """标记店铺商品采集已开始。"""

        normalized_url = str(seller_url or "").strip()
        if not normalized_url:
            return {"status": "skipped", "reason": "empty_seller_url"}
        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured"}

        self.ensure_schema()
        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                update ozon_reviewed_seller_shops
                set crawl_status = 'in_progress',
                    crawl_started_at = current_timestamp,
                    crawl_failed_at = null,
                    crawl_error = null,
                    updated_at = current_timestamp
                where seller_url = ?
                """,
                (normalized_url,),
            )
            connection.commit()
        return {"status": "saved", "updated_count": int(cursor.rowcount or 0), "seller_url": normalized_url}

    def list_pending_shop_type_urls(self) -> set[str]:
        """返回尚未完成店铺类型判定的店铺 URL 集合。"""

        if not self.is_configured:
            return set()

        self.ensure_schema()
        with self.client.connect() as connection:
            rows = connection.execute(
                """
                select seller_url
                from ozon_reviewed_seller_shops
                where trim(coalesce(seller_url, '')) <> ''
                  and trim(coalesce(shop_type, '')) = ''
                """
            ).fetchall()
        return {str(row["seller_url"]).strip() for row in rows if str(row["seller_url"]).strip()}

    def mark_shop_type(
        self,
        *,
        seller_url: str,
        shop_type: str,
        reason: str,
        sample_size: int,
        primary_category_count: int,
        brand_count: int,
        profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """写入店铺类型判定结果。"""

        normalized_url = str(seller_url or "").strip()
        normalized_type = str(shop_type or "").strip()
        if not normalized_url:
            return {"status": "skipped", "reason": "empty_seller_url"}
        if not normalized_type:
            return {"status": "skipped", "reason": "empty_shop_type"}
        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured"}

        self.ensure_schema()
        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                update ozon_reviewed_seller_shops
                set
                    shop_type = ?,
                    shop_type_reason = ?,
                    shop_type_sample_size = ?,
                    shop_type_primary_category_count = ?,
                    shop_type_brand_count = ?,
                    shop_type_profile = ?,
                    shop_type_checked_at = current_timestamp,
                    updated_at = current_timestamp
                where seller_url = ?
                """,
                (
                    normalized_type,
                    str(reason or "").strip(),
                    max(int(sample_size or 0), 0),
                    max(int(primary_category_count or 0), 0),
                    max(int(brand_count or 0), 0),
                    json.dumps(profile or {}, ensure_ascii=False),
                    normalized_url,
                ),
            )
            connection.commit()
        return {"status": "saved", "updated_count": int(cursor.rowcount or 0), "seller_url": normalized_url}

    def mark_shop_crawl_completed(
        self,
        *,
        seller_url: str,
        product_count: int,
        qualified_count: int,
        rejected_count: int,
    ) -> dict[str, Any]:
        """标记店铺商品采集已完成。"""

        normalized_url = str(seller_url or "").strip()
        if not normalized_url:
            return {"status": "skipped", "reason": "empty_seller_url"}
        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured"}

        self.ensure_schema()
        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                update ozon_reviewed_seller_shops
                set crawl_status = 'completed',
                    crawl_product_count = ?,
                    crawl_qualified_count = ?,
                    crawl_rejected_count = ?,
                    crawl_completed_at = current_timestamp,
                    crawl_failed_at = null,
                    crawl_error = null,
                    updated_at = current_timestamp
                where seller_url = ?
                """,
                (
                    max(int(product_count), 0),
                    max(int(qualified_count), 0),
                    max(int(rejected_count), 0),
                    normalized_url,
                ),
            )
            connection.commit()
        return {"status": "saved", "updated_count": int(cursor.rowcount or 0), "seller_url": normalized_url}

    def mark_shop_crawl_failed(self, *, seller_url: str, error: str) -> dict[str, Any]:
        """标记店铺商品采集失败。"""

        normalized_url = str(seller_url or "").strip()
        if not normalized_url:
            return {"status": "skipped", "reason": "empty_seller_url"}
        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured"}

        self.ensure_schema()
        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                update ozon_reviewed_seller_shops
                set crawl_status = 'failed',
                    crawl_failed_at = current_timestamp,
                    crawl_error = ?,
                    updated_at = current_timestamp
                where seller_url = ?
                """,
                (str(error or "").strip(), normalized_url),
            )
            connection.commit()
        return {"status": "saved", "updated_count": int(cursor.rowcount or 0), "seller_url": normalized_url}

    def save_results(
        self,
        *,
        product_results: list[dict[str, Any]],
        start_url: str,
        listing_url: str,
    ) -> dict[str, Any]:
        """保存本次处理的商品和去重后的店铺。"""

        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured", "saved_product_count": 0, "saved_shop_count": 0}
        if not product_results:
            return {"status": "skipped", "reason": "empty_results", "saved_product_count": 0, "saved_shop_count": 0}

        self.ensure_schema()
        shop_payloads = self._collect_unique_shops(product_results)

        with self.client.connect() as connection:
            shop_save_result = self._insert_shop_rows(connection, shop_payloads)
            cursor = connection.cursor()
            for product in product_results:
                sellers = product.get("reviewed_sellers") or []
                cursor.execute(
                    """
                    insert into ozon_reviewed_seller_products (
                        source_product_id,
                        title,
                        source_url,
                        start_url,
                        listing_url,
                        offer_button_text,
                        seller_count,
                        status,
                        note,
                        raw_payload,
                        processed_at
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
                    on conflict(source_product_id) do update set
                        title = excluded.title,
                        source_url = excluded.source_url,
                        start_url = excluded.start_url,
                        listing_url = excluded.listing_url,
                        offer_button_text = excluded.offer_button_text,
                        seller_count = excluded.seller_count,
                        status = excluded.status,
                        note = excluded.note,
                        raw_payload = excluded.raw_payload,
                        processed_at = current_timestamp,
                        updated_at = current_timestamp
                    """,
                    (
                        product.get("product_sku"),
                        product.get("product_title"),
                        product.get("product_url"),
                        start_url,
                        listing_url,
                        product.get("offer_button_text"),
                        len(sellers),
                        "processed" if not product.get("skipped_reason") else "processed_with_note",
                        product.get("skipped_reason") or "",
                        json.dumps(product, ensure_ascii=False),
                    ),
                )

            connection.commit()

        return {
            "status": "saved",
            "saved_product_count": len(product_results),
            "input_shop_count": int(shop_save_result.get("input_shop_count") or 0),
            "saved_shop_count": int(shop_save_result.get("saved_shop_count") or 0),
            "new_shop_count": int(shop_save_result.get("new_shop_count") or 0),
            "existing_shop_count": int(shop_save_result.get("existing_shop_count") or 0),
        }

    def _insert_shop_rows(
        self,
        connection: sqlite3.Connection,
        shop_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """向店铺表插入新店铺；已存在 URL 直接跳过。"""

        normalized_rows = self._normalize_shop_rows(shop_rows)
        if not normalized_rows:
            return {
                "status": "skipped",
                "reason": "empty_shop_rows",
                "input_shop_count": 0,
                "saved_shop_count": 0,
                "new_shop_count": 0,
                "existing_shop_count": 0,
            }

        existing_shop_urls = self._list_existing_shop_urls(
            connection,
            [payload["seller_url"] for payload in normalized_rows],
        )
        cursor = connection.cursor()
        saved_shop_count = 0

        for payload in normalized_rows:
            cursor.execute(
                """
                insert into ozon_reviewed_seller_shops (
                    seller_url,
                    seller_name,
                    review_count,
                    review_text,
                    first_source_product_id,
                    first_source_product_url,
                    last_source_product_id,
                    last_source_product_url,
                    source_count,
                    raw_payload,
                    first_seen_at,
                    last_seen_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, current_timestamp, current_timestamp)
                on conflict(seller_url) do nothing
                """,
                (
                    payload.get("seller_url"),
                    payload.get("seller_name"),
                    payload.get("review_count", 0),
                    payload.get("review_text"),
                    payload.get("product_sku"),
                    payload.get("product_url"),
                    payload.get("product_sku"),
                    payload.get("product_url"),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            if int(cursor.rowcount or 0) > 0:
                saved_shop_count += 1

        return {
            "status": "saved",
            "input_shop_count": len(normalized_rows),
            "saved_shop_count": saved_shop_count,
            "new_shop_count": saved_shop_count,
            "existing_shop_count": len(normalized_rows) - saved_shop_count,
            "existing_shop_urls": sorted(existing_shop_urls),
        }

    @staticmethod
    def _list_existing_shop_urls(connection: sqlite3.Connection, seller_urls: list[str]) -> set[str]:
        """查询已存在的店铺 URL。"""

        normalized_urls = [str(url or "").strip() for url in seller_urls if str(url or "").strip()]
        if not normalized_urls:
            return set()

        query = "select seller_url from ozon_reviewed_seller_shops where seller_url in (%s)" % ",".join(
            "?" for _ in normalized_urls
        )
        rows = connection.execute(query, normalized_urls).fetchall()
        return {str(row["seller_url"]).strip() for row in rows if str(row["seller_url"]).strip()}

    @staticmethod
    def _normalize_shop_rows(shop_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """规范化店铺记录，保留唯一 seller_url。"""

        deduped: dict[str, dict[str, Any]] = {}
        for row in shop_rows:
            seller_url = str(row.get("seller_url") or row.get("店铺URL") or "").strip()
            if not seller_url:
                continue
            payload = {
                "seller_url": seller_url,
                "seller_name": str(row.get("seller_name") or row.get("店铺名") or "").strip(),
                "review_count": OzonReviewedSellerRepository._to_int(row.get("review_count") or row.get("评论数")),
                "review_text": str(row.get("review_text") or row.get("评论文本") or "").strip(),
                "product_sku": str(
                    row.get("product_sku") or row.get("source_product_sku") or row.get("商品SKU") or ""
                ).strip(),
                "product_url": str(
                    row.get("product_url") or row.get("source_product_url") or row.get("商品URL") or ""
                ).strip(),
            }
            existing = deduped.get(seller_url)
            if existing is None or int(payload["review_count"]) >= int(existing.get("review_count") or 0):
                deduped[seller_url] = payload
        return list(deduped.values())

    @staticmethod
    def _to_int(value: Any) -> int:
        """把任意数字输入安全转换成 int。"""

        if value is None:
            return 0
        text = str(value).strip()
        if not text or text.lower() == "nan":
            return 0
        try:
            return int(float(text))
        except Exception:
            return 0

    @staticmethod
    def _ensure_shop_tracking_columns(connection: sqlite3.Connection) -> None:
        """为历史数据库补齐店铺抓取状态列。"""

        columns = {
            str(row["name"]).strip()
            for row in connection.execute("pragma table_info(ozon_reviewed_seller_shops)").fetchall()
        }
        alter_statements: list[str] = []

        if "crawl_status" not in columns:
            alter_statements.append(
                "alter table ozon_reviewed_seller_shops add column crawl_status text not null default 'pending'"
            )
        if "crawl_product_count" not in columns:
            alter_statements.append(
                "alter table ozon_reviewed_seller_shops add column crawl_product_count integer not null default 0"
            )
        if "crawl_qualified_count" not in columns:
            alter_statements.append(
                "alter table ozon_reviewed_seller_shops add column crawl_qualified_count integer not null default 0"
            )
        if "crawl_rejected_count" not in columns:
            alter_statements.append(
                "alter table ozon_reviewed_seller_shops add column crawl_rejected_count integer not null default 0"
            )
        if "crawl_started_at" not in columns:
            alter_statements.append("alter table ozon_reviewed_seller_shops add column crawl_started_at text")
        if "crawl_completed_at" not in columns:
            alter_statements.append("alter table ozon_reviewed_seller_shops add column crawl_completed_at text")
        if "crawl_failed_at" not in columns:
            alter_statements.append("alter table ozon_reviewed_seller_shops add column crawl_failed_at text")
        if "crawl_error" not in columns:
            alter_statements.append("alter table ozon_reviewed_seller_shops add column crawl_error text")
        if "shop_type" not in columns:
            alter_statements.append("alter table ozon_reviewed_seller_shops add column shop_type text not null default ''")
        if "shop_type_reason" not in columns:
            alter_statements.append("alter table ozon_reviewed_seller_shops add column shop_type_reason text")
        if "shop_type_sample_size" not in columns:
            alter_statements.append(
                "alter table ozon_reviewed_seller_shops add column shop_type_sample_size integer not null default 0"
            )
        if "shop_type_primary_category_count" not in columns:
            alter_statements.append(
                "alter table ozon_reviewed_seller_shops add column shop_type_primary_category_count integer not null default 0"
            )
        if "shop_type_brand_count" not in columns:
            alter_statements.append(
                "alter table ozon_reviewed_seller_shops add column shop_type_brand_count integer not null default 0"
            )
        if "shop_type_profile" not in columns:
            alter_statements.append("alter table ozon_reviewed_seller_shops add column shop_type_profile text")
        if "shop_type_checked_at" not in columns:
            alter_statements.append("alter table ozon_reviewed_seller_shops add column shop_type_checked_at text")

        for statement in alter_statements:
            connection.execute(statement)

        connection.execute(
            "create index if not exists idx_ozon_reviewed_seller_shops_crawl_status on ozon_reviewed_seller_shops (crawl_status)"
        )
        connection.execute(
            "create index if not exists idx_ozon_reviewed_seller_shops_shop_type on ozon_reviewed_seller_shops (shop_type)"
        )

    @staticmethod
    def _collect_unique_shops(product_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """汇总本次结果中的唯一店铺。"""

        unique: dict[str, dict[str, Any]] = {}
        for product in product_results:
            product_sku = str(product.get("product_sku") or "").strip()
            product_url = str(product.get("product_url") or "").strip()
            for seller in product.get("reviewed_sellers") or []:
                seller_url = str(seller.get("seller_url") or "").strip()
                if not seller_url:
                    continue
                payload = {
                    "seller_url": seller_url,
                    "seller_name": seller.get("seller_name", ""),
                    "review_count": int(seller.get("review_count") or 0),
                    "review_text": seller.get("review_text", ""),
                    "product_sku": product_sku,
                    "product_url": product_url,
                }
                existing = unique.get(seller_url)
                if existing is None or int(payload["review_count"]) >= int(existing.get("review_count") or 0):
                    unique[seller_url] = payload
        return list(unique.values())
