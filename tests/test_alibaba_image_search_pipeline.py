"""1688 图搜图流水线测试。"""

from pathlib import Path
import pytest

from openpyxl import load_workbook

from config.settings import Settings
from ozon_selection.services import alibaba_image_search_pipeline as pipeline_module
from ozon_selection.services.alibaba_image_search_pipeline import AlibabaImageSearchPipeline


def test_limit_products_returns_prefix_when_limit_is_positive() -> None:
    """确保调试模式会截断 Ozon 商品列表。"""

    products = [{"sku": "1"}, {"sku": "2"}, {"sku": "3"}]

    result = AlibabaImageSearchPipeline.limit_products(products, 2)

    assert result == [{"sku": "1"}, {"sku": "2"}]


def test_limit_products_returns_all_when_limit_is_none() -> None:
    """未设置限制时应保留全部商品。"""

    products = [{"sku": "1"}, {"sku": "2"}]

    result = AlibabaImageSearchPipeline.limit_products(products, None)

    assert result == products


def test_format_attributes_returns_joined_text() -> None:
    """确保 1688 属性会被格式化为可读文本。"""

    value = AlibabaImageSearchPipeline.format_attributes(
        [
            {"key": "颜色", "value": "红色"},
            {"key": "材质", "value": "硅胶"},
        ]
    )

    assert value == "颜色: 红色 | 材质: 硅胶"


def test_format_string_list_returns_joined_text() -> None:
    """确保 GPT 差异点会被格式化为可读文本。"""

    value = AlibabaImageSearchPipeline.format_string_list(["长度不同", "重量不同"])

    assert value == "长度不同 | 重量不同"


def test_select_best_image_match_items_only_keeps_top_scored_result() -> None:
    """每个 Ozon 商品只应保留主图分最高的一条结果。"""

    pipeline = AlibabaImageSearchPipeline()

    result = pipeline.select_best_image_match_items(
        [
            {"ai_image_comparison": {"status": "completed", "same_product": True, "image_match_score": 83}},
            {"ai_image_comparison": {"status": "completed", "same_product": True, "image_match_score": 79}},
            {"ai_image_comparison": {"status": "completed", "same_product": False, "image_match_score": 95}},
        ]
    )

    assert len(result) == 1
    assert result[0]["ai_image_comparison"]["image_match_score"] == 95


def test_build_database_rows_includes_supplier_attributes() -> None:
    """确保 supplier_links payload 会带上 1688 属性和主图比对结果。"""

    rows = AlibabaImageSearchPipeline.build_database_rows(
        [
            {
                "ozon_product": {
                    "sku": "1",
                    "name": "demo",
                    "url": "https://www.ozon.ru/product/demo-1/",
                    "imageUrl": "https://ozon.example/1.jpg",
                    "localImagePath": "/tmp/1.jpg",
                    "price": 100,
                },
                "image_search": {
                    "items": [
                        {
                            "title": "supplier",
                            "detail_url": "https://detail.1688.com/offer/1.html",
                            "attributes": [{"key": "颜色", "value": "红色"}],
                            "ai_image_comparison": {
                                "status": "completed",
                                "same_product": True,
                                "image_match_score": 87,
                                "confidence": "high",
                                "summary": "主图同类。",
                            },
                        }
                    ]
                },
            }
        ]
    )

    assert rows[0]["supplier_attributes"] == [{"key": "颜色", "value": "红色"}]
    assert rows[0]["ai_image_same_product"] is True
    assert rows[0]["ai_image_match_score"] == 87
    assert "ai_same_product" not in rows[0]
    assert "ai_parameter_match_score" not in rows[0]


def test_build_failed_search_result_keeps_batch_running_shape() -> None:
    """单个商品图搜失败时应返回空结果占位，不中断整批结构。"""

    result = AlibabaImageSearchPipeline.build_failed_search_result(
        product={"sku": "1", "localImagePath": "/tmp/demo.jpg"},
        error="上传图片后未找到“搜索图片”按钮，无法提交 1688 图搜图。",
    )

    assert result["ozon_product"]["sku"] == "1"
    assert result["image_search"]["items"] == []
    assert "搜索图片" in result["image_search"]["search_error"]


