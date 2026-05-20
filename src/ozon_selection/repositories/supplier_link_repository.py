"""1688 货源映射仓储。"""

from __future__ import annotations

from typing import Any

from supabase import create_client
from supabase.lib.client_options import SyncClientOptions

from config.settings import Settings, get_settings


class SupplierLinkRepository:
    """负责把 Ozon 和 1688 的匹配结果写入 Supabase。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        table_name: str = "supplier_links",
    ) -> None:
        self.settings = settings or get_settings()
        self.table_name = table_name
        self._client = None

    @property
    def is_configured(self) -> bool:
        """判断当前是否已配置 Supabase 写入所需参数。"""

        diagnostics = self.get_configuration_diagnostics()
        return not diagnostics["missing_fields"] and not diagnostics["placeholder_fields"]

    def save_many(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        """批量保存 1688 匹配结果。"""

        if not payloads:
            return {"status": "skipped", "count": 0, "reason": "empty_payload"}

        if not self.is_configured:
            diagnostics = self.get_configuration_diagnostics()
            return {
                "status": "skipped",
                "count": 0,
                "reason": "supabase_not_configured",
                "missing_fields": diagnostics["missing_fields"],
                "placeholder_fields": diagnostics["placeholder_fields"],
            }

        client = self._ensure_client()
        table = client.table(self.table_name)
        if hasattr(table, "upsert"):
            response = table.upsert(payloads, on_conflict="source_product_id,supplier_product_url").execute()
        else:
            response = table.insert(payloads).execute()
        saved_rows = response.data if hasattr(response, "data") else None
        return {
            "status": "saved",
            "count": len(saved_rows) if isinstance(saved_rows, list) else len(payloads),
            "table": self.table_name,
        }

    def get_configuration_diagnostics(self) -> dict[str, list[str]]:
        """返回 Supabase 关键配置的缺失和占位状态。"""

        required_fields = {
            "SUPABASE_URL": self.settings.supabase_url,
            "SUPABASE_SERVICE_ROLE_KEY": self.settings.supabase_service_role_key,
        }
        missing_fields: list[str] = []
        placeholder_fields: list[str] = []

        for field_name, value in required_fields.items():
            normalized = (value or "").strip()
            if not normalized:
                missing_fields.append(field_name)
            elif not self._is_real_value(normalized):
                placeholder_fields.append(field_name)

        return {
            "missing_fields": missing_fields,
            "placeholder_fields": placeholder_fields,
        }

    def _ensure_client(self):
        """按需创建 Supabase 客户端。"""

        if self._client is None:
            options = SyncClientOptions(schema=self.settings.supabase_schema)
            self._client = create_client(
                self.settings.supabase_url,
                self.settings.supabase_service_role_key,
                options=options,
            )
        return self._client

    @staticmethod
    def _is_real_value(value: str) -> bool:
        """判断配置值是否不是示例占位内容。"""

        normalized = (value or "").strip()
        if not normalized:
            return False

        placeholders = {
            "https://your-project.supabase.co",
            "your_supabase_service_role_key",
            "your_supabase_anon_key",
        }
        return normalized not in placeholders
