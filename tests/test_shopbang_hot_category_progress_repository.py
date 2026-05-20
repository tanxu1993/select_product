"""Shopbang 热销类目进度仓储测试。"""

from __future__ import annotations

from config.settings import Settings
from ozon_selection.repositories.shopbang_hot_category_progress_repository import (
    ShopbangHotCategoryProgressRepository,
)


def test_shopbang_hot_category_progress_repository_can_save_and_load(tmp_path) -> None:
    """应能保存类目分页进度并再次读取。"""

    settings = Settings(SQLITE_PATH=str(tmp_path / "shopbang_progress.db"))
    repository = ShopbangHotCategoryProgressRepository(settings=settings)

    save_result = repository.save_progress(
        category_name="爱好和创作",
        request_body={"pageNo": 1, "pageSize": 60, "categories": [123]},
        last_completed_page=4,
        last_page_size=60,
        status="completed",
    )

    assert save_result["status"] == "saved"
    assert save_result["last_completed_page"] == 4

    row = repository.get_progress(category_name="爱好和创作")
    assert row is not None
    assert row["category_name"] == "爱好和创作"
    assert row["request_body"] == {"categories": [123], "pageNo": 1, "pageSize": 60}
    assert row["last_completed_page"] == 4
    assert row["last_page_size"] == 60
    assert row["last_status"] == "completed"
