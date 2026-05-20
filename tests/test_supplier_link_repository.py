"""1688 货源映射仓储测试。"""

from __future__ import annotations

from config.settings import Settings
from ozon_selection.repositories.supplier_link_repository import SupplierLinkRepository


class DummyExecuteResult:
    """模拟 Supabase execute() 返回值。"""

    def __init__(self, data):
        self.data = data


class DummyTable:
    """模拟 Supabase table 查询对象。"""

    def __init__(self) -> None:
        self.payloads = None
        self.on_conflict = None

    def insert(self, payloads):
        self.payloads = payloads
        return self

    def upsert(self, payloads, on_conflict=None):
        self.payloads = payloads
        self.on_conflict = on_conflict
        return self

    def execute(self):
        return DummyExecuteResult(self.payloads)


class DummyClient:
    """模拟 Supabase client。"""

    def __init__(self) -> None:
        self.table_name = None
        self.table_instance = DummyTable()

    def table(self, table_name):
        self.table_name = table_name
        return self.table_instance


def test_save_many_skips_when_supabase_not_configured() -> None:
    """未配置 Supabase 时应跳过写入。"""

    repository = SupplierLinkRepository(
        settings=Settings(
            SUPABASE_URL="",
            SUPABASE_SERVICE_ROLE_KEY="",
        )
    )
    result = repository.save_many([{"source_product_id": "1"}])

    assert result["status"] == "skipped"
    assert result["reason"] == "supabase_not_configured"
    assert result["missing_fields"] == ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]
    assert result["placeholder_fields"] == []


def test_save_many_reports_placeholder_supabase_values() -> None:
    """占位值配置应被明确识别出来。"""

    repository = SupplierLinkRepository(
        settings=Settings(
            SUPABASE_URL="https://your-project.supabase.co",
            SUPABASE_SERVICE_ROLE_KEY="your_supabase_service_role_key",
        )
    )

    result = repository.save_many([{"source_product_id": "1"}])

    assert result["status"] == "skipped"
    assert result["reason"] == "supabase_not_configured"
    assert result["missing_fields"] == []
    assert result["placeholder_fields"] == ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]


def test_save_many_inserts_payloads(monkeypatch) -> None:
    """确保会向 supplier_links 表写入批量记录。"""

    dummy_client = DummyClient()

    def fake_create_client(*_args, **_kwargs):
        return dummy_client

    monkeypatch.setattr(
        "ozon_selection.repositories.supplier_link_repository.create_client",
        fake_create_client,
    )

    repository = SupplierLinkRepository(
        settings=Settings(
            SUPABASE_URL="https://example.supabase.co",
            SUPABASE_SERVICE_ROLE_KEY="service-role-key",
        )
    )
    payloads = [{"source_product_id": "1", "supplier_product_url": "https://detail.1688.com/offer/1.html"}]

    result = repository.save_many(payloads)

    assert result["status"] == "saved"
    assert result["count"] == 1
    assert dummy_client.table_name == "supplier_links"
    assert dummy_client.table_instance.payloads == payloads
    assert dummy_client.table_instance.on_conflict == "source_product_id,supplier_product_url"
