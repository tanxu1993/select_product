"""Shopbang 历史页关键词仓储。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from config.settings import Settings, get_settings
from ozon_selection.api.clients.sqlite_client import SQLiteClient


class ShopbangHistoryKeywordRepository:
    """负责 SQLite 中的 Shopbang 历史关键词数据。"""

    AVG_PRICE_MIN = 500
    AVG_PRICE_MAX = 20000
    RUNTIME_COLUMNS = (
        ("used", "integer not null default 0"),
        ("used_at", "text"),
        ("last_used_status", "text"),
        ("last_error", "text"),
        ("use_count", "integer not null default 0"),
    )

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
            if self._table_exists(connection, "shopbang_history_keywords"):
                self._ensure_runtime_columns(connection)
            connection.executescript(sql)
            self._ensure_runtime_columns(connection)
            connection.execute(
                """
                create index if not exists idx_shopbang_history_keywords_used
                    on shopbang_history_keywords (used, last_seen_at desc)
                """
            )
            connection.commit()

    def upsert_keywords(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """按关键词写入历史页关键词。"""

        normalized_records = self._dedupe_records(records)
        filtered_records, filtered_out_count = self._filter_records_by_avg_price(normalized_records)
        if not self.is_configured:
            return {
                "status": "skipped",
                "reason": "sqlite_not_configured",
                "saved_count": 0,
                "input_count": len(records),
                "deduped_count": len(normalized_records),
                "filtered_out_count": filtered_out_count,
            }
        if not filtered_records:
            return {
                "status": "skipped",
                "reason": "empty_records",
                "saved_count": 0,
                "input_count": len(records),
                "deduped_count": len(normalized_records),
                "filtered_out_count": filtered_out_count,
            }

        self.ensure_schema()

        saved_count = 0
        with self.client.connect() as connection:
            cursor = connection.cursor()
            for record in filtered_records:
                cursor.execute(
                    """
                    insert into shopbang_history_keywords (
                        keyword,
                        avg_price,
                        source_page,
                        source_endpoint,
                        price_min,
                        price_max,
                        source_count,
                        filters_json,
                        raw_payload
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(keyword) do update set
                        avg_price = coalesce(excluded.avg_price, shopbang_history_keywords.avg_price),
                        source_page = excluded.source_page,
                        source_endpoint = excluded.source_endpoint,
                        price_min = excluded.price_min,
                        price_max = excluded.price_max,
                        source_count = shopbang_history_keywords.source_count + 1,
                        filters_json = excluded.filters_json,
                        raw_payload = excluded.raw_payload,
                        last_seen_at = current_timestamp,
                        updated_at = current_timestamp
                    """,
                    (
                        record.get("keyword"),
                        record.get("avg_price"),
                        record.get("source_page"),
                        record.get("source_endpoint"),
                        record.get("price_min"),
                        record.get("price_max"),
                        max(int(record.get("source_count") or 1), 1),
                        json.dumps(record.get("filters") or {}, ensure_ascii=False, sort_keys=True),
                        json.dumps(record.get("raw_payload") or {}, ensure_ascii=False, sort_keys=True),
                    ),
                )
                saved_count += 1
            connection.commit()

        return {
            "status": "saved",
            "saved_count": saved_count,
            "input_count": len(records),
            "deduped_count": len(normalized_records),
            "filtered_out_count": filtered_out_count,
        }

    def delete_out_of_range_keywords(self) -> dict[str, Any]:
        """删除平均价格不在 500-20000 之间的历史关键词。"""

        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured", "deleted_count": 0}

        self.ensure_schema()
        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                delete from shopbang_history_keywords
                where avg_price is null
                   or avg_price < ?
                   or avg_price > ?
                """,
                (self.AVG_PRICE_MIN, self.AVG_PRICE_MAX),
            )
            deleted_count = int(cursor.rowcount or 0)
            connection.commit()
        return {"status": "deleted", "deleted_count": deleted_count}

    def delete_keywords_by_fragments(self, fragments: list[str], *, include_raw_payload: bool = False) -> dict[str, Any]:
        """按关键词片段删除历史关键词。"""

        normalized_fragments = [str(item or "").strip().lower() for item in fragments if str(item or "").strip()]
        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured", "deleted_count": 0}
        if not normalized_fragments:
            return {"status": "skipped", "reason": "empty_fragments", "deleted_count": 0}

        self.ensure_schema()
        conditions: list[str] = []
        parameters: list[str] = []
        for item in normalized_fragments:
            conditions.append("lower(keyword) like ?")
            parameters.append(f"%{item}%")
            if include_raw_payload:
                conditions.append("lower(raw_payload) like ?")
                parameters.append(f"%{item}%")
        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"""
                delete from shopbang_history_keywords
                where {" or ".join(conditions)}
                """,
                tuple(parameters),
            )
            deleted_count = int(cursor.rowcount or 0)
            connection.commit()
        return {
            "status": "deleted",
            "deleted_count": deleted_count,
            "fragment_count": len(normalized_fragments),
            "include_raw_payload": include_raw_payload,
        }

    def list_keywords(self, *, keyword: str = "", used_status: str = "all") -> list[dict[str, Any]]:
        """读取全部历史页关键词。"""

        if not self.is_configured:
            return []

        self.ensure_schema()
        clauses = []
        params: list[Any] = []
        if keyword.strip():
            clauses.append("keyword like ? collate nocase")
            params.append(f"%{keyword.strip()}%")
        if used_status == "used":
            clauses.append("used = 1")
        elif used_status == "unused":
            clauses.append("used = 0")

        where_sql = f"where {' and '.join(clauses)}" if clauses else ""
        with self.client.connect() as connection:
            rows = connection.execute(
                f"""
                select
                    id,
                    keyword,
                    avg_price,
                    source_page,
                    source_endpoint,
                    price_min,
                    price_max,
                    source_count,
                    filters_json,
                    raw_payload,
                    used,
                    used_at,
                    last_used_status,
                    last_error,
                    use_count,
                    first_seen_at,
                    last_seen_at,
                    created_at,
                    updated_at
                from shopbang_history_keywords
                {where_sql}
                order by last_seen_at desc, id desc
                """,
                params,
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def pick_random_unused_keywords(self, *, limit: int) -> list[str]:
        """随机挑选未爬取关键词。"""

        if not self.is_configured or limit <= 0:
            return []

        self.ensure_schema()
        with self.client.connect() as connection:
            rows = connection.execute(
                """
                select keyword
                from shopbang_history_keywords
                where used = 0
                order by random()
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [str(row["keyword"]) for row in rows]

    def mark_keyword_used(self, *, keyword: str, status: str, error: str = "") -> dict[str, Any]:
        """标记历史关键词已爬取。"""

        normalized_keyword = str(keyword or "").strip()
        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured"}
        if not normalized_keyword:
            return {"status": "skipped", "reason": "empty_keyword"}

        self.ensure_schema()
        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                update shopbang_history_keywords
                set
                    used = 1,
                    used_at = current_timestamp,
                    last_used_status = ?,
                    last_error = ?,
                    use_count = use_count + 1,
                    updated_at = current_timestamp
                where keyword = ?
                """,
                (status, error, normalized_keyword),
            )
            connection.commit()
        return {"status": "updated", "keyword": normalized_keyword}

    @classmethod
    def filter_records_by_avg_price(cls, records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """对外暴露平均价格过滤逻辑，统一导出与入库口径。"""

        return cls._filter_records_by_avg_price(records)

    @staticmethod
    def _dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按关键词去重。"""

        deduped: dict[str, dict[str, Any]] = {}
        ordered_keywords: list[str] = []
        for record in records:
            keyword = str(record.get("keyword") or "").strip()
            if not keyword:
                continue
            if keyword not in deduped:
                deduped[keyword] = dict(record)
                ordered_keywords.append(keyword)
                continue

            existing = deduped[keyword]
            if existing.get("avg_price") in (None, "") and record.get("avg_price") not in (None, ""):
                existing["avg_price"] = record.get("avg_price")
            if existing.get("source_page") in (None, "") and record.get("source_page") not in (None, ""):
                existing["source_page"] = record.get("source_page")
            existing["source_count"] = max(int(existing.get("source_count") or 1), 1) + 1
            existing["raw_payload"] = record.get("raw_payload") or existing.get("raw_payload") or {}
        return [deduped[keyword] for keyword in ordered_keywords]

    @classmethod
    def _filter_records_by_avg_price(cls, records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """只保留平均价格在 500-20000 之间的记录。"""

        kept_records: list[dict[str, Any]] = []
        filtered_out_count = 0
        for record in records:
            avg_price = record.get("avg_price")
            try:
                numeric_price = float(avg_price)
            except Exception:
                filtered_out_count += 1
                continue
            if not cls.AVG_PRICE_MIN <= numeric_price <= cls.AVG_PRICE_MAX:
                filtered_out_count += 1
                continue
            record_copy = dict(record)
            record_copy["avg_price"] = numeric_price
            kept_records.append(record_copy)
        return kept_records, filtered_out_count

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
        """把 SQLite 行转成 Python 字典。"""

        payload = dict(row)
        for field_name in ("filters_json", "raw_payload"):
            try:
                payload[field_name.removesuffix("_json")] = json.loads(str(payload.get(field_name) or "{}"))
            except Exception:
                payload[field_name.removesuffix("_json")] = {}
        payload.pop("filters_json", None)
        if "used" in payload:
            payload["used"] = bool(payload.get("used"))
        return payload

    @classmethod
    def _ensure_runtime_columns(cls, connection: sqlite3.Connection) -> None:
        """为旧库补齐运行时需要的状态字段。"""

        existing_columns = {
            str(row["name"])
            for row in connection.execute("pragma table_info(shopbang_history_keywords)").fetchall()
        }
        for column_name, column_sql in cls.RUNTIME_COLUMNS:
            if column_name in existing_columns:
                continue
            connection.execute(f"alter table shopbang_history_keywords add column {column_name} {column_sql}")

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
        """判断数据表是否已存在。"""

        row = connection.execute(
            "select name from sqlite_master where type = 'table' and name = ?",
            (table_name,),
        ).fetchone()
        return row is not None
