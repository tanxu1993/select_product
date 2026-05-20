"""Ozon 关键词池仓储。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

from config.settings import Settings, get_settings
from ozon_selection.api.clients.sqlite_client import SQLiteClient
from ozon_selection.services.keyword_deduper import KeywordDeduper


class OzonKeywordPoolRepository:
    """负责 SQLite 中的 Ozon 关键词池数据。"""

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

    def upsert_keyword_records(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """把结构化关键词记录写入关键词池。"""

        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured", "saved_count": 0}
        if not records:
            return {"status": "skipped", "reason": "empty_records", "saved_count": 0}

        self.ensure_schema()
        keyword_entries = self._expand_keyword_entries(records)
        if not keyword_entries:
            return {"status": "skipped", "reason": "empty_keywords", "saved_count": 0}

        deduped_entries, removed_keywords = self._dedupe_keyword_entries(keyword_entries)

        saved_count = 0
        with self.client.connect() as connection:
            cursor = connection.cursor()
            for entry in deduped_entries:
                cursor.execute(
                    """
                    insert into ozon_keyword_pool (
                        keyword,
                        keyword_level,
                        current_category,
                        parent_category,
                        grandparent_category,
                        source_product_title,
                        source_product_url,
                        source_product_sku,
                        source_batch_type
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(keyword) do update set
                        keyword_level = excluded.keyword_level,
                        current_category = excluded.current_category,
                        parent_category = excluded.parent_category,
                        grandparent_category = excluded.grandparent_category,
                        source_product_title = excluded.source_product_title,
                        source_product_url = excluded.source_product_url,
                        source_product_sku = excluded.source_product_sku,
                        source_batch_type = excluded.source_batch_type,
                        updated_at = current_timestamp
                    """,
                    (
                        entry.get("keyword"),
                        entry.get("keyword_level"),
                        entry.get("current_category"),
                        entry.get("parent_category"),
                        entry.get("grandparent_category"),
                        entry.get("source_product_title"),
                        entry.get("source_product_url"),
                        entry.get("source_product_sku"),
                        entry.get("source_batch_type") or "shopbang_hot",
                    ),
                )
                saved_count += 1
            duplicate_cleanup = self._delete_duplicate_parent_categories(connection)
            connection.commit()

        return {
            "status": "saved",
            "saved_count": saved_count,
            "input_keyword_count": len(keyword_entries),
            "removed_count": len(removed_keywords) + int(duplicate_cleanup.get("deleted_count", 0)),
            "removed_keywords": removed_keywords,
            "kept_keywords": [str(entry.get("keyword") or "") for entry in deduped_entries],
            "deleted_duplicate_parent_rows": int(duplicate_cleanup.get("deleted_count", 0)),
        }

    def list_keywords(self, *, keyword: str = "", used_status: str = "all") -> list[dict[str, Any]]:
        """查询关键词池。"""

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
        query = f"""
            select
                id,
                keyword,
                keyword_level,
                current_category,
                parent_category,
                grandparent_category,
                source_product_title,
                source_product_url,
                source_product_sku,
                source_batch_type,
                used,
                used_at,
                last_used_status,
                last_error,
                use_count,
                created_at,
                updated_at
            from ozon_keyword_pool
            {where_sql}
            order by used asc, updated_at desc, id desc
        """

        with self.client.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._decode_row(row) for row in rows]

    def delete_keywords(self, keywords: list[str]) -> dict[str, Any]:
        """按关键词删除关键词池记录。"""

        normalized_keywords = [str(keyword or "").strip() for keyword in keywords if str(keyword or "").strip()]
        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured", "deleted_count": 0}
        if not normalized_keywords:
            return {"status": "skipped", "reason": "empty_keywords", "deleted_count": 0}

        self.ensure_schema()
        placeholders = ", ".join("?" for _ in normalized_keywords)
        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"delete from ozon_keyword_pool where keyword in ({placeholders})",
                normalized_keywords,
            )
            deleted_count = int(cursor.rowcount or 0)
            connection.commit()

        return {
            "status": "deleted",
            "deleted_count": deleted_count,
            "keyword_count": len(normalized_keywords),
        }

    def delete_duplicate_parent_categories(self) -> dict[str, Any]:
        """按上一级类目删除重复记录，仅保留每组中的优先项。"""

        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured", "deleted_count": 0}

        self.ensure_schema()
        with self.client.connect() as connection:
            result = self._delete_duplicate_parent_categories(connection)
            connection.commit()
        return result

    def pick_random_unused_keywords(self, *, limit: int) -> list[str]:
        """随机挑选未使用关键词。"""

        if not self.is_configured or limit <= 0:
            return []

        self.ensure_schema()
        with self.client.connect() as connection:
            rows = connection.execute(
                """
                select keyword
                from ozon_keyword_pool
                where used = 0
                order by random()
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [str(row["keyword"]) for row in rows]

    def list_processed_source_urls(self) -> list[str]:
        """返回已处理过的来源商品 URL。"""

        if not self.is_configured:
            return []

        self.ensure_schema()
        with self.client.connect() as connection:
            rows = connection.execute(
                """
                select distinct source_product_url
                from ozon_keyword_pool
                where trim(coalesce(source_product_url, '')) <> ''
                order by source_product_url asc
                """
            ).fetchall()
        normalized_urls: list[str] = []
        for row in rows:
            normalized = self.normalize_source_url(str(row["source_product_url"] or ""))
            if not normalized or normalized in normalized_urls:
                continue
            normalized_urls.append(normalized)
        return normalized_urls

    def mark_keyword_used(self, *, keyword: str, status: str, error: str = "") -> dict[str, Any]:
        """标记关键词已执行。"""

        normalized_keyword = keyword.strip()
        if not self.is_configured:
            return {"status": "skipped", "reason": "sqlite_not_configured"}
        if not normalized_keyword:
            return {"status": "skipped", "reason": "empty_keyword"}

        self.ensure_schema()
        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                update ozon_keyword_pool
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

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
        """把 SQLite 行转换成 Python 字典。"""

        payload = dict(row)
        payload["used"] = bool(payload.get("used"))
        return payload

    @staticmethod
    def normalize_source_url(url: str) -> str:
        """规范化来源 URL，便于跨轮次去重。"""

        normalized = str(url or "").strip()
        if not normalized:
            return ""
        parts = urlsplit(normalized)
        clean_path = parts.path.rstrip("/") or parts.path
        return urlunsplit((parts.scheme, parts.netloc, clean_path, "", ""))

    @staticmethod
    def _expand_keyword_entries(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把结构化关键词记录展开成待入库的关键词条目。"""

        entries: list[dict[str, Any]] = []
        for record in records:
            for keyword_level, field_name in (("parent", "parent_category"), ("grandparent", "grandparent_category")):
                keyword = str(record.get(field_name) or "").strip()
                if not keyword:
                    continue
                entries.append(
                    {
                        "keyword": keyword,
                        "keyword_level": keyword_level,
                        "current_category": record.get("current_category"),
                        "parent_category": record.get("parent_category"),
                        "grandparent_category": record.get("grandparent_category"),
                        "source_product_title": record.get("source_product_title"),
                        "source_product_url": record.get("source_product_url"),
                        "source_product_sku": record.get("source_product_sku"),
                        "source_batch_type": record.get("source_batch_type") or "shopbang_hot",
                    }
                )
        return entries

    @staticmethod
    def _dedupe_keyword_entries(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        """按语义规则去重关键词条目。"""

        if not entries:
            return [], []

        entry_by_keyword: dict[str, dict[str, Any]] = {}
        ordered_keywords: list[str] = []
        for entry in entries:
            keyword = str(entry.get("keyword") or "").strip()
            if not keyword:
                continue
            if keyword in entry_by_keyword:
                continue
            entry_by_keyword[keyword] = entry
            ordered_keywords.append(keyword)

        deduped = KeywordDeduper.dedupe_semantic(ordered_keywords)
        kept_entries: list[dict[str, Any]] = []
        removed_keywords: list[str] = []
        for group in deduped:
            kept_entry = entry_by_keyword.get(group.keyword)
            if kept_entry is not None:
                kept_entries.append(kept_entry)
            removed_keywords.extend(group.removed_keywords)
        parent_deduped_entries, parent_removed_keywords = OzonKeywordPoolRepository._dedupe_entries_by_parent_category(
            kept_entries
        )
        return parent_deduped_entries, removed_keywords + parent_removed_keywords

    @staticmethod
    def _dedupe_entries_by_parent_category(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        """按上一级类目去重内存中的关键词条目。"""

        grouped_entries: dict[str, list[dict[str, Any]]] = {}
        ordered_keys: list[str] = []
        for entry in entries:
            parent_category = str(entry.get("parent_category") or "").strip()
            group_key = parent_category or f"__keyword__::{str(entry.get('keyword') or '').strip()}"
            if group_key not in grouped_entries:
                grouped_entries[group_key] = []
                ordered_keys.append(group_key)
            grouped_entries[group_key].append(entry)

        kept_entries: list[dict[str, Any]] = []
        removed_keywords: list[str] = []
        for group_key in ordered_keys:
            group = grouped_entries[group_key]
            preferred = min(
                group,
                key=lambda item: (
                    0 if str(item.get("keyword") or "").strip() == str(item.get("parent_category") or "").strip() else 1,
                    0 if str(item.get("keyword_level") or "").strip() == "parent" else 1,
                    str(item.get("keyword") or "").strip(),
                ),
            )
            kept_entries.append(preferred)
            for item in group:
                if item is preferred:
                    continue
                keyword = str(item.get("keyword") or "").strip()
                if keyword:
                    removed_keywords.append(keyword)
        return kept_entries, removed_keywords

    @staticmethod
    def _delete_duplicate_parent_categories(connection: sqlite3.Connection) -> dict[str, Any]:
        """删除 SQLite 中上一级类目重复的记录。"""

        duplicate_rows = connection.execute(
            """
            select
                id,
                keyword,
                keyword_level,
                parent_category,
                used,
                use_count,
                updated_at
            from ozon_keyword_pool
            where trim(coalesce(parent_category, '')) <> ''
            order by parent_category asc, id desc
            """
        ).fetchall()

        grouped_rows: dict[str, list[sqlite3.Row]] = {}
        for row in duplicate_rows:
            parent_category = str(row["parent_category"] or "").strip()
            if not parent_category:
                continue
            grouped_rows.setdefault(parent_category, []).append(row)

        ids_to_delete: list[int] = []
        for group in grouped_rows.values():
            if len(group) <= 1:
                continue
            preferred = min(
                group,
                key=lambda row: (
                    0 if str(row["keyword"] or "").strip() == str(row["parent_category"] or "").strip() else 1,
                    0 if str(row["keyword_level"] or "").strip() == "parent" else 1,
                    -int(row["used"] or 0),
                    -int(row["use_count"] or 0),
                    -int(row["id"] or 0),
                ),
            )
            for row in group:
                if int(row["id"]) == int(preferred["id"]):
                    continue
                ids_to_delete.append(int(row["id"]))

        if not ids_to_delete:
            return {"status": "deleted", "deleted_count": 0}

        placeholders = ", ".join("?" for _ in ids_to_delete)
        cursor = connection.cursor()
        cursor.execute(
            f"delete from ozon_keyword_pool where id in ({placeholders})",
            ids_to_delete,
        )
        return {
            "status": "deleted",
            "deleted_count": int(cursor.rowcount or 0),
            "deleted_ids": ids_to_delete,
        }
