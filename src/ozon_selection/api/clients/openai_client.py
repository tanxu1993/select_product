"""OpenAI 兼容客户端封装。

该实现统一使用：
1. `httpx.Client`
2. `POST /v1/chat/completions`
3. `stream=True`

这样可以最大程度兼容官方 OpenAI 与各类 OpenAI 兼容网关。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from config.settings import Settings, get_settings


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OpenAIResponsePayload:
    """简化后的模型返回结构。"""

    output_text: str
    response_id: str | None = None


class OpenAIClient:
    """封装 OpenAI 兼容接口调用。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: httpx.Client | None = None
        self._chat_completions_path: str | None = None

    @property
    def timeout_error_cls(self) -> type[Exception]:
        """返回超时异常类型。"""

        return httpx.TimeoutException

    def _ensure_client(self) -> httpx.Client:
        """按需初始化 HTTP 客户端。"""

        if self._client is not None:
            return self._client

        api_key = self.settings.openai_api_key.strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY 未配置，无法调用 OpenAI 接口。")

        self._client = httpx.Client(
            base_url=self._extract_origin(self.settings.openai_base_url),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.settings.openai_timeout_seconds,
            follow_redirects=True,
        )
        self._chat_completions_path = self._build_chat_completions_path(self.settings.openai_base_url)
        return self._client

    @staticmethod
    def _extract_origin(base_url: str) -> str:
        """从配置中提取 origin，供 httpx.Client(base_url=...) 使用。"""

        normalized = (base_url or "").strip().rstrip("/")
        if not normalized:
            return "https://api.openai.com"

        parts = urlsplit(normalized)
        if not parts.scheme or not parts.netloc:
            return normalized
        return urlunsplit((parts.scheme, parts.netloc, "", "", ""))

    @staticmethod
    def _build_chat_completions_path(base_url: str) -> str:
        """根据配置生成完整 chat.completions 路径。"""

        normalized = (base_url or "").strip().rstrip("/")
        if not normalized:
            return "/v1/chat/completions"

        parts = urlsplit(normalized)
        prefix = parts.path.rstrip("/")
        if prefix.endswith("/v1"):
            return f"{prefix}/chat/completions"
        if prefix:
            return f"{prefix}/v1/chat/completions"
        return "/v1/chat/completions"

    def stream_chat_completion(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1200,
    ) -> OpenAIResponsePayload:
        """调用 `/v1/chat/completions` 并读取流式输出。"""

        client = self._ensure_client()
        openai_messages: list[dict[str, Any]] = []
        if system.strip():
            openai_messages.append({"role": "system", "content": system.strip()})
        openai_messages.extend(messages)

        body = {
            "model": self.settings.openai_product_parse_model,
            "max_tokens": max_tokens,
            "messages": openai_messages,
            "stream": True,
        }

        request_path = self._chat_completions_path or self._build_chat_completions_path(self.settings.openai_base_url)
        with client.stream("POST", request_path, json=body) as response:
            if response.status_code >= 400:
                error_text = response.read().decode("utf-8", errors="replace")
                raise httpx.HTTPStatusError(
                    f"HTTP {response.status_code}: {error_text[:2000]}",
                    request=response.request,
                    response=response,
                )

            response_id = response.headers.get("x-request-id") or response.headers.get("openai-request-id")
            content_type = response.headers.get("content-type", "").lower()
            if "application/json" in content_type and "text/event-stream" not in content_type:
                payload = response.json()
                output_text = self._extract_json_completion_text(payload)
                return OpenAIResponsePayload(output_text=output_text, response_id=response_id)

            content_parts: list[str] = []
            for line in response.iter_lines():
                line = line.strip()
                if not line or line == "data: [DONE]":
                    continue

                if not line.startswith("data: "):
                    continue

                try:
                    chunk_data = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue

                if chunk_data.get("id") and response_id is None:
                    response_id = chunk_data["id"]

                choices = chunk_data.get("choices", [])
                if not choices:
                    continue

                delta = choices[0].get("delta", {})
                if "content" in delta and delta["content"]:
                    content_parts.append(delta["content"])

            full_content = "".join(content_parts).strip()
            logger.debug("streaming done, total chars=%s", len(full_content))
            return OpenAIResponsePayload(output_text=full_content, response_id=response_id)

    @staticmethod
    def _extract_json_completion_text(payload: dict[str, Any]) -> str:
        """兼容非流式 JSON 响应。"""

        choices = payload.get("choices", [])
        if not choices:
            return ""

        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            texts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                    texts.append(item["text"])
            return "".join(texts).strip()

        return ""
