"""Ozon 关键词池仓储测试。"""

from __future__ import annotations

from config.settings import Settings
from ozon_selection.repositories.ozon_keyword_pool_repository import OzonKeywordPoolRepository


def test_keyword_pool_repository_can_upsert_pick_and_mark(tmp_path) -> None:
    """应能写入关键词、抽取未使用关键词并标记状态。"""

    settings = Settings(SQLITE_PATH=str(tmp_path / "keyword_pool.db"))
    repository = OzonKeywordPoolRepository(settings=settings)

    save_result = repository.upsert_keyword_records(
        [
            {
                "current_category": "SvetoCopy",
                "parent_category": "Бумага для печати",
                "grandparent_category": "Бумага",
                "source_product_title": "SvetoCopy Printer Paper A4, 500 pcs",
                "source_product_url": "https://www.ozon.ru/product/149222760",
                "source_product_sku": "149222760",
                "source_batch_type": "shopbang_hot",
            }
        ]
    )

    assert save_result["status"] == "saved"
    assert save_result["input_keyword_count"] == 2
    assert save_result["saved_count"] == 1
    assert save_result["removed_count"] == 1
    assert save_result["removed_keywords"] == ["Бумага"]

    all_rows = repository.list_keywords()
    assert [item["keyword"] for item in all_rows] == ["Бумага для печати"]

    picked = repository.pick_random_unused_keywords(limit=10)
    assert picked == ["Бумага для печати"]

    repository.mark_keyword_used(keyword="Бумага для печати", status="success")

    used_rows = repository.list_keywords(used_status="used")
    assert len(used_rows) == 1
    assert used_rows[0]["keyword"] == "Бумага для печати"
    assert used_rows[0]["last_used_status"] == "success"
    assert used_rows[0]["use_count"] == 1


def test_list_processed_source_urls_returns_normalized_unique_urls(tmp_path) -> None:
    """已处理 URL 应按规范化结果返回。"""

    settings = Settings(SQLITE_PATH=str(tmp_path / "keyword_pool_urls.db"))
    repository = OzonKeywordPoolRepository(settings=settings)

    repository.upsert_keyword_records(
        [
            {
                "current_category": "CatA",
                "parent_category": "Бумага для печати",
                "grandparent_category": "",
                "source_product_title": "A",
                "source_product_url": "https://www.ozon.ru/product/149222760/?__rr=1",
                "source_product_sku": "149222760",
                "source_batch_type": "shopbang_hot",
            },
            {
                "current_category": "CatB",
                "parent_category": "Шампуни",
                "grandparent_category": "",
                "source_product_title": "B",
                "source_product_url": "https://www.ozon.ru/product/149222760/",
                "source_product_sku": "149222760",
                "source_batch_type": "shopbang_hot",
            },
        ]
    )

    assert repository.list_processed_source_urls() == ["https://www.ozon.ru/product/149222760"]


def test_keyword_pool_repository_dedupes_duplicate_parent_categories(tmp_path) -> None:
    """同一个上一级类目只应保留一条记录。"""

    settings = Settings(SQLITE_PATH=str(tmp_path / "keyword_pool_parent_dedupe.db"))
    repository = OzonKeywordPoolRepository(settings=settings)

    save_result = repository.upsert_keyword_records(
        [
            {
                "current_category": "DualSense",
                "parent_category": "PlayStation",
                "grandparent_category": "Игры и консоли",
                "source_product_title": "Sony PlayStation Controller",
                "source_product_url": "https://www.ozon.ru/product/123456789",
                "source_product_sku": "123456789",
                "source_batch_type": "shopbang_hot",
            }
        ]
    )

    assert save_result["status"] == "saved"
    assert save_result["input_keyword_count"] == 2
    assert save_result["saved_count"] == 1
    assert save_result["removed_count"] == 1
    assert save_result["deleted_duplicate_parent_rows"] == 0

    all_rows = repository.list_keywords()
    assert len(all_rows) == 1
    assert all_rows[0]["keyword"] == "PlayStation"
    assert all_rows[0]["keyword_level"] == "parent"
