"""商品解析服务。

核心目标：
1. 接收标题、图片、规格、价格
2. 调用 OpenAI / GPT-5.4 进行图文理解
3. 输出稳定、可落库的结构化采购信息
"""

from __future__ import annotations

import json
import logging
import mimetypes
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from config.settings import Settings, get_settings
from ozon_selection.api.clients.openai_client import OpenAIClient
from ozon_selection.api.schemas.product_parse_schema import (
    ProductParseResult,
)


logger = logging.getLogger(__name__)
SUPPORTED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}


class ProductParseError(RuntimeError):
    """商品解析失败。"""


class ProductParseTimeoutError(ProductParseError):
    """商品解析超时。"""


class ProductParserService:
    """负责调用多模态模型解析商品信息。"""

    def __init__(
        self,
        settings: Settings | None = None,
        openai_client: OpenAIClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.openai_client = openai_client or OpenAIClient(settings=self.settings)

    def parse_product(
        self,
        *,
        title: str,
        image: str | None = None,
        specs: Any = None,
        price: str | float | int | None = None,
    ) -> dict[str, Any]:
        """解析单个商品并返回结构化 JSON。"""

        if not title or not title.strip():
            raise ValueError("title is required")

        if not self.settings.openai_api_key:
            raise ProductParseError("未配置 OPENAI_API_KEY，无法调用商品解析模型。")

        notes: list[str] = []
        image_input = None
        if image:
            try:
                image_input = self._build_image_input(image=image)
            except Exception as exc:
                logger.warning("Failed to prepare product image, fallback to text-only: %s", exc)
                notes.append(f"图片不可用，已降级为纯文本分析：{exc}")

        system_prompt, user_messages = self._build_model_input(
            title=title.strip(),
            specs=specs,
            price=price,
            image_input=image_input,
        )
        max_retries = max(self.settings.product_parser_json_retry_count, 0)
        last_exception: Exception | None = None
        timeout_error_cls = self.openai_client.timeout_error_cls

        for attempt in range(max_retries + 1):
            try:
                response = self.openai_client.stream_chat_completion(
                    system=system_prompt,
                    messages=user_messages,
                )
                parsed = self._parse_model_output(response.output_text)
                if notes:
                    parsed.notes = self._merge_notes(parsed.notes, notes)
                return parsed.model_dump()
            except timeout_error_cls as exc:
                raise ProductParseTimeoutError(
                    f"调用 OpenAI 超时，请检查网络或提高 OPENAI_TIMEOUT_SECONDS。原始错误: {exc}"
                ) from exc
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_exception = exc
                logger.warning(
                    "Structured product parse failed on attempt %s/%s: %s",
                    attempt + 1,
                    max_retries + 1,
                    exc,
                )
                if attempt >= max_retries:
                    break

        raise ProductParseError(f"商品解析失败，已达到最大重试次数。最后一次错误: {last_exception}")

    def _build_model_input(
        self,
        *,
        title: str,
        specs: Any,
        price: str | float | int | None,
        image_input: dict[str, Any] | None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """拼装 chat.completions 需要的 system 和 messages。"""

        system_prompt = (
            "你是跨境电商采购分析助手。"
            "请根据商品标题、图片、规格和价格，输出严谨的采购结构化 JSON。"
            "category_cn 必须是中文且足够精确。"
            "key_specs 只保留影响采购和物流决策的核心规格。"
            "对于非标品，要优先提取该品类的关键参数，例如："
            "铅头钩提取克重和钩号，鱼竿提取长度和调性，服装提取版型、材质和尺码，"
            "电子配件提取接口、功率、电压、电流、协议、材质或尺寸。"
            "risk_tags 只能从以下标签中选择：带电、液体、易碎、超尺寸、仿牌风险。"
            "search_keywords 必须严格输出 3 个，顺序从精准到宽泛，适合用于 1688 搜索。"
            "如果图片无帮助，也要基于文本尽最大可能给出可靠判断。"
            "不要输出 Markdown，不要输出解释性文字，只返回一个合法 JSON 对象。"
            "JSON 必须包含以下字段："
            "category_cn、key_specs、estimated_weight_g、risk_tags、search_keywords、"
            "sourcing_tips、confidence、notes。"
            "其中 key_specs 必须是对象，estimated_weight_g 必须是整数，"
            "risk_tags 必须是数组，search_keywords 必须严格为 3 个字符串，"
            "confidence 只能是 high、medium、low。"
        )

        user_text = "\n".join(
            [
                f"标题: {title}",
                f"价格: {price if price is not None else '未知'}",
                "规格参数:",
                self._format_specs(specs),
            ]
        )

        user_content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        if image_input is not None:
            user_content.append(image_input)

        return system_prompt, [
            {
                "role": "user",
                "content": user_content,
            }
        ]

    def _build_image_input(self, *, image: str) -> dict[str, Any]:
        """把远程图片或本地图片转换成 chat.completions 可读的 data URL。"""

        if self._is_url(image):
            image_bytes, mime_type = self._download_remote_image(image)
        else:
            image_bytes, mime_type = self._read_local_image(Path(image))

        data_url = self._to_data_url(image_bytes=image_bytes, mime_type=mime_type)
        return {"type": "image_url", "image_url": {"url": data_url}}

    def _download_remote_image(self, image_url: str) -> tuple[bytes, str]:
        """下载远程图片，并附带防盗链常用请求头。"""

        headers = {
            "User-Agent": self.settings.product_parser_image_user_agent or self.settings.ozon_user_agent,
            "Referer": self.settings.product_parser_image_referer or self.settings.ozon_base_url,
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": self.settings.product_parser_image_accept_language,
        }
        headers = {key: value for key, value in headers.items() if value}

        timeout = self.settings.product_parser_image_download_timeout_seconds
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = client.get(image_url)
            response.raise_for_status()

        mime_type = self._resolve_mime_type(
            source=image_url,
            content_type=response.headers.get("content-type", ""),
        )
        self._validate_supported_mime_type(mime_type)
        return response.content, mime_type

    @staticmethod
    def _read_local_image(image_path: Path) -> tuple[bytes, str]:
        """读取本地图片。"""

        if not image_path.exists():
            raise FileNotFoundError(f"本地图片不存在: {image_path}")

        image_bytes = image_path.read_bytes()
        mime_type = ProductParserService._resolve_mime_type(source=str(image_path), content_type="")
        ProductParserService._validate_supported_mime_type(mime_type)
        return image_bytes, mime_type

    @staticmethod
    def _to_data_url(*, image_bytes: bytes, mime_type: str) -> str:
        """把图片字节流转成 Base64 data URL。"""

        import base64

        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _resolve_mime_type(*, source: str, content_type: str) -> str:
        """尽可能准确地推断图片 MIME 类型。"""

        if content_type:
            mime_type = content_type.split(";")[0].strip()
            if mime_type.startswith("image/"):
                return mime_type

        guessed, _ = mimetypes.guess_type(source)
        if guessed and guessed.startswith("image/"):
            return guessed

        return "image/jpeg"

    @staticmethod
    def _validate_supported_mime_type(mime_type: str) -> None:
        """限制到视觉模型稳定支持的图片格式。"""

        if mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
            raise ValueError(f"不支持的图片格式: {mime_type}")

    @staticmethod
    def _is_url(value: str) -> bool:
        """判断字符串是否为 HTTP URL。"""

        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def _parse_model_output(output_text: str) -> ProductParseResult:
        """解析并校验模型输出。"""

        if not output_text.strip():
            raise ValueError("模型返回为空")

        normalized_output = output_text.strip()
        if normalized_output.startswith("```"):
            normalized_output = normalized_output.removeprefix("```json").removeprefix("```JSON")
            normalized_output = normalized_output.removeprefix("```").strip()
            if normalized_output.endswith("```"):
                normalized_output = normalized_output[:-3].strip()

        parsed_json = json.loads(normalized_output)
        return ProductParseResult.model_validate(parsed_json)

    @staticmethod
    def _merge_notes(existing_notes: str, extra_notes: Iterable[str]) -> str:
        """合并模型备注与运行时备注。"""

        merged: list[str] = []
        if existing_notes.strip():
            merged.append(existing_notes.strip())
        for note in extra_notes:
            text = str(note).strip()
            if text and text not in merged:
                merged.append(text)
        return "；".join(merged)

    @staticmethod
    def _format_specs(specs: Any) -> str:
        """把不同格式的规格参数归一化成文本。"""

        if specs is None:
            return "- 未提供"

        if isinstance(specs, dict):
            lines = [f"- {key}: {value}" for key, value in specs.items()]
            return "\n".join(lines) if lines else "- 未提供"

        if isinstance(specs, (list, tuple)):
            normalized_lines: list[str] = []
            for item in specs:
                if isinstance(item, dict):
                    key = item.get("key") or item.get("name") or item.get("label") or "规格"
                    value = item.get("value") or item.get("text") or item.get("content") or ""
                    normalized_lines.append(f"- {key}: {value}".rstrip())
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    normalized_lines.append(f"- {item[0]}: {item[1]}")
                else:
                    normalized_lines.append(f"- {item}")
            return "\n".join(normalized_lines) if normalized_lines else "- 未提供"

        return f"- {specs}"


def parse_product(
    *,
    title: str,
    image: str | None = None,
    specs: Any = None,
    price: str | float | int | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """模块级快捷入口，便于脚本或其他任务直接调用。"""

    service = ProductParserService(settings=settings)
    return service.parse_product(title=title, image=image, specs=specs, price=price)
