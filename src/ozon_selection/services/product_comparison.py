"""商品参数比对服务。"""

from __future__ import annotations

import json
import logging
import mimetypes
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError

from config.settings import Settings, get_settings
from ozon_selection.api.clients.openai_client import OpenAIClient


logger = logging.getLogger(__name__)


class ProductComparisonError(RuntimeError):
    """商品参数比对失败。"""


class ProductComparisonResult(BaseModel):
    """商品参数比对结构化结果。"""

    same_product: bool = Field(description="两边是否可视为同一件或同款商品")
    parameter_gap: Literal["small", "medium", "large"] = Field(description="核心参数差距等级")
    match_score: int = Field(ge=0, le=100, description="匹配分，100 为最接近")
    confidence: Literal["high", "medium", "low"] = Field(description="模型判断置信度")
    summary: str = Field(min_length=1, max_length=300, description="简短结论")
    difference_points: list[str] = Field(default_factory=list, description="关键差异点列表")


class ProductImageComparisonResult(BaseModel):
    """主图级别的初筛结果。"""

    same_product: bool = Field(description="根据主图判断是否可视为同一件或同款商品")
    image_match_score: int = Field(ge=0, le=100, description="主图同款匹配分，100 为最接近")
    confidence: Literal["high", "medium", "low"] = Field(description="模型判断置信度")
    summary: str = Field(min_length=1, max_length=240, description="主图比对结论")


