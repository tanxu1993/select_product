"""Shopbang 热销类目翻页进度仓储。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from config.settings import Settings, get_settings
from ozon_selection.api.clients.sqlite_client import SQLiteClient


class ShopbangHotCategoryProgressRepository:
    """负责 SQLite 中的 Shopbang 热销类目续跑进度。"""

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
            connection.commit()

    def get_progress(self, *, category_name: str) -> dict[str, Any] | None:
        """查询单个类目的已保存进度。"""

        normalized_name = category_name.strip()
        if not self.is_configured or not normalized_name:
            return None

        self.ensure_schema()
        with self.client.connect() as connection:
            row = connection.execute(
                """
                select
                    id,
                    category_name,
                    request_body,
                    last_completed_page,
                    last_page_size,
                    last_status,
                    last_error,
                    last_run_at,
                    created_at,
                    updated_at
                from shopbang_hot_category_progress
                where category_name = ?
                """,
                (normalized_name,),
            ).fetchone()
        if row is None:
            return None
        return self._decode_row(row)

    def list_progress(self, *, query: str = "", status: str = "all") -> list[dict[str, Any]]:
        """读取全部类目续跑进度。"""

        normalized_query = str(query or "").strip()
        normalized_status = str(status or "all").strip().lower()
        if not self.is_configured:
            return []

        self.ensure_schema()

        clauses: list[str] = []
        params: list[Any] = []
        if normalized_query:
            clauses.append("category_name like ? collate nocase")
            params.append(f"%{normalized_query}%")
        if normalized_status and normalized_status != "all":
            clauses.append("last_status = ?")
            params.append(normalized_status)

        where_sql = f"where {' and '.join(clauses)}" if clauses else ""
        query_sql = f"""
            select
                id,
                category_name,
                request_body,
                last_completed_page,
                last_page_size,
                last_status,
                last_error,
                last_run_at,
                created_at,
                updated_at
            from shopbang_hot_category_progress
            {where_sql}
            order by last_run_at desc, id desc
        """

        with self.client.connect() as connection:
            rows = connection.execute(query_sql, params).fetchall()
        return [self._decode_row(row) for row in rows]

    def save_progress(
        self,
        *,
        category_name: str,
        request_body: dict[str, Any] | None,
        last_completed_page: int,
        last_page_size: int,
        status: str,
        error: str = "",
    ) -> dict[str, Any]:
        """写入类目翻页进度。"""

        normalized_name = category_name.strip()
        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured"}
        if not normalized_name:
            return {"status": "skipped", "reason": "empty_category_name"}

        self.ensure_schema()
        payload_text = json.dumps(request_body or {}, ensure_ascii=False, sort_keys=True)
        with self.client.connect() as connection:
            connection.execute(
                """
                insert into shopbang_hot_category_progress (
                    category_name,
                    request_body,
                    last_completed_page,
                    last_page_size,
                    last_status,
                    last_error,
                    last_run_at
                )
                values (?, ?, ?, ?, ?, ?, current_timestamp)
                on conflict(category_name) do update set
                    request_body = excluded.request_body,
                    last_completed_page = excluded.last_completed_page,
                    last_page_size = excluded.last_page_size,
                    last_status = excluded.last_status,
                    last_error = excluded.last_error,
                    last_run_at = current_timestamp,
                    updated_at = current_timestamp
                """,
                (
                    normalized_name,
                    payload_text,
                    max(int(last_completed_page), 0),
                    max(int(last_page_size), 0),
                    status.strip(),
                    error.strip(),
                ),
            )
            connection.commit()
        return {
            "status": "saved",
            "category_name": normalized_name,
            "last_completed_page": max(int(last_completed_page), 0),
            "last_page_size": max(int(last_page_size), 0),
            "last_status": status.strip(),
            "last_error": error.strip(),
        }

    def delete_progress(self, category_names: list[str]) -> dict[str, Any]:
        """按类目名删除续跑进度。"""

        normalized_names = sorted({str(name or "").strip() for name in category_names if str(name or "").strip()})
        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured", "deleted_count": 0}
        if not normalized_names:
            return {"status": "skipped", "reason": "empty_category_names", "deleted_count": 0}

        self.ensure_schema()

        placeholders = ", ".join("?" for _ in normalized_names)
        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"delete from shopbang_hot_category_progress where category_name in ({placeholders})",
                normalized_names,
            )
            deleted_count = int(cursor.rowcount or 0)
            connection.commit()

        return {
            "status": "deleted",
            "deleted_count": deleted_count,
            "category_count": len(normalized_names),
        }

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
        """把 SQLite 行转换成 Python 字典。"""

        payload = dict(row)
        try:
            payload["request_body"] = json.loads(str(payload.get("request_body") or "{}"))
        except Exception:
            payload["request_body"] = {}
        return payload