def test_build_excel_rows_includes_image_comparison_only() -> None:
    """确保 Excel 行只保留主图比对结果，不再输出参数 GPT 比对。"""

    rows = AlibabaImageSearchPipeline.build_excel_rows(
        [
            {
                "ozon_product": {
                    "sku": "1",
                    "name": "demo",
                    "url": "https://www.ozon.ru/product/demo-1/",
                    "localImagePath": "/tmp/1.jpg",
                    "attributes": [{"key": "长度", "value": "7cm"}],
                },
                "image_search": {
                    "items": [
                        {
                            "title": "supplier",
                            "detail_url": "https://detail.1688.com/offer/1.html",
                            "attributes": [{"key": "规格", "value": "7cm"}],
                            "ai_image_comparison": {
                                "status": "completed",
                                "same_product": True,
                                "image_match_score": 87,
                                "confidence": "high",
                                "summary": "主图同类。",
                            },
                        }
                    ]
                },
            }
        ]
    )

    assert rows[0]["GPT主图状态"] == "completed"
    assert rows[0]["GPT主图同款分"] == 87
    assert "GPT状态" not in rows[0]
    assert "GPT参数相似分" not in rows[0]


def test_export_to_excel_merges_repeated_ozon_columns(tmp_path: Path) -> None:
    """同一 Ozon 商品对应多条 1688 结果时应合并重复的 Ozon 列。"""

    pipeline = AlibabaImageSearchPipeline(
        settings=Settings(
            OZON_SCRAPE_OUTPUT_DIR=str(tmp_path),
        )
    )
    rows = [
        {
            "Ozon SKU": "sku-1",
            "Ozon商品": "ozon a",
            "Ozon属性数": 1,
            "Ozon商品属性": "颜色: 红色",
            "Ozon链接": "https://www.ozon.ru/product/a/",
            "Ozon主图路径": "/tmp/a.jpg",
            "1688序号": 1,
            "1688标题": "item-1",
        },
        {
            "Ozon SKU": "sku-1",
            "Ozon商品": "ozon a",
            "Ozon属性数": 1,
            "Ozon商品属性": "颜色: 红色",
            "Ozon链接": "https://www.ozon.ru/product/a/",
            "Ozon主图路径": "/tmp/a.jpg",
            "1688序号": 2,
            "1688标题": "item-2",
        },
        {
            "Ozon SKU": "sku-2",
            "Ozon商品": "ozon b",
            "Ozon属性数": 2,
            "Ozon商品属性": "材质: TPU",
            "Ozon链接": "https://www.ozon.ru/product/b/",
            "Ozon主图路径": "/tmp/b.jpg",
            "1688序号": 1,
            "1688标题": "item-3",
        },
        {
            "Ozon SKU": "sku-2",
            "Ozon商品": "ozon b",
            "Ozon属性数": 2,
            "Ozon商品属性": "材质: TPU",
            "Ozon链接": "https://www.ozon.ru/product/b/",
            "Ozon主图路径": "/tmp/b.jpg",
            "1688序号": 2,
            "1688标题": "item-4",
        },
    ]

    output_path = pipeline.export_to_excel(rows)

    workbook = load_workbook(output_path)
    sheet = workbook["1688图搜图"]
    merged_ranges = {str(item) for item in sheet.merged_cells.ranges}

    assert "A2:A3" in merged_ranges
    assert "F2:F3" in merged_ranges
    assert "A4:A5" in merged_ranges
    assert "F4:F5" in merged_ranges


def test_run_checks_1688_login_before_loading_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """执行图搜图前应先做 1688 登录状态检查。"""

    events: list[str] = []

    class DummyContext:
        pass

    class DummySession:
        def __init__(self) -> None:
            self.context = DummyContext()

        def close(self) -> None:
            events.append("session_close")

    class DummyBrowserSearch:
        def ensure_ready_context(self, playwright):
            events.append("login_preflight")
            return DummySession(), Path("/tmp/auth-state-1688.json")

    class DummyPlaywrightContextManager:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(pipeline_module, "sync_playwright", lambda: DummyPlaywrightContextManager())

    pipeline = AlibabaImageSearchPipeline(browser_search=DummyBrowserSearch())  # type: ignore[arg-type]

    def fake_load_source_products(manifest_path):
        events.append("load_source")
        raise RuntimeError("stop_after_load")

    pipeline.load_source_products = fake_load_source_products  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="stop_after_load"):
        pipeline.run()

    assert events[:2] == ["login_preflight", "load_source"]
