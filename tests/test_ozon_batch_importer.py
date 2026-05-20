"""Ozon SQLite 批次导入测试。"""

from __future__ import annotations

from pathlib import Path

from config.settings import Settings
from ozon_selection.repositories.ozon_batch_repository import OzonBatchRepository
from ozon_selection.services.ozon_batch_importer import OzonBatchImporter
from ozon_selection.services.ozon_candidate_pipeline import OzonCandidatePipeline


def test_build_product_payload_maps_expected_fields() -> None:
    """manifest 商品应被正确映射到 SQLite 记录。"""

    payload = OzonBatchImporter.build_product_payload(
        {
            "sku": "123",
            "name": "demo",
            "detailTitle": "demo detail",
            "url": "https://www.ozon.ru/product/demo-123/",
            "imageUrl": "https://image.example/demo.jpg",
            "localImagePath": "data/raw/product_images/123/1.jpg",
            "detailImageUrl": "https://image.example/detail.jpg",
            "attributes": [{"key": "颜色", "value": "红色"}],
            "price": 1000,
            "detailPrice": 999,
            "score": 8,
            "warnings": ["warn"],
            "failReasons": ["fail"],
            "deliveryInfo": "跨境配送",
            "returnInfo": "30天退货",
            "warehouseInfo": "中国仓",
            "isRussianLocalWarehouse": False,
        }
    )

    assert payload["source_product_id"] == "123"
    assert payload["title"] == "demo"
    assert payload["detail_title"] == "demo detail"
    assert payload["image_path"] == "data/raw/product_images/123/1.jpg"
    assert payload["attributes"] == [{"key": "颜色", "value": "红色"}]
    assert payload["warnings"] == ["warn"]
    assert payload["fail_reasons"] == ["fail"]
    assert payload["delivery_info"] == "跨境配送"
    assert payload["is_russian_local_warehouse"] is False


def test_upsert_batch_with_products_skips_when_sqlite_not_configured() -> None:
    """未配置 SQLite 时应跳过写入。"""

    repository = OzonBatchRepository(settings=Settings(SQLITE_PATH=""))

    result = repository.upsert_batch_with_products(batch={"keyword": "demo"}, products=[])

    assert result == {"status": "skipped", "reason": "sqlite_not_configured"}


def test_list_batches_returns_empty_when_sqlite_not_configured() -> None:
    """未配置 SQLite 时页面读取应安全返回空列表。"""

    repository = OzonBatchRepository(settings=Settings(SQLITE_PATH=""))

    assert repository.list_batches() == []


def test_save_batch_to_sqlite_returns_skip_when_sqlite_not_configured(tmp_path: Path) -> None:
    """未配置 SQLite 时，采集流程的批次入库应跳过而不是报错。"""

    manifest_path = tmp_path / "ozon_candidates_demo.json"
    manifest_path.write_text(
        '{"keyword":"demo","search_url":"https://example.com","excel_path":"demo.xlsx","products":[]}',
        encoding="utf-8",
    )
    pipeline = OzonCandidatePipeline(settings=Settings(SQLITE_PATH=""))

    result = pipeline.save_batch_to_sqlite(manifest_path)

    assert result["status"] == "skipped"
    assert result["reason"] == "sqlite_not_configured"


def test_import_products_saves_batch_directly_to_sqlite(tmp_path: Path) -> None:
    """采集流程应可直接写入 SQLite，而不依赖 manifest 文件。"""

    settings = Settings(SQLITE_PATH=str(tmp_path / "ozon.db"))
    repository = OzonBatchRepository(settings=settings)
    importer = OzonBatchImporter(settings=settings, repository=repository)

    result = importer.import_products(
        keyword="demo",
        search_url="https://www.ozon.ru/search/?text=demo",
        products=[
            {
                "sku": "123",
                "name": "demo product",
                "url": "https://www.ozon.ru/product/demo-123/",
                "localImagePath": "data/raw/product_images/123/1.jpg",
                "score": 9,
                "passed": True,
            }
        ],
    )

    assert result["status"] == "saved"
    assert result["total_products"] == 1
    assert str(result["source_ref"]).startswith("sqlite://ozon_collect/")

    batches = repository.list_batches()
    assert len(batches) == 1
    assert batches[0]["keyword"] == "demo"

    products = repository.list_products_for_management(keyword="demo", review_status="pending")
    assert len(products) == 1
    assert products[0]["title"] == "demo product"


def test_import_products_skips_empty_products(tmp_path: Path) -> None:
    """没有通过商品时不应创建待审核批次。"""

    settings = Settings(SQLITE_PATH=str(tmp_path / "ozon.db"))
    repository = OzonBatchRepository(settings=settings)
    importer = OzonBatchImporter(settings=settings, repository=repository)

    result = importer.import_products(
        keyword="empty-demo",
        search_url="https://www.ozon.ru/search/?text=empty-demo",
        products=[],
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "empty_products"
    assert repository.list_batches() == []


def test_delete_products_refreshes_batch_statistics(tmp_path: Path) -> None:
    """删除商品后应同步刷新批次商品数。"""

    settings = Settings(SQLITE_PATH=str(tmp_path / "ozon.db"))
    repository = OzonBatchRepository(settings=settings)
    importer = OzonBatchImporter(settings=settings, repository=repository)

    importer.import_products(
        keyword="demo",
        search_url="https://www.ozon.ru/search/?text=demo",
        products=[
            {"sku": "123", "name": "product 1", "url": "https://www.ozon.ru/product/demo-123/", "passed": True},
            {"sku": "456", "name": "product 2", "url": "https://www.ozon.ru/product/demo-456/", "passed": True},
        ],
    )

    batch = repository.list_batches()[0]
    products = repository.get_batch_products(int(batch["id"]))
    delete_result = repository.delete_products([int(products[0]["id"])])

    assert delete_result["status"] == "deleted"
    assert delete_result["deleted_count"] == 1

    refreshed_batch = repository.list_batches()[0]
    assert refreshed_batch["total_products"] == 1
