"""按 httpx 流式方式直连 OpenAI 兼容网关的最小测试脚本。

测试目标：
1. 使用 OPENAI_BASE_URL 作为根地址
2. 使用 OPENAI_API_KEY 作为 Bearer Token
3. 直接 POST 到 /v1/chat/completions
4. 使用 stream=True 读取 SSE 响应
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings
from ozon_selection.api.clients.openai_client import OpenAIClient


logger = logging.getLogger(__name__)


def main() -> None:
    """执行一次流式 chat.completions 测试。"""

    parser = argparse.ArgumentParser(description="Test streaming /v1/chat/completions by httpx")
    parser.add_argument(
        "--prompt",
        default="请只回复 pong",
        help="发送给模型的用户消息",
    )
    parser.add_argument(
        "--system",
        default="你是一个简洁的助手。",
        help="system 消息",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=64,
        help="max_tokens",
    )
    args = parser.parse_args()

    settings = get_settings()
    base_url = OpenAIClient._extract_origin(settings.openai_base_url)
    request_path = OpenAIClient._build_chat_completions_path(settings.openai_base_url)
    api_key = settings.openai_api_key.strip()

    if not base_url:
        raise RuntimeError("未配置 OPENAI_BASE_URL")
    if not api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY")

    messages = [{"role": "user", "content": args.prompt}]

    with httpx.Client(
        base_url=base_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=settings.openai_timeout_seconds,
        follow_redirects=True,
    ) as client:
        result = call_openai(
            client=client,
            request_path=request_path,
            model=settings.openai_product_parse_model,
            messages=messages,
            system=args.system,
            max_tokens=args.max_tokens,
        )

    print("FULL_URL:", f"{base_url}{request_path}")
    print("API_KEY_MASKED:", mask_api_key(api_key))
    print("OPENAI_BASE_URL:", base_url)
    print("MODEL:", settings.openai_product_parse_model)
    print("RESULT:")
    print(result)


def call_openai(
    client: httpx.Client,
    request_path: str,
    model: str,
    messages: list[dict],
    system: str,
    max_tokens: int,
) -> str:
    """调用 OpenAI 兼容接口并解析流式返回。"""

    openai_messages: list[dict] = []
    if system:
        openai_messages.append({"role": "system", "content": system})
    openai_messages.extend(messages)

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": openai_messages,
        "stream": True,
    }

    with client.stream("POST", request_path, json=body) as response:
        if response.status_code >= 400:
            error_text = response.read().decode("utf-8", errors="replace")
            raise httpx.HTTPStatusError(
                f"HTTP {response.status_code}: {error_text[:2000]}",
                request=response.request,
                response=response,
            )

        content_parts: list[str] = []
        for line in response.iter_lines():
            line = line.strip()
            if not line or line == "data: [DONE]":
                continue

            if line.startswith("data: "):
                try:
                    chunk_data = json.loads(line[6:])
                    choices = chunk_data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        if "content" in delta and delta["content"]:
                            content_parts.append(delta["content"])
                except json.JSONDecodeError:
                    continue

        full_content = "".join(content_parts)
        logger.debug("streaming done, total chars=%s", len(full_content))
        return full_content


def mask_api_key(api_key: str) -> str:
    """返回便于核对的 API Key 掩码。"""

    normalized = api_key.strip()
    if len(normalized) <= 12:
        return normalized
    return f"{normalized[:8]}...{normalized[-4:]}"


if __name__ == "__main__":
    main()
