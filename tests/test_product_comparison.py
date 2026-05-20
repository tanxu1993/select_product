"""商品参数比对服务测试。"""

from __future__ import annotations

from config.settings import Settings
from ozon_selection.api.clients.openai_client import OpenAIResponsePayload
from ozon_selection.services.product_comparison import ProductComparisonService


class DummyOpenAIClient:
    """模拟 OpenAI 返回。"""

    def __init__(self, output_text: str) -> None:
        self.output_text = output_text

    def stream_chat_completion(self, **_kwargs):
        return OpenAIResponsePayload(output_text=self.output_text, response_id="resp_123")


def test_compare_products_returns_structured_result() -> None:
    """确保比对结果会被解析为稳定结构。"""

    service = ProductComparisonService(
        settings=Settings(OPENAI_API_KEY="test-key"),
        openai_client=DummyOpenAIClient(
            """
            {
              "same_product": true,
              "parameter_gap": "small",
              "match_score": 92,
              "confidence": "high",
              "summary": "同类商品，规格接近。",
              "difference_points": ["1688 重量信息更明确", "Ozon 标题更偏零售文案"]
            }
            """
        ),
    )

    result = service.compare_products(
        ozon_product={"name": "路亚软饵", "attributes": [{"key": "长度", "value": "7cm"}]},
        supplier_product={"title": "路亚软虫", "attributes": [{"key": "规格", "value": "7cm"}]},
    )

    assert result["status"] == "completed"
    assert result["same_product"] is True
    assert result["parameter_gap"] == "small"
    assert result["match_score"] == 92
    assert result["parameter_match_score"] == 92
    assert result["response_id"] == "resp_123"


def test_compare_products_skips_when_openai_not_configured() -> None:
    """未配置 OpenAI 时应跳过比对。"""

    service = ProductComparisonService(settings=Settings(OPENAI_API_KEY=""))

    result = service.compare_products(ozon_product={"name": "A"}, supplier_product={"title": "B"})

    assert result == {"status": "skipped", "reason": "openai_not_configured"}


def test_compare_product_images_returns_structured_result() -> None:
    """确保主图初筛结果会被解析为稳定结构。"""

    service = ProductComparisonService(
        settings=Settings(OPENAI_API_KEY="test-key"),
        openai_client=DummyOpenAIClient(
            """
            {
              "same_product": true,
              "image_match_score": 86,
              "confidence": "high",
              "summary": "主图展示的是同一类软饵。"
            }
            """
        ),
    )

    result = service.compare_product_images(
        ozon_product={"imageUrl": "https://ozon.example/1.jpg", "name": "路亚软饵"},
        supplier_product={"image_url": "https://1688.example/1.jpg", "title": "软虫假饵"},
    )

    assert result["status"] == "completed"
    assert result["same_product"] is True
    assert result["image_match_score"] == 86


def test_parse_image_compare_output_normalizes_match_score() -> None:
    """确保主图比对输出能兼容宽松格式。"""

    result = ProductComparisonService._parse_image_compare_output(
        """
        {
          "same_product": "是",
          "match_score": "84分",
          "confidence": 0.78,
          "summary": "属于同类商品。"
        }
        """
    )

    assert result.same_product is True
    assert result.image_match_score == 84
    assert result.confidence == "medium"


def test_parse_model_output_normalizes_numeric_confidence() -> None:
    """确保数值型 confidence 会被归一化。"""

    result = ProductComparisonService._parse_model_output(
        """
        {
          "same_product": "是",
          "parameter_gap": "小",
          "match_score": "88分",
          "confidence": 0.82,
          "summary": "基本同类。",
          "difference_points": "包装不同\\n重量标注不同"
        }
        """
    )

    assert result.same_product is True
    assert result.parameter_gap == "small"
    assert result.match_score == 88
    assert result.confidence == "high"
    assert result.difference_points == ["包装不同", "重量标注不同"]


def test_parse_model_output_accepts_legacy_same_category_field() -> None:
    """兼容模型偶发返回旧字段名。"""

    result = ProductComparisonService._parse_model_output(
        """
        {
          "same_category": "是",
          "parameter_gap": "small",
          "match_score": 90,
          "confidence": "high",
          "summary": "可视为同款。",
          "difference_points": []
        }
        """
    )

    assert result.same_product is True


def test_parse_model_output_accepts_parameter_match_score_alias() -> None:
    """兼容模型偶发返回参数相似分字段。"""

    result = ProductComparisonService._parse_model_output(
        """
        {
          "same_product": true,
          "parameter_gap": "small",
          "parameter_match_score": 89,
          "confidence": "high",
          "summary": "参数接近。",
          "difference_points": []
        }
        """
    )

    assert result.match_score == 89
