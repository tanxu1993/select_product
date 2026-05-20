"""Ozon 关键词批次与人工去重仓储。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from config.settings import Settings, get_settings
from ozon_selection.api.clients.sqlite_client import SQLiteClient


class OzonBatchRepository:
    """负责 SQLite 中的 Ozon 批次与人工去重数据。"""

    PRODUCT_JSON_FIELDS = {"attributes", "warnings", "fail_reasons", "raw_payload"}

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
            self._ensure_alibaba_processed_columns(connection)
            connection.commit()

    def upsert_batch_with_products(
        self,
        *,
        batch: dict[str, Any],
        products: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """写入一个 Ozon 关键词批次和对应商品。"""

        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured"}

        self.ensure_schema()

        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                insert into ozon_keyword_batches (
                    keyword, source_manifest_path, source_excel_path, search_url, generated_at, status, total_products
                )
                values (?, ?, ?, ?, ?, ?, ?)
                on conflict(source_manifest_path) do update set
                    keyword = excluded.keyword,
                    source_excel_path = excluded.source_excel_path,
                    search_url = excluded.search_url,
                    generated_at = excluded.generated_at,
                    total_products = excluded.total_products,
                    updated_at = current_timestamp
                """,
                (
                    batch["keyword"],
                    batch["source_manifest_path"],
                    batch.get("source_excel_path"),
                    batch.get("search_url"),
                    batch.get("generated_at"),
                    batch.get("status", "pending"),
                    batch.get("total_products", len(products)),
                ),
            )
            cursor.execute(
                "select id from ozon_keyword_batches where source_manifest_path = ?",
                (batch["source_manifest_path"],),
            )
            batch_id = int(cursor.fetchone()["id"])

            for product in products:
                cursor.execute(
                    """
                    insert into ozon_batch_products (
                        batch_id,
                        source_product_id,
                        title,
                        detail_title,
                        source_url,
                        image_url,
                        image_path,
                        detail_image_url,
                        attributes,
                        price,
                        detail_price,
                        category,
                        brand,
                        monthly_sales,
                        daily_sales,
                        growth_rate,
                        return_rate,
                        conversion_rate,
                        ctr,
                        cart_add_rate,
                        search_views,
                        ad_share,
                        promotion_days,
                        weight_grams,
                        shipping_mode,
                        sellers,
                        lowest_competitor,
                        listed_days,
                        avg_price,
                        score,
                        passed,
                        warnings,
                        fail_reasons,
                        delivery_info,
                        return_info,
                        warehouse_info,
                        is_russian_local_warehouse,
                        raw_payload
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(batch_id, source_product_id) do update set
                        title = excluded.title,
                        detail_title = excluded.detail_title,
                        source_url = excluded.source_url,
                        image_url = excluded.image_url,
                        image_path = excluded.image_path,
                        detail_image_url = excluded.detail_image_url,
                        attributes = excluded.attributes,
                        price = excluded.price,
                        detail_price = excluded.detail_price,
                        category = excluded.category,
                        brand = excluded.brand,
                        monthly_sales = excluded.monthly_sales,
                        daily_sales = excluded.daily_sales,
                        growth_rate = excluded.growth_rate,
                        return_rate = excluded.return_rate,
                        conversion_rate = excluded.conversion_rate,
                        ctr = excluded.ctr,
                        cart_add_rate = excluded.cart_add_rate,
                        search_views = excluded.search_views,
                        ad_share = excluded.ad_share,
                        promotion_days = excluded.promotion_days,
                        weight_grams = excluded.weight_grams,
                        shipping_mode = excluded.shipping_mode,
                        sellers = excluded.sellers,
                        lowest_competitor = excluded.lowest_competitor,
                        listed_days = excluded.listed_days,
                        avg_price = excluded.avg_price,
                        score = excluded.score,
                        passed = excluded.passed,
                        warnings = excluded.warnings,
                        fail_reasons = excluded.fail_reasons,
                        delivery_info = excluded.delivery_info,
                        return_info = excluded.return_info,
                        warehouse_info = excluded.warehouse_info,
                        is_russian_local_warehouse = excluded.is_russian_local_warehouse,
                        raw_payload = excluded.raw_payload,
                        updated_at = current_timestamp
                    """,
                    (
                        batch_id,
                        product.get("source_product_id"),
                        product.get("title"),
                        product.get("detail_title"),
                        product.get("source_url"),
                        product.get("image_url"),
                        product.get("image_path"),
                        product.get("detail_image_url"),
                        json.dumps(product.get("attributes") or [], ensure_ascii=False),
                        product.get("price"),
                        product.get("detail_price"),
                        product.get("category"),
                        product.get("brand"),
                        product.get("monthly_sales"),
                        product.get("daily_sales"),
                        product.get("growth_rate"),
                        product.get("return_rate"),
                        product.get("conversion_rate"),
                        product.get("ctr"),
                        product.get("cart_add_rate"),
                        product.get("search_views"),
                        product.get("ad_share"),
                        product.get("promotion_days"),
                        product.get("weight_grams"),
                        product.get("shipping_mode"),
                        product.get("sellers"),
                        product.get("lowest_competitor"),
                        product.get("listed_days"),
                        product.get("avg_price"),
                        product.get("score"),
                        1 if product.get("passed", True) else 0,
                        json.dumps(product.get("warnings") or [], ensure_ascii=False),
                        json.dumps(product.get("fail_reasons") or [], ensure_ascii=False),
                        product.get("delivery_info"),
                        product.get("return_info"),
                        product.get("warehouse_info"),
                        None if product.get("is_russian_local_warehouse") is None else int(bool(product.get("is_russian_local_warehouse"))),
                        json.dumps(product.get("raw_payload") or {}, ensure_ascii=False),
                    ),
                )
            connection.commit()

        return {"status": "saved", "batch_id": batch_id, "count": len(products)}

    def list_batches(self, *, keyword: str = "", include_completed: bool = True) -> list[dict[str, Any]]:
        """列出批次。"""

        if not self.is_configured:
            return []

        self.ensure_schema()

        clauses = []
        params: list[Any] = []
        if keyword.strip():
            clauses.append("keyword like ? collate nocase")
            params.append(f"%{keyword.strip()}%")
        if not include_completed:
            clauses.append("status <> 'completed'")

        where_sql = f"where {' and '.join(clauses)}" if clauses else ""
        query = f"""
            select
                id,
                keyword,
                source_manifest_path,
                source_excel_path,
                search_url,
                generated_at,
                status,
                total_products,
                dedupe_kept_count,
                dedupe_completed_at,
                created_at,
                updated_at
            from ozon_keyword_batches
            {where_sql}
            order by generated_at desc, id desc
        """

        with self.client.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_batch_products(self, batch_id: int) -> list[dict[str, Any]]:
        """读取批次商品。"""

        if not self.is_configured:
            return []

        self.ensure_schema()

        with self.client.connect() as connection:
            rows = connection.execute(
                """
                select
                    id,
                    batch_id,
                    source_product_id,
                    title,
                    detail_title,
                    source_url,
                    image_url,
                    image_path,
                    detail_image_url,
                    attributes,
                    price,
                    detail_price,
                    category,
                    brand,
                    monthly_sales,
                    daily_sales,
                    growth_rate,
                    return_rate,
                    conversion_rate,
                    ctr,
                    cart_add_rate,
                    search_views,
                    ad_share,
                    promotion_days,
                    weight_grams,
                    shipping_mode,
                    sellers,
                    lowest_competitor,
                    listed_days,
                    avg_price,
                    score,
                    passed,
                    warnings,
                    fail_reasons,
                    delivery_info,
                    return_info,
                    warehouse_info,
                    is_russian_local_warehouse,
                    manual_dedupe_selected,
                    created_at,
                    updated_at
                from ozon_batch_products
                where batch_id = ?
                order by score desc, source_product_id asc
                """,
                (batch_id,),
            ).fetchall()
        return [self._decode_product_row(row) for row in rows]

    def list_completed_products(self, *, keyword: str = "", include_alibaba_processed: bool = False) -> list[dict[str, Any]]:
        """读取已人工去重完成后的商品。"""

        if not self.is_configured:
            return []

        self.ensure_schema()

        params: list[Any] = []
        keyword_clause = ""
        if keyword.strip():
            keyword_clause = "and b.keyword like ? collate nocase"
            params.append(f"%{keyword.strip()}%")
        alibaba_clause = ""
        if not include_alibaba_processed:
            alibaba_clause = "and coalesce(p.alibaba_processed, 0) = 0"

        query = f"""
            select
                p.id,
                p.batch_id,
                p.source_product_id,
                p.title,
                p.detail_title,
                p.source_url,
                p.image_url,
                p.image_path,
                p.detail_image_url,
                p.attributes,
                p.price,
                p.detail_price,
                p.category,
                p.brand,
                p.monthly_sales,
                p.daily_sales,
                p.growth_rate,
                p.return_rate,
                p.conversion_rate,
                p.ctr,
                p.cart_add_rate,
                p.search_views,
                p.ad_share,
                p.promotion_days,
                p.weight_grams,
                p.shipping_mode,
                p.sellers,
                p.lowest_competitor,
                p.listed_days,
                p.avg_price,
                p.score,
                p.passed,
                p.warnings,
                p.fail_reasons,
                p.delivery_info,
                p.return_info,
                p.warehouse_info,
                p.is_russian_local_warehouse,
                p.raw_payload,
                p.manual_dedupe_selected,
                p.alibaba_processed,
                p.alibaba_processed_at,
                p.created_at,
                p.updated_at,
                b.keyword as batch_keyword,
                b.source_manifest_path as batch_manifest_path,
                b.dedupe_completed_at as batch_dedupe_completed_at
            from ozon_batch_products p
            inner join ozon_keyword_batches b on b.id = p.batch_id
            where b.status = 'completed'
              and coalesce(p.passed, 0) = 1
              and coalesce(p.manual_dedupe_selected, 1) = 1
            {keyword_clause}
            {alibaba_clause}
            order by b.dedupe_completed_at desc, b.id desc, p.score desc, p.source_product_id asc
        """

        with self.client.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._decode_product_row(row) for row in rows]

    def list_products_for_management(
        self,
        *,
        keyword: str = "",
        review_status: str = "all",
        batch_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """按关键词、审核状态和批次过滤商品，供管理页查询与导出。"""

        if not self.is_configured:
            return []

        self.ensure_schema()

        clauses = []
        params: list[Any] = []

        if keyword.strip():
            clauses.append("b.keyword like ? collate nocase")
            params.append(f"%{keyword.strip()}%")
        if review_status == "completed":
            clauses.append("b.status = 'completed'")
        elif review_status == "pending":
            clauses.append("b.status <> 'completed'")
        if batch_id is not None:
            clauses.append("b.id = ?")
            params.append(batch_id)

        where_sql = f"where {' and '.join(clauses)}" if clauses else ""
        query = f"""
            select
                p.id,
                p.batch_id,
                p.source_product_id,
                p.title,
                p.detail_title,
                p.source_url,
                p.image_url,
                p.image_path,
                p.detail_image_url,
                p.attributes,
                p.price,
                p.detail_price,
                p.category,
                p.brand,
                p.monthly_sales,
                p.daily_sales,
                p.growth_rate,
                p.return_rate,
                p.conversion_rate,
                p.ctr,
                p.cart_add_rate,
                p.search_views,
                p.ad_share,
                p.promotion_days,
                p.weight_grams,
                p.shipping_mode,
                p.sellers,
                p.lowest_competitor,
                p.listed_days,
                p.avg_price,
                p.score,
                p.passed,
                p.warnings,
                p.fail_reasons,
                p.delivery_info,
                p.return_info,
                p.warehouse_info,
                p.is_russian_local_warehouse,
                p.manual_dedupe_selected,
                p.alibaba_processed,
                p.alibaba_processed_at,
                p.created_at,
                p.updated_at,
                b.keyword as batch_keyword,
                b.status as batch_status,
                b.generated_at as batch_generated_at,
                b.dedupe_completed_at as batch_dedupe_completed_at
            from ozon_batch_products p
            inner join ozon_keyword_batches b on b.id = p.batch_id
            {where_sql}
            order by b.generated_at desc, b.id desc, p.score desc, p.source_product_id asc
        """

        with self.client.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._decode_product_row(row) for row in rows]

    def apply_manual_dedupe(self, *, batch_id: int, keep_product_ids: list[int]) -> dict[str, Any]:
        """保留选中商品，删除其他商品，并标记批次完成。"""

        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured"}

        self.ensure_schema()

        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "update ozon_batch_products set manual_dedupe_selected = 0 where batch_id = ?",
                (batch_id,),
            )

            if keep_product_ids:
                placeholders = ", ".join("?" for _ in keep_product_ids)
                cursor.execute(
                    f"""
                    update ozon_batch_products
                    set manual_dedupe_selected = 1
                    where batch_id = ? and id in ({placeholders})
                    """,
                    [batch_id, *keep_product_ids],
                )
                cursor.execute(
                    f"""
                    delete from ozon_batch_products
                    where batch_id = ? and id not in ({placeholders})
                    """,
                    [batch_id, *keep_product_ids],
                )
                deleted_count = int(cursor.rowcount or 0)
            else:
                cursor.execute(
                    "delete from ozon_batch_products where batch_id = ?",
                    (batch_id,),
                )
                deleted_count = int(cursor.rowcount or 0)

            cursor.execute(
                """
                update ozon_keyword_batches
                set
                    status = 'completed',
                    total_products = ?,
                    dedupe_kept_count = ?,
                    dedupe_completed_at = current_timestamp,
                    updated_at = current_timestamp
                where id = ?
                """,
                (len(keep_product_ids), len(keep_product_ids), batch_id),
            )
            connection.commit()

        return {
            "status": "completed",
            "batch_id": batch_id,
            "kept_count": len(keep_product_ids),
            "deleted_count": deleted_count,
        }

    def mark_products_alibaba_processed(self, product_ids: list[int]) -> dict[str, Any]:
        """把已完成 1688 搜图的 Ozon 商品标记为已处理。"""

        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured"}

        normalized_ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        if not normalized_ids:
            return {"status": "skipped", "reason": "empty_product_ids", "updated_count": 0}

        self.ensure_schema()

        placeholders = ", ".join("?" for _ in normalized_ids)
        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"""
                update ozon_batch_products
                set
                    alibaba_processed = 1,
                    alibaba_processed_at = current_timestamp,
                    updated_at = current_timestamp
                where id in ({placeholders})
                """,
                normalized_ids,
            )
            updated_count = cursor.rowcount
            connection.commit()

        return {
            "status": "updated",
            "updated_count": updated_count,
        }

    def delete_batch(self, batch_id: int) -> dict[str, Any]:
        """删除整个关键词批次及其所有商品。"""

        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured"}

        self.ensure_schema()

        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute("select count(*) as product_count from ozon_batch_products where batch_id = ?", (batch_id,))
            row = cursor.fetchone()
            product_count = int((row["product_count"] if row else 0) or 0)
            cursor.execute("delete from ozon_keyword_batches where id = ?", (batch_id,))
            deleted_batches = cursor.rowcount
            connection.commit()

        return {
            "status": "deleted",
            "batch_id": batch_id,
            "deleted_batches": deleted_batches,
            "deleted_products": product_count,
        }

    def delete_batches(self, batch_ids: list[int]) -> dict[str, Any]:
        """批量删除关键词批次及其所有商品。"""

        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured"}

        normalized_ids = sorted({int(batch_id) for batch_id in batch_ids if int(batch_id) > 0})
        if not normalized_ids:
            return {"status": "skipped", "reason": "empty_batch_ids", "deleted_batches": 0, "deleted_products": 0}

        self.ensure_schema()

        placeholders = ", ".join("?" for _ in normalized_ids)
        with self.client.connect() as connection:
            cursor = connection.cursor()
            row = cursor.execute(
                f"select count(*) as product_count from ozon_batch_products where batch_id in ({placeholders})",
                normalized_ids,
            ).fetchone()
            deleted_products = int((row["product_count"] if row else 0) or 0)
            cursor.execute(
                f"delete from ozon_keyword_batches where id in ({placeholders})",
                normalized_ids,
            )
            deleted_batches = int(cursor.rowcount or 0)
            connection.commit()

        return {
            "status": "deleted",
            "deleted_batches": deleted_batches,
            "deleted_products": deleted_products,
            "batch_id_count": len(normalized_ids),
        }

    def delete_batches_by_keyword(self, keyword: str) -> dict[str, Any]:
        """按关键词过滤删除批次及其商品。"""

        normalized_keyword = keyword.strip()
        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured"}
        if not normalized_keyword:
            return {"status": "skipped", "reason": "empty_keyword"}

        self.ensure_schema()

        with self.client.connect() as connection:
            cursor = connection.cursor()
            batch_rows = cursor.execute(
                """
                select id
                from ozon_keyword_batches
                where keyword like ? collate nocase
                """,
                (f"%{normalized_keyword}%",),
            ).fetchall()
            batch_ids = [int(row["id"]) for row in batch_rows]
            if not batch_ids:
                return {
                    "status": "skipped",
                    "reason": "no_matching_batches",
                    "keyword": normalized_keyword,
                    "deleted_batches": 0,
                    "deleted_products": 0,
                }

            placeholders = ", ".join("?" for _ in batch_ids)
            product_row = cursor.execute(
                f"select count(*) as product_count from ozon_batch_products where batch_id in ({placeholders})",
                batch_ids,
            ).fetchone()
            deleted_products = int((product_row["product_count"] if product_row else 0) or 0)
            cursor.execute(
                f"delete from ozon_keyword_batches where id in ({placeholders})",
                batch_ids,
            )
            deleted_batches = cursor.rowcount
            connection.commit()

        return {
            "status": "deleted",
            "keyword": normalized_keyword,
            "deleted_batches": deleted_batches,
            "deleted_products": deleted_products,
        }

    def delete_products(self, product_ids: list[int]) -> dict[str, Any]:
        """删除指定商品，并同步刷新批次统计。"""

        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured"}
        if not product_ids:
            return {"status": "skipped", "reason": "empty_product_ids"}

        self.ensure_schema()

        placeholders = ", ".join("?" for _ in product_ids)
        with self.client.connect() as connection:
            cursor = connection.cursor()
            batch_rows = cursor.execute(
                f"select distinct batch_id from ozon_batch_products where id in ({placeholders})",
                product_ids,
            ).fetchall()
            batch_ids = [int(row["batch_id"]) for row in batch_rows]
            cursor.execute(
                f"delete from ozon_batch_products where id in ({placeholders})",
                product_ids,
            )
            deleted_count = cursor.rowcount
            self._refresh_batch_statistics(connection, batch_ids)
            connection.commit()

        return {
            "status": "deleted",
            "deleted_count": deleted_count,
            "affected_batch_ids": batch_ids,
        }

    def _refresh_batch_statistics(self, connection: sqlite3.Connection, batch_ids: list[int]) -> None:
        """根据当前商品数量刷新批次统计，并清理空批次。"""

        if not batch_ids:
            return

        cursor = connection.cursor()
        placeholders = ", ".join("?" for _ in batch_ids)
        rows = cursor.execute(
            f"""
            select
                b.id,
                b.status,
                count(p.id) as product_count
            from ozon_keyword_batches b
            left join ozon_batch_products p on p.batch_id = b.id
            where b.id in ({placeholders})
            group by b.id, b.status
            """,
            batch_ids,
        ).fetchall()

        for row in rows:
            current_batch_id = int(row["id"])
            product_count = int(row["product_count"] or 0)
            if product_count <= 0:
                cursor.execute("delete from ozon_keyword_batches where id = ?", (current_batch_id,))
                continue

            dedupe_kept_count = product_count if row["status"] == "completed" else None
            cursor.execute(
                """
                update ozon_keyword_batches
                set
                    total_products = ?,
                    dedupe_kept_count = ?,
                    updated_at = current_timestamp
                where id = ?
                """,
                (product_count, dedupe_kept_count, current_batch_id),
            )

    @staticmethod
    def _ensure_alibaba_processed_columns(connection: sqlite3.Connection) -> None:
        """兼容旧 SQLite：补齐 1688 已处理标记字段。"""

        columns = {
            str(row["name"])
            for row in connection.execute("pragma table_info(ozon_batch_products)").fetchall()
        }
        if "alibaba_processed" not in columns:
            connection.execute(
                "alter table ozon_batch_products add column alibaba_processed integer not null default 0"
            )
        if "alibaba_processed_at" not in columns:
            connection.execute(
                "alter table ozon_batch_products add column alibaba_processed_at text"
            )
        connection.execute(
            "create index if not exists idx_ozon_batch_products_alibaba_processed on ozon_batch_products (alibaba_processed)"
        )

    def _decode_product_row(self, row: sqlite3.Row) -> dict[str, Any]:
        """把 SQLite 行转换成 Python 字典。"""

        payload = dict(row)
        for field in self.PRODUCT_JSON_FIELDS:
            if payload.get(field):
                payload[field] = json.loads(payload[field])
            elif field == "raw_payload":
                payload[field] = {}
            else:
                payload[field] = []

        for field in ("passed", "manual_dedupe_selected", "is_russian_local_warehouse", "alibaba_processed"):
            if payload.get(field) is not None:
                payload[field] = bool(payload[field])

        return payload
