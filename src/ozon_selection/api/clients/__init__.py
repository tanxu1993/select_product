"""第三方客户端集合。"""

from ozon_selection.api.clients.openai_client import OpenAIClient
from ozon_selection.api.clients.sqlite_client import SQLiteClient

__all__ = ["OpenAIClient", "SQLiteClient"]
