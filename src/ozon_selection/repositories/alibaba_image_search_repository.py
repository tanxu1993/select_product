"""1688 图搜图结果 SQLite 仓储。"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from config.settings import Settings, get_settings
from ozon_selection.api.clients.sqlite_client import SQLiteClient


class AlibabaImageSearchRepository:
    """负责把 1688 图搜图结果写入和读取 SQLite。"""

    JSON_FIELDS = {"supplier_attributes", "raw_payload"}

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
            self._ensure_completion_columns(connection)
            self._backfill_task_completion_status(connection)
            connection.commit()

    def save_many(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        """批量写入 1688 图搜图结果。"""

        if not payloads:
            return {"status": "skipped", "count": 0, "reason": "empty_payload"}
        if not self.is_configured:
            return {"status": "skipped", "count": 0, "reason": "sqlite_not_configured"}

        self.ensure_schema()

        with self.client.connect() as connection:
            cursor = connection.cursor()
            for payload in payloads:
                resolved_is_completed = int(bool(payload.get("is_completed"))) if payload.get("is_completed") is not None else 1
                resolved_completed_at = payload.get("completed_at")
                if resolved_is_completed and not resolved_completed_at:
                    resolved_completed_at = time.strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute(
                    """
                    insert into alibaba_image_search_results (
                        ozon_batch_id,
                        ozon_keyword,
                        source_platform,
                        source_product_id,
                        source_title,
                        source_product_url,
                        source_image_url,
                        source_image_path,
                        source_price,
                        supplier_platform,
                        supplier_title,
                        supplier_product_url,
                        supplier_image_url,
                        supplier_price,
                        supplier_price_text,
                        supplier_unit_price,
                        supplier_unit_price_text,
                        supplier_weight_text,
                        supplier_weight_grams,
                        supplier_attributes,
                        supplier_seller,
                        ai_image_same_product,
                        ai_image_match_score,
                        ai_image_confidence,
                        ai_image_summary,
                        search_method,
                        source_reference,
                        raw_payload,
                        is_completed,
                        completed_at
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(ozon_batch_id, source_product_id, supplier_product_url) do update set
                        ozon_keyword = excluded.ozon_keyword,
                        source_title = excluded.source_title,
                        source_product_url = excluded.source_product_url,
                        source_image_url = excluded.source_image_url,
                        source_image_path = excluded.source_image_path,
                        source_price = excluded.source_price,
                        supplier_title = excluded.supplier_title,
                        supplier_image_url = excluded.supplier_image_url,
                        supplier_price = excluded.supplier_price,
                        supplier_price_text = excluded.supplier_price_text,
                        supplier_unit_price = excluded.supplier_unit_price,
                        supplier_unit_price_text = excluded.supplier_unit_price_text,
                        supplier_weight_text = excluded.supplier_weight_text,
                        supplier_weight_grams = excluded.supplier_weight_grams,
                        supplier_attributes = excluded.supplier_attributes,
                        supplier_seller = excluded.supplier_seller,
                        ai_image_same_product = excluded.ai_image_same_product,
                        ai_image_match_score = excluded.ai_image_match_score,
                        ai_image_confidence = excluded.ai_image_confidence,
                        ai_image_summary = excluded.ai_image_summary,
                        search_method = excluded.search_method,
                        source_reference = excluded.source_reference,
                        raw_payload = excluded.raw_payload,
                        is_completed = excluded.is_completed,
                        completed_at = coalesce(excluded.completed_at, completed_at, current_timestamp),
                        updated_at = current_timestamp
                    """,
                    (
                        payload.get("ozon_batch_id"),
                        payload.get("ozon_keyword"),
                        payload.get("source_platform", "ozon"),
                        payload.get("source_product_id"),
                        payload.get("source_title"),
                        payload.get("source_product_url"),
                        payload.get("source_image_url"),
                        payload.get("source_image_path"),
                        payload.get("source_price"),
                        payload.get("supplier_platform", "1688"),
                        payload.get("supplier_title"),
                        payload.get("supplier_product_url"),
                        payload.get("supplier_image_url"),
                        payload.get("supplier_price"),
                        payload.get("supplier_price_text"),
                        payload.get("supplier_unit_price"),
                        payload.get("supplier_unit_price_text"),
                        payload.get("supplier_weight_text"),
                        payload.get("supplier_weight_grams"),
                        json.dumps(payload.get("supplier_attributes") or [], ensure_ascii=False),
                        payload.get("supplier_seller"),
                        None if payload.get("ai_image_same_product") is None else int(bool(payload.get("ai_image_same_product"))),
                        payload.get("ai_image_match_score"),
                        payload.get("ai_image_confidence"),
                        payload.get("ai_image_summary"),
                        payload.get("search_method"),
                        payload.get("source_reference"),
                        json.dumps(payload.get("raw_payload") or {}, ensure_ascii=False),
                        resolved_is_completed,
                        resolved_completed_at,
                    ),
                )
            connection.commit()

        return {"status": "saved", "count": len(payloads)}

    def list_results(
        self,
        *,
        keyword: str = "",
        completion_status: str = "all",
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """读取 1688 图搜图结果。"""

        if not self.is_configured:
            return []

        self.ensure_schema()

        clauses: list[str] = []
        params: list[Any] = []
        if keyword.strip():
            clauses.append("ozon_keyword like ? collate nocase")
            params.append(f"%{keyword.strip()}%")
        if completion_status == "completed":
            clauses.append("is_completed = 1")
        elif completion_status == "pending":
            clauses.append("is_completed = 0")

        where_sql = f"where {' and '.join(clauses)}" if clauses else ""

        limit_sql = ""
        if limit is not None and limit > 0:
            limit_sql = " limit ? offset ?"
            params.extend([int(limit), max(int(offset), 0)])

        query = f"""
            select
                id,
                ozon_batch_id,
                ozon_keyword,
                source_platform,
                source_product_id,
                source_title,
                source_product_url,
                source_image_url,
                source_image_path,
                source_price,
                supplier_platform,
                supplier_title,
                supplier_product_url,
                supplier_image_url,
                supplier_price,
                supplier_price_text,
                supplier_unit_price,
                supplier_unit_price_text,
                supplier_weight_text,
                supplier_weight_grams,
                supplier_attributes,
                supplier_seller,
                ai_image_same_product,
                ai_image_match_score,
                ai_image_confidence,
                ai_image_summary,
                search_method,
                source_reference,
                raw_payload,
                is_completed,
                completed_at,
                created_at,
                updated_at
            from alibaba_image_search_results
            {where_sql}
            order by created_at desc, id desc
            {limit_sql}
        """

        with self.client.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._decode_row(row) for row in rows]

    def count_results(self, *, keyword: str = "", completion_status: str = "all") -> int:
        """统计 1688 图搜图结果数量。"""

        if not self.is_configured:
            return 0

        self.ensure_schema()

        clauses: list[str] = []
        params: list[Any] = []
        if keyword.strip():
            clauses.append("ozon_keyword like ? collate nocase")
            params.append(f"%{keyword.strip()}%")
        if completion_status == "completed":
            clauses.append("is_completed = 1")
        elif completion_status == "pending":
            clauses.append("is_completed = 0")

        where_sql = f"where {' and '.join(clauses)}" if clauses else ""

        query = f"""
            select count(1) as total_count
            from alibaba_image_search_results
            {where_sql}
        """
        with self.client.connect() as connection:
            row = connection.execute(query, params).fetchone()
        return int((row["total_count"] if row else 0) or 0)

    def delete_results(self, result_ids: list[int]) -> dict[str, Any]:
        """批量删除 1688 图搜图结果。"""

        normalized_ids = [int(result_id) for result_id in result_ids if int(result_id) > 0]
        if not normalized_ids:
            return {"status": "skipped", "deleted_count": 0, "reason": "empty_result_ids"}
        if not self.is_configured:
            return {"status": "skipped", "deleted_count": 0, "reason": "sqlite_not_configured"}

        self.ensure_schema()
        placeholders = ",".join("?" for _ in normalized_ids)
        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"delete from alibaba_image_search_results where id in ({placeholders})",
                normalized_ids,
            )
            deleted_count = cursor.rowcount
            connection.commit()
        return {"status": "deleted", "deleted_count": deleted_count}

    def mark_results_completed(self, result_ids: list[int]) -> dict[str, Any]:
        """批量标记 1688 图搜图结果为已完成。"""

        normalized_ids = [int(result_id) for result_id in result_ids if int(result_id) > 0]
        if not normalized_ids:
            return {"status": "skipped", "updated_count": 0, "reason": "empty_result_ids"}
        if not self.is_configured:
            return {"status": "skipped", "updated_count": 0, "reason": "sqlite_not_configured"}

        self.ensure_schema()
        placeholders = ",".join("?" for _ in normalized_ids)
        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"""
                update alibaba_image_search_results
                set
                    is_completed = 1,
                    completed_at = current_timestamp,
                    updated_at = current_timestamp
                where id in ({placeholders})
                """,
                normalized_ids,
            )
            updated_count = cursor.rowcount
            connection.commit()
        return {"status": "updated", "updated_count": updated_count}

    @staticmethod
    def _ensure_completion_columns(connection: sqlite3.Connection) -> None:
        """兼容旧 SQLite：补齐完成状态字段。"""

        columns = {
            str(row["name"])
            for row in connection.execute("pragma table_info(alibaba_image_search_results)").fetchall()
        }
        if "is_completed" not in columns:
            connection.execute(
                "alter table alibaba_image_search_results add column is_completed integer not null default 0"
            )
        if "completed_at" not in columns:
            connection.execute(
                "alter table alibaba_image_search_results add column completed_at text"
            )
        connection.execute(
            "create index if not exists idx_alibaba_image_search_results_completed on alibaba_image_search_results (is_completed)"
        )

    @staticmethod
    def _backfill_task_completion_status(connection: sqlite3.Connection) -> None:
        """统一完成口径：已有图搜结果即视为任务完成。"""

        connection.execute(
            """
            update alibaba_image_search_results
            set
                is_completed = 1,
                completed_at = coalesce(completed_at, created_at, updated_at, current_timestamp),
                updated_at = current_timestamp
            where is_completed = 0
            """
        )

    def _decode_row(self, row: sqlite3.Row) -> dict[str, Any]:
        """把 SQLite 行解码成 Python 字典。"""

        payload = dict(row)
        for field in self.JSON_FIELDS:
            if payload.get(field):
                payload[field] = json.loads(payload[field])
            else:
                payload[field] = [] if field == "supplier_attributes" else {}
        if payload.get("ai_image_same_product") is not None:
            payload["ai_image_same_product"] = bool(payload["ai_image_same_product"])
        if payload.get("is_completed") is not None:
            payload["is_completed"] = bool(payload["is_completed"])
        return payload
