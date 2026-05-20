"""SQLite 客户端封装。"""

from __future__ import annotations

import sqlite3

from config.settings import Settings, get_settings


class SQLiteClient:
    """按需创建 SQLite 连接。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def is_configured(self) -> bool:
        """判断是否已配置 SQLite 文件路径。"""

        return bool((self.settings.sqlite_path or "").strip())

    def connect(self) -> sqlite3.Connection:
        """创建 SQLite 连接。"""

        database_path = self.settings.sqlite_db_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys = on")
        return connection