class ProductComparisonService:
    """调用 GPT 比对 Ozon 与 1688 商品参数。"""

    def __init__(
        self,
        settings: Settings | None = None,
        openai_client: OpenAIClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.openai_client = openai_client or OpenAIClient(settings=self.settings)

    @property
    def is_configured(self) -> bool:
        """判断是否已配置可用的 OpenAI Key。"""

        api_key = (self.settings.openai_api_key or "").strip()
        return bool(api_key and api_key != "your_openai_api_key")

    def compare_products(
        self,
        *,
        ozon_product: dict[str, Any],
        supplier_product: dict[str, Any],
    ) -> dict[str, Any]:
        """返回结构化比对结果。"""

        if not self.is_configured:
            return {
                "status": "skipped",
                "reason": "openai_not_configured",
            }

        system_prompt, messages = self._build_model_input(
            ozon_product=ozon_product,
            supplier_product=supplier_product,
        )
        max_retries = max(self.settings.product_parser_json_retry_count, 0)
        last_exception: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                response = self.openai_client.stream_chat_completion(
                    system=system_prompt,
                    messages=messages,
                    max_tokens=900,
                )
                parsed = self._parse_model_output(response.output_text)
                return {
                    "status": "completed",
                    **parsed.model_dump(),
                    "parameter_match_score": parsed.match_score,
                    "response_id": response.response_id,
                }
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_exception = exc
                logger.warning(
                    "Structured product comparison failed on attempt %s/%s: %s",
                    attempt + 1,
                    max_retries + 1,
                    exc,
                )
                if attempt >= max_retries:
                    break

        raise ProductComparisonError(f"商品参数比对失败，已达到最大重试次数。最后一次错误: {last_exception}")

    def compare_product_images(
        self,
        *,
        ozon_product: dict[str, Any],
        supplier_product: dict[str, Any],
    ) -> dict[str, Any]:
        """基于 Ozon 与 1688 主图做初筛。"""

        if not self.is_configured:
            return {
                "status": "skipped",
                "reason": "openai_not_configured",
            }

        ozon_image = self.pick_ozon_image(ozon_product)
        supplier_image = self.pick_supplier_image(supplier_product)
        if not ozon_image or not supplier_image:
            return {
                "status": "skipped",
                "reason": "image_not_available",
            }

        system_prompt, messages = self._build_image_compare_input(
            ozon_product=ozon_product,
            supplier_product=supplier_product,
            ozon_image=ozon_image,
            supplier_image=supplier_image,
        )

        max_retries = max(self.settings.product_parser_json_retry_count, 0)
        last_exception: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                response = self.openai_client.stream_chat_completion(
                    system=system_prompt,
                    messages=messages,
                    max_tokens=600,
                )
                parsed = self._parse_image_compare_output(response.output_text)
                return {
                    "status": "completed",
                    **parsed.model_dump(),
                    "response_id": response.response_id,
                }
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_exception = exc
                logger.warning(
                    "Structured image comparison failed on attempt %s/%s: %s",
                    attempt + 1,
                    max_retries + 1,
                    exc,
                )
                if attempt >= max_retries:
                    break

        raise ProductComparisonError(f"商品主图比对失败，已达到最大重试次数。最后一次错误: {last_exception}")

    def _build_model_input(
        self,
        *,
        ozon_product: dict[str, Any],
        supplier_product: dict[str, Any],
    ) -> tuple[str, list[dict[str, Any]]]:
        """构造模型输入。"""

        system_prompt = (
            "你是电商选品比对助手。"
            "你需要判断 Ozon 商品和 1688 商品是否可视为同一件或同款商品，并比较核心参数差距。"
            "match_score 的含义是参数信息相似度评分，0 到 100，100 表示核心参数、规格和用途都非常接近。"
            "parameter_gap 只能返回 small、medium、large。"
            "same_product 必须严格返回 true 或 false。"
            "difference_points 只保留最关键的差异，最多 5 条。"
            "如果标题像店铺名而不是商品名，也要结合属性、重量、价格、规格综合判断。"
            "只有在款式、尺寸规格、结构用途都高度一致时，same_product 才能返回 true。"
            "不要输出 Markdown，不要解释，只返回一个合法 JSON 对象。"
            "JSON 必须包含字段：same_product、parameter_gap、match_score、confidence、summary、difference_points。"
        )

        ozon_text = "\n".join(
            [
                f"Ozon 标题: {ozon_product.get('detailTitle') or ozon_product.get('name') or '未知'}",
                f"Ozon 价格: {ozon_product.get('detailPrice') or ozon_product.get('price') or '未知'}",
                f"Ozon 主图链接: {ozon_product.get('detailImageUrl') or ozon_product.get('imageUrl') or '未知'}",
                "Ozon 商品属性:",
                self._format_attributes(ozon_product.get("attributes")),
            ]
        )
        supplier_text = "\n".join(
            [
                f"1688 标题: {supplier_product.get('title') or '未知'}",
                f"1688 价格: {supplier_product.get('price_text') or supplier_product.get('price') or '未知'}",
                f"1688 单价: {supplier_product.get('unit_price_text') or supplier_product.get('unit_price') or '未知'}",
                f"1688 重量: {supplier_product.get('weight_text') or supplier_product.get('weight_grams') or '未知'}",
                f"1688 链接: {supplier_product.get('detail_url') or '未知'}",
                "1688 商品属性:",
                self._format_attributes(supplier_product.get("attributes")),
            ]
        )

        user_text = "\n\n".join(
            [
                "请比较下面两组商品信息，判断是否为同一件或同款商品，再比较参数差距，并返回参数信息相似度评分。",
                ozon_text,
                supplier_text,
            ]
        )
        return system_prompt, [{"role": "user", "content": user_text}]

    def _build_image_compare_input(
        self,
        *,
        ozon_product: dict[str, Any],
        supplier_product: dict[str, Any],
        ozon_image: str,
        supplier_image: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        """构造主图初筛模型输入。"""

        system_prompt = (
            "你是电商商品主图比对助手。"
            "你需要根据两张商品主图判断它们是否可视为同一件或同款商品。"
            "判断应以主图内容为主，标题只作为辅助。"
            "image_match_score 是主图同款匹配分，0 到 100，100 表示主图展示的是同款或极高相似规格商品。"
            "same_product 必须严格返回 true 或 false。"
            "只有在主体商品、结构、用途、尺寸形态都高度一致时，same_product 才能返回 true。"
            "不要输出 Markdown，不要解释，只返回一个合法 JSON 对象。"
            "JSON 必须包含字段：same_product、image_match_score、confidence、summary。"
        )

        user_content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": "\n".join(
                    [
                        "请先比较主图是否是同一件或同款商品，再给出主图同款匹配分。",
                        f"Ozon 标题: {ozon_product.get('detailTitle') or ozon_product.get('name') or '未知'}",
                        f"1688 标题: {supplier_product.get('title') or '未知'}",
                        "第一张图片是 Ozon 主图，第二张图片是 1688 搜图结果主图。",
                    ]
                ),
            },
            self._build_image_message_item(ozon_image),
            self._build_image_message_item(supplier_image),
        ]
        return system_prompt, [{"role": "user", "content": user_content}]

    @staticmethod
    def _format_attributes(attributes: Any) -> str:
        """格式化属性列表，减少无关上下文。"""

        if not isinstance(attributes, list) or not attributes:
            return "无"

        lines: list[str] = []
        for item in attributes[:25]:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            value = str(item.get("value") or "").strip()
            if key and value:
                lines.append(f"- {key}: {value}")
        return "\n".join(lines) if lines else "无"

    @staticmethod
    def pick_ozon_image(product: dict[str, Any]) -> str | None:
        """优先挑选可用于 GPT 的 Ozon 主图。"""

        for key in ("localImagePath", "detailImageUrl", "imageUrl"):
            value = str(product.get(key) or "").strip()
            if value:
                return value
        return None

    @staticmethod
    def pick_supplier_image(product: dict[str, Any]) -> str | None:
        """挑选 1688 结果页主图。"""

        value = str(product.get("image_url") or "").strip()
        return value or None

    def _build_image_message_item(self, image: str) -> dict[str, Any]:
        """把本地路径或远程图片地址转成模型可读结构。"""

        if self._is_url(image):
            return {"type": "image_url", "image_url": {"url": image}}

        image_path = Path(image)
        if not image_path.exists():
            raise FileNotFoundError(f"本地图片不存在: {image_path}")
        return {"type": "image_url", "image_url": {"url": self._to_data_url(image_path)}}

    @staticmethod
    def _is_url(value: str) -> bool:
        """判断是否为 HTTP URL。"""

        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def _to_data_url(image_path: Path) -> str:
        """把本地图片转成 data URL。"""

        import base64

        mime_type = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _parse_model_output(output_text: str) -> ProductComparisonResult:
        """解析模型输出。"""

        normalized_output = output_text.strip()
        if not normalized_output:
            raise ValueError("模型返回为空")

        if normalized_output.startswith("```"):
            normalized_output = normalized_output.removeprefix("```json").removeprefix("```JSON")
            normalized_output = normalized_output.removeprefix("```").strip()
            if normalized_output.endswith("```"):
                normalized_output = normalized_output[:-3].strip()

        payload = json.loads(normalized_output)
        payload = ProductComparisonService._normalize_payload(payload)
        return ProductComparisonResult.model_validate(payload)

    @staticmethod
    def _parse_image_compare_output(output_text: str) -> ProductImageComparisonResult:
        """解析主图比对输出。"""

        normalized_output = output_text.strip()
        if not normalized_output:
            raise ValueError("模型返回为空")

        if normalized_output.startswith("```"):
            normalized_output = normalized_output.removeprefix("```json").removeprefix("```JSON")
            normalized_output = normalized_output.removeprefix("```").strip()
            if normalized_output.endswith("```"):
                normalized_output = normalized_output[:-3].strip()

        payload = json.loads(normalized_output)
        payload = ProductComparisonService._normalize_image_compare_payload(payload)
        return ProductImageComparisonResult.model_validate(payload)

    @staticmethod
    def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """兼容模型偶发的宽松输出格式。"""

        normalized = dict(payload)
        if "same_product" not in normalized and "same_category" in normalized:
            normalized["same_product"] = normalized.get("same_category")
        if "match_score" not in normalized and "parameter_match_score" in normalized:
            normalized["match_score"] = normalized.get("parameter_match_score")

        confidence = normalized.get("confidence")
        if isinstance(confidence, (int, float)):
            score = float(confidence)
            if score >= 0.8:
                normalized["confidence"] = "high"
            elif score >= 0.55:
                normalized["confidence"] = "medium"
            else:
                normalized["confidence"] = "low"
        elif isinstance(confidence, str):
            lowered = confidence.strip().lower()
            confidence_mapping = {
                "high": "high",
                "medium": "medium",
                "low": "low",
                "高": "high",
                "中": "medium",
                "低": "low",
            }
            if lowered in confidence_mapping:
                normalized["confidence"] = confidence_mapping[lowered]

        same_product = normalized.get("same_product")
        if isinstance(same_product, str):
            lowered = same_product.strip().lower()
            if lowered in {"true", "yes", "是", "同类", "同款", "同商品"}:
                normalized["same_product"] = True
            elif lowered in {"false", "no", "否", "非同类", "非同款", "不同商品"}:
                normalized["same_product"] = False

        match_score = normalized.get("match_score")
        if isinstance(match_score, str):
            digits = "".join(char for char in match_score if char.isdigit())
            if digits:
                normalized["match_score"] = int(digits)

        parameter_gap = normalized.get("parameter_gap")
        if isinstance(parameter_gap, str):
            lowered = parameter_gap.strip().lower()
            gap_mapping = {
                "small": "small",
                "medium": "medium",
                "large": "large",
                "low": "small",
                "minor": "small",
                "中": "medium",
                "中等": "medium",
                "较大": "large",
                "大": "large",
                "小": "small",
            }
            if lowered in gap_mapping:
                normalized["parameter_gap"] = gap_mapping[lowered]

        difference_points = normalized.get("difference_points")
        if isinstance(difference_points, str):
            parts = [part.strip() for part in difference_points.split("\n") if part.strip()]
            normalized["difference_points"] = parts[:5]

        return normalized

    @staticmethod
    def _normalize_image_compare_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """兼容主图比对的宽松输出格式。"""

        normalized = ProductComparisonService._normalize_payload(payload)

        image_match_score = normalized.get("image_match_score")
        if image_match_score is None and normalized.get("match_score") is not None:
            normalized["image_match_score"] = normalized.get("match_score")
            image_match_score = normalized["image_match_score"]

        if isinstance(image_match_score, str):
            digits = "".join(char for char in image_match_score if char.isdigit())
            if digits:
                normalized["image_match_score"] = int(digits)

        return normalized
