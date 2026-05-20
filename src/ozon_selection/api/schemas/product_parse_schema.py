"""商品解析结构定义。

该模块同时承担两件事：
1. 使用 Pydantic 对模型返回结果做本地校验
2. 提供一份适合 OpenAI Structured Outputs 的 JSON Schema
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


RiskTag = Literal["带电", "液体", "易碎", "超尺寸", "仿牌风险"]
ConfidenceLevel = Literal["high", "medium", "low"]

RISK_TAG_OPTIONS: tuple[str, ...] = ("带电", "液体", "易碎", "超尺寸", "仿牌风险")


class ProductParseResult(BaseModel):
    """商品采购结构化结果。"""

    model_config = ConfigDict(extra="forbid")

    category_cn: str = Field(description="商品的中文精确品类名称。")
    key_specs: dict[str, str] = Field(description="该品类最核心的规格键值对。")
    estimated_weight_g: int = Field(description="预估含包装重量，单位为克。")
    risk_tags: list[RiskTag] = Field(description="风险标签列表。")
    search_keywords: list[str] = Field(description="1688 搜索词，要求从精准到宽泛共 3 个。")
    sourcing_tips: str = Field(description="采购注意事项。")
    confidence: ConfidenceLevel = Field(description="解析置信度。")
    notes: str = Field(description="其他补充说明。")

    @field_validator("category_cn", "sourcing_tips", "notes")
    @classmethod
    def _strip_text_fields(cls, value: str) -> str:
        """统一清洗纯文本字段。"""

        return value.strip()

    @field_validator("key_specs")
    @classmethod
    def _normalize_specs(cls, value: dict[str, Any]) -> dict[str, str]:
        """把规格键值统一转为字符串，避免出现数字或空值混杂。"""

        normalized: dict[str, str] = {}
        for key, item in value.items():
            text_key = str(key).strip()
            text_value = str(item).strip()
            if text_key and text_value:
                normalized[text_key] = text_value
        return normalized

    @field_validator("risk_tags")
    @classmethod
    def _deduplicate_risk_tags(cls, value: list[RiskTag]) -> list[RiskTag]:
        """保持风险标签顺序，同时去重。"""

        deduplicated: list[RiskTag] = []
        for item in value:
            if item not in deduplicated:
                deduplicated.append(item)
        return deduplicated

    @field_validator("search_keywords")
    @classmethod
    def _strip_search_keywords(cls, value: list[str]) -> list[str]:
        """清洗搜索关键词中的空白字符。"""

        return [item.strip() for item in value if item and item.strip()]

    @model_validator(mode="after")
    def _validate_business_rules(self) -> "ProductParseResult":
        """补充无法在 OpenAI Schema 中稳定表达的业务约束。"""

        if len(self.search_keywords) != 3:
            raise ValueError("search_keywords must contain exactly 3 items")

        if self.estimated_weight_g < 0:
            raise ValueError("estimated_weight_g must be non-negative")

        return self


def build_product_parse_json_schema() -> dict[str, Any]:
    """返回给 OpenAI Structured Outputs 使用的精简 Schema。"""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "category_cn": {
                "type": "string",
                "description": "商品的中文精确品类名称，例如铅头钩、连帽卫衣、USB-C扩展坞。",
            },
            "key_specs": {
                "type": "object",
                "description": "核心规格键值对。非标品要优先抽取能直接决定采购的关键规格。",
                "additionalProperties": {"type": "string"},
            },
            "estimated_weight_g": {
                "type": "integer",
                "description": "预估含包装重量，单位克。",
            },
            "risk_tags": {
                "type": "array",
                "description": "风险标签，只能从给定候选项中选择。",
                "items": {
                    "type": "string",
                    "enum": list(RISK_TAG_OPTIONS),
                },
            },
            "search_keywords": {
                "type": "array",
                "description": "1688 搜索词，必须返回 3 个，顺序为精准到宽泛。",
                "items": {"type": "string"},
            },
            "sourcing_tips": {
                "type": "string",
                "description": "采购时需要重点核对的事项。",
            },
            "confidence": {
                "type": "string",
                "description": "解析置信度。",
                "enum": ["high", "medium", "low"],
            },
            "notes": {
                "type": "string",
                "description": "其他补充备注，没有时返回空字符串。",
            },
        },
        "required": [
            "category_cn",
            "key_specs",
            "estimated_weight_g",
            "risk_tags",
            "search_keywords",
            "sourcing_tips",
            "confidence",
            "notes",
        ],
    }
