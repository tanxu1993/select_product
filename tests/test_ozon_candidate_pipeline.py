"""Ozon 候选商品流水线测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from config.settings import get_settings
from ozon_selection.services.ozon_candidate_pipeline import OzonCandidatePipeline


def test_build_candidate_payload_maps_expected_fields() -> None:
    """确保候选商品 payload 字段映射稳定。"""

    payload = OzonCandidatePipeline.build_candidate_payload(
        {
            "sku": "123",
            "name": "demo",
            "url": "https://www.ozon.ru/product/demo-123/",
            "imageUrl": "https://image.example/demo.jpg",
            "localImagePath": "data/raw/product_images/123/1.jpg",
            "detailTitle": "demo detail",
            "detailPrice": 999,
            "detailImageUrl": "https://image.example/detail.jpg",
            "attributes": [{"key": "颜色", "value": "红色"}],
            "price": 1000,
            "score": 8,
            "warnings": ["warn"],
            "failReasons": [],
        }
    )

    assert payload["source_product_id"] == "123"
    assert payload["title"] == "demo"
    assert payload["image_path"] == "data/raw/product_images/123/1.jpg"
    assert payload["detail_title"] == "demo detail"
    assert payload["detail_price"] == 999
    assert payload["attributes"] == [{"key": "颜色", "value": "红色"}]
    assert payload["passed"] is True


def test_find_latest_manifest_returns_newest_file(tmp_path: Path) -> None:
    """确保能找到最新的 Ozon 清单。"""

    older = tmp_path / "ozon_candidates_old.json"
    newer = tmp_path / "ozon_candidates_new.json"
    older.write_text(json.dumps({"products": []}), encoding="utf-8")
    newer.write_text(json.dumps({"products": []}), encoding="utf-8")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    result = OzonCandidatePipeline.find_latest_manifest(tmp_path)

    assert result == newer


def test_filter_qualified_products_keeps_only_passed_items() -> None:
    """确保只保留 passed=true 的商品。"""

    result = OzonCandidatePipeline.filter_qualified_products(
        [
            {"sku": "1", "passed": True, "score": 8},
            {"sku": "2", "passed": False, "score": 10},
            {"sku": "3", "passed": True, "score": 6},
        ]
    )

    assert [item["sku"] for item in result] == ["1", "3"]


def test_restore_settings_from_payload_preserves_runtime_overrides() -> None:
    """子进程恢复设置时，不应退回 `.env` 中的旧值。"""

    settings = get_settings().model_copy(
        deep=True,
        update={
            "ozon_scrape_target_products": 1,
            "ozon_keyword_timeout_seconds": 0,
        },
    )

    restored = OzonCandidatePipeline.restore_settings_from_payload(settings.model_dump())

    assert restored.ozon_scrape_target_products == 1
    assert restored.ozon_keyword_timeout_seconds == 0


def test_run_downloads_images_only_for_qualified_products(tmp_path: Path) -> None:
    """图片下载应只发生在筛选通过的商品上。"""

    class DummyCollector:
        def __init__(self) -> None:
            self.downloaded_skus: list[str] = []
            self.enriched_skus: list[str] = []
            self.exported_rows: list[dict] = []
            self.exported_keyword = ""

        def build_search_url(self, keyword: str) -> str:
            return f"https://example.com/search?q={keyword}"

        def scrape_products(self, search_url: str) -> list[dict]:
            assert search_url == "https://example.com/search?q=demo"
            return [
                {"sku": "pass-1", "name": "pass", "imageUrl": "https://img/pass.jpg"},
                {"sku": "fail-1", "name": "fail", "imageUrl": "https://img/fail.jpg"},
            ]

        def evaluate(self, product: dict) -> object:
            class Result:
                def __init__(self, passed: bool) -> None:
                    self.score = 10 if passed else 0
                    self.warns: list[str] = []
                    self.fails = [] if passed else ["boom"]
                    self.shipping = None
                    self.max_cost = None
                    self.tier = None

            return Result(product["sku"] == "pass-1")

        def save_product_images(self, products: list[dict], image_cache: dict | None = None) -> None:
            self.downloaded_skus = [str(item["sku"]) for item in products]
            for product in products:
                product["localImagePath"] = str(tmp_path / f"{product['sku']}.jpg")

        def enrich_products_with_attributes(self, products: list[dict]) -> list[dict]:
            self.enriched_skus = [str(item["sku"]) for item in products]
            return products

        def build_result_rows(self, products: list[dict]) -> list[dict]:
            return [
                {
                    "SKU": product["sku"],
                    "结果": "✅ 通过" if product.get("passed") else "❌ 未通过",
                    "红线原因": " | ".join(product.get("failReasons") or []),
                }
                for product in products
            ]

        def export_to_excel(self, rows: list[dict], keyword: str) -> str:
            self.exported_rows = rows
            self.exported_keyword = keyword
            return str(tmp_path / "demo.xlsx")

    class DummyRepository:
        table_name = "product_candidates"

        def save_many(self, payloads: list[dict]) -> dict:
            return {"status": "skipped", "count": len(payloads)}

    class DummyKeywordPoolRepository:
        pass

    pipeline = OzonCandidatePipeline(
        collector=DummyCollector(),  # type: ignore[arg-type]
        repository=DummyRepository(),  # type: ignore[arg-type]
        keyword_pool_repository=DummyKeywordPoolRepository(),  # type: ignore[arg-type]
    )
    pipeline.save_products_to_sqlite = lambda **kwargs: {"status": "success", "count": 1, **kwargs}  # type: ignore[assignment]

    result = pipeline.run("demo")

    assert result["total_collected"] == 2
    assert result["qualified_count"] == 1
    assert result["products"][0]["sku"] == "pass-1"
    assert pipeline.collector.downloaded_skus == ["pass-1"]  # type: ignore[attr-defined]
    assert pipeline.collector.enriched_skus == ["pass-1"]  # type: ignore[attr-defined]
    assert pipeline.collector.exported_keyword == "demo"  # type: ignore[attr-defined]
    assert [row["SKU"] for row in pipeline.collector.exported_rows] == ["pass-1", "fail-1"]  # type: ignore[attr-defined]
    assert result["excel_path"].endswith("demo.xlsx")
    assert result["products"][0]["localImagePath"].endswith("pass-1.jpg")


def test_run_for_keywords_calls_each_keyword_once(tmp_path: Path) -> None:
    """多关键词模式应按顺序逐个执行。"""

    pipeline = OzonCandidatePipeline()
    called_keywords: list[str] = []
    checkpoint_path = tmp_path / "ozon_keyword_checkpoint.json"

    def fake_run(keyword: str) -> dict:
        called_keywords.append(str(keyword))
        return {"keyword": keyword, "sqlite_result": {}, "database_result": {}}

    pipeline.run_keyword_with_timeout = fake_run  # type: ignore[assignment]
    pipeline.ensure_shopbang_login = lambda: None  # type: ignore[assignment]
    pipeline.get_keyword_checkpoint_path = lambda: checkpoint_path  # type: ignore[assignment]

    result = pipeline.run_for_keywords(["A", "B", "C"])

    assert called_keywords == ["A", "B", "C"]
    assert result["success_count"] == 3
    assert result["failure_count"] == 0


def test_run_for_keywords_collects_failures(tmp_path: Path) -> None:
    """某个关键词失败时，不应阻断后续关键词。"""

    pipeline = OzonCandidatePipeline()
    checkpoint_path = tmp_path / "ozon_keyword_checkpoint.json"

    def fake_run(keyword: str) -> dict:
        if keyword == "B":
            raise RuntimeError("boom")
        return {"keyword": keyword, "sqlite_result": {}, "database_result": {}}

    pipeline.run_keyword_with_timeout = fake_run  # type: ignore[assignment]
    pipeline.ensure_shopbang_login = lambda: None  # type: ignore[assignment]
    pipeline.get_keyword_checkpoint_path = lambda: checkpoint_path  # type: ignore[assignment]

    result = pipeline.run_for_keywords(["A", "B", "C"])

    assert [item["keyword"] for item in result["results"]] == ["A", "C"]
    assert result["failure_count"] == 1
    assert result["failures"][0]["keyword"] == "B"


def test_run_for_keywords_skips_completed_keywords(tmp_path: Path) -> None:
    """重新执行时应跳过 checkpoint 中已完成的关键词。"""

    pipeline = OzonCandidatePipeline()
    called_keywords: list[str] = []
    checkpoint_path = tmp_path / "ozon_keyword_checkpoint.json"
    checkpoint_path.write_text(
        json.dumps({"completed_keywords": ["A"]}, ensure_ascii=False),
        encoding="utf-8",
    )

    def fake_run(keyword: str) -> dict:
        called_keywords.append(str(keyword))
        return {"keyword": keyword, "sqlite_result": {}, "database_result": {}}

    pipeline.run_keyword_with_timeout = fake_run  # type: ignore[assignment]
    pipeline.ensure_shopbang_login = lambda: None  # type: ignore[assignment]
    pipeline.get_keyword_checkpoint_path = lambda: checkpoint_path  # type: ignore[assignment]

    result = pipeline.run_for_keywords(["A", "B", "C"])

    assert called_keywords == ["B", "C"]
    assert result["skipped_count"] == 1
    assert result["skipped_keywords"] == ["A"]


def test_run_for_keywords_uses_sqlite_pool_when_manual_keywords_missing() -> None:
    """未手动传入关键词时，应从 SQLite 关键词池随机取词。"""

    pipeline = OzonCandidatePipeline()
    called_keywords: list[str] = []

    class DummyKeywordPoolRepository:
        def __init__(self) -> None:
            self.marked: list[tuple[str, str, str]] = []

        def pick_random_unused_keywords(self, *, limit: int) -> list[str]:
            assert limit == 2
            return ["池词A", "池词B"]

        def mark_keyword_used(self, *, keyword: str, status: str, error: str = "") -> dict:
            self.marked.append((keyword, status, error))
            return {"status": "updated"}

    dummy_repository = DummyKeywordPoolRepository()

    def fake_run(keyword: str | None = None) -> dict:
        called_keywords.append(str(keyword))
        return {
            "keyword": keyword,
            "qualified_count": 1,
            "sqlite_result": {},
            "database_result": {},
        }

    pipeline.run_keyword_with_timeout = fake_run  # type: ignore[assignment]
    pipeline.ensure_shopbang_login = lambda: None  # type: ignore[assignment]
    pipeline.keyword_pool_repository = dummy_repository  # type: ignore[assignment]

    result = pipeline.run_for_keywords(None, pool_count=2)

    assert called_keywords == ["池词A", "池词B"]
    assert result["keyword_source"] == "sqlite_pool"
    assert result["checkpoint_path"] == ""
    assert dummy_repository.marked == [
        ("池词A", "success", ""),
        ("池词B", "success", ""),
    ]


def test_run_for_keywords_marks_pool_keyword_failed_and_continues() -> None:
    """关键词池模式下，失败关键词也应回写状态且不阻断后续关键词。"""

    pipeline = OzonCandidatePipeline()

    class DummyKeywordPoolRepository:
        def __init__(self) -> None:
            self.marked: list[tuple[str, str, str]] = []

        def pick_random_unused_keywords(self, *, limit: int) -> list[str]:
            return ["池词A", "池词B"]

        def mark_keyword_used(self, *, keyword: str, status: str, error: str = "") -> dict:
            self.marked.append((keyword, status, error))
            return {"status": "updated"}

    dummy_repository = DummyKeywordPoolRepository()

    def fake_run(keyword: str) -> dict:
        if keyword == "池词A":
            raise RuntimeError("boom")
        return {
            "keyword": keyword,
            "qualified_count": 0,
            "sqlite_result": {},
            "database_result": {},
        }

    pipeline.run_keyword_with_timeout = fake_run  # type: ignore[assignment]
    pipeline.ensure_shopbang_login = lambda: None  # type: ignore[assignment]
    pipeline.keyword_pool_repository = dummy_repository  # type: ignore[assignment]

    result = pipeline.run_for_keywords(None, pool_count=2)

    assert result["failure_count"] == 1
    assert [item["keyword"] for item in result["results"]] == ["池词B"]
    assert dummy_repository.marked == [
        ("池词A", "failed", "boom"),
        ("池词B", "success_empty", ""),
    ]


def test_run_for_keywords_treats_timeout_as_failure_and_continues(tmp_path: Path) -> None:
    """单关键词超时后应跳过并继续后续关键词。"""

    pipeline = OzonCandidatePipeline()
    checkpoint_path = tmp_path / "ozon_keyword_checkpoint.json"

    def fake_run(keyword: str) -> dict:
        if keyword == "A":
            raise TimeoutError("keyword_timeout_after_600s")
        return {"keyword": keyword, "sqlite_result": {}, "database_result": {}}

    pipeline.run_keyword_with_timeout = fake_run  # type: ignore[assignment]
    pipeline.ensure_shopbang_login = lambda: None  # type: ignore[assignment]
    pipeline.get_keyword_checkpoint_path = lambda: checkpoint_path  # type: ignore[assignment]

    result = pipeline.run_for_keywords(["A", "B"])

    assert [item["keyword"] for item in result["results"]] == ["B"]
    assert result["failure_count"] == 1
    assert result["failures"] == [{"keyword": "A", "error": "keyword_timeout_after_600s"}]
