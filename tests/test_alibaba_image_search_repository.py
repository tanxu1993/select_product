"""1688 图搜图 SQLite 仓储测试。"""

from __future__ import annotations

from config.settings import Settings
from ozon_selection.repositories.alibaba_image_search_repository import AlibabaImageSearchRepository


def test_save_many_skips_when_sqlite_not_configured() -> None:
    """未配置 SQLite 时应跳过写入。"""

    repository = AlibabaImageSearchRepository(settings=Settings(SQLITE_PATH=""))

    result = repository.save_many([{"source_product_id": "1"}])

    assert result["status"] == "skipped"
    assert result["reason"] == "sqlite_not_configured"


def test_save_and_list_results(tmp_path) -> None:
    """应能写入并读取 1688 图搜图结果。"""

    repository = AlibabaImageSearchRepository(settings=Settings(SQLITE_PATH=str(tmp_path / "ozon.db")))

    result = repository.save_many(
        [
            {
                "ozon_batch_id": None,
                "ozon_keyword": "demo",
                "source_platform": "ozon",
                "source_product_id": "sku-1",
                "source_title": "ozon demo",
                "supplier_platform": "1688",
                "supplier_title": "supplier demo",
                "supplier_product_url": "https://detail.1688.com/offer/1.html",
                "supplier_attributes": [{"key": "颜色", "value": "红色"}],
                "ai_image_same_product": True,
                "ai_image_match_score": 91,
                "source_reference": "sqlite://demo",
                "raw_payload": {"demo": True},
            }
        ]
    )

    assert result["status"] == "saved"
    assert result["count"] == 1

    rows = repository.list_results(keyword="demo")
    assert len(rows) == 1
    assert rows[0]["source_product_id"] == "sku-1"
    assert rows[0]["supplier_attributes"] == [{"key": "颜色", "value": "红色"}]
    assert rows[0]["ai_image_same_product"] is True


def test_list_results_supports_pagination_and_delete(tmp_path) -> None:
    """应支持分页读取和批量删除。"""

    repository = AlibabaImageSearchRepository(settings=Settings(SQLITE_PATH=str(tmp_path / "ozon.db")))
    repository.save_many(
        [
            {
                "ozon_keyword": "demo",
                "source_product_id": "sku-1",
                "supplier_title": "supplier 1",
                "supplier_product_url": "https://detail.1688.com/offer/1.html",
            },
            {
                "ozon_keyword": "demo",
                "source_product_id": "sku-2",
                "supplier_title": "supplier 2",
                "supplier_product_url": "https://detail.1688.com/offer/2.html",
            },
            {
                "ozon_keyword": "demo",
                "source_product_id": "sku-3",
                "supplier_title": "supplier 3",
                "supplier_product_url": "https://detail.1688.com/offer/3.html",
            },
        ]
    )

    total_count = repository.count_results(keyword="demo")
    assert total_count == 3

    first_page = repository.list_results(keyword="demo", limit=2, offset=0)
    second_page = repository.list_results(keyword="demo", limit=2, offset=2)
    assert len(first_page) == 2
    assert len(second_page) == 1

    delete_result = repository.delete_results([int(first_page[0]["id"]), int(second_page[0]["id"])])
    assert delete_result["status"] == "deleted"
    assert delete_result["deleted_count"] == 2
    assert repository.count_results(keyword="demo") == 1


def test_mark_results_completed_and_filter_by_completion_status(tmp_path) -> None:
    """应支持批量标记已完成，并按完成状态过滤。"""

    repository = AlibabaImageSearchRepository(settings=Settings(SQLITE_PATH=str(tmp_path / "ozon.db")))
    repository.save_many(
        [
            {
                "ozon_keyword": "demo",
                "source_product_id": "sku-1",
                "supplier_title": "supplier 1",
                "supplier_product_url": "https://detail.1688.com/offer/1.html",
            },
            {
                "ozon_keyword": "demo",
                "source_product_id": "sku-2",
                "supplier_title": "supplier 2",
                "supplier_product_url": "https://detail.1688.com/offer/2.html",
            },
        ]
    )

    pending_rows = repository.list_results(keyword="demo", completion_status="pending")
    assert len(pending_rows) == 2

    mark_result = repository.mark_results_completed([int(pending_rows[0]["id"])])
    assert mark_result["status"] == "updated"
    assert mark_result["updated_count"] == 1

    completed_rows = repository.list_results(keyword="demo", completion_status="completed")
    pending_rows_after = repository.list_results(keyword="demo", completion_status="pending")
    assert len(completed_rows) == 1
    assert completed_rows[0]["is_completed"] is True
    assert completed_rows[0]["completed_at"]
    assert len(pending_rows_after) == 1
