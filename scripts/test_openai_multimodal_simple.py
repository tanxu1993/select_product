"""最简单的图文 OpenAI 调用测试脚本。

实现方式与已验证可用的文本脚本保持一致：
1. `httpx.Client`
2. `POST /v1/chat/completions`
3. `stream=True`
4. 文本 + 图片一起发送
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings
from ozon_selection.api.clients.openai_client import OpenAIClient


def main() -> None:
    """执行一次最小图文调用。"""

    parser = argparse.ArgumentParser(description="Simple multimodal /v1/chat/completions test")
    parser.add_argument(
        "--image",
        default=str(PROJECT_ROOT / "data/raw/product_parser_test_images/usb_c_hub.png"),
        help="图片本地路径或图片 URL",
    )
    parser.add_argument(
        "--prompt",
        default="请用中文简要描述这张图片里的商品，并提取最关键的 3 个规格点。",
        help="发送给模型的文本提示词",
    )
    parser.add_argument(
        "--system",
        default="你是一个简洁的商品识别助手。",
        help="system 消息",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=300,
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

    image_url = build_image_input_url(
        image=args.image,
        user_agent=settings.product_parser_image_user_agent or settings.ozon_user_agent,
        referer=settings.product_parser_image_referer or settings.ozon_base_url,
        timeout_seconds=settings.product_parser_image_download_timeout_seconds,
        accept_language=settings.product_parser_image_accept_language,
    )

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": args.prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ]

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
    print("IMAGE:", args.image)
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
    """调用 `/v1/chat/completions` 并解析流式返回。"""

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

            if not line.startswith("data: "):
                continue

            try:
                chunk_data = json.loads(line[6:])
            except json.JSONDecodeError:
                continue

            choices = chunk_data.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta", {})
            if "content" in delta and delta["content"]:
                content_parts.append(delta["content"])

        return "".join(content_parts).strip()


def build_image_input_url(
    *,
    image: str,
    user_agent: str,
    referer: str,
    timeout_seconds: int,
    accept_language: str,
) -> str:
    """把本地图片转成 data URL，远程图片则直接透传 URL。

    这样可以区分：
    1. 网关是否支持视觉输入
    2. 网关是否只是拦截了大体积 base64 data URL
    """

    if is_http_url(image):
        return image

    path = Path(image)
    if not path.exists():
        raise FileNotFoundError(f"图片不存在: {path}")
    image_bytes = path.read_bytes()
    mime_type = guess_mime_type(str(path), "")
    return bytes_to_data_url(image_bytes=image_bytes, mime_type=mime_type)


def download_image_as_bytes(
    *,
    image_url: str,
    user_agent: str,
    referer: str,
    timeout_seconds: int,
    accept_language: str,
) -> tuple[bytes, str]:
    """下载远程图片。"""

    headers = {
        "User-Agent": user_agent,
        "Referer": referer,
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": accept_language,
    }
    headers = {key: value for key, value in headers.items() if value}

    with httpx.Client(timeout=timeout_seconds, follow_redirects=True, headers=headers) as client:
        response = client.get(image_url)
        response.raise_for_status()
        return response.content, guess_mime_type(image_url, response.headers.get("content-type", ""))


def bytes_to_data_url(*, image_bytes: bytes, mime_type: str) -> str:
    """把图片字节流编码成 data URL。"""

    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def guess_mime_type(source: str, content_type: str) -> str:
    """推断图片 MIME 类型。"""

    if content_type:
        mime_type = content_type.split(";")[0].strip()
        if mime_type.startswith("image/"):
            return mime_type

    guessed, _ = mimetypes.guess_type(source)
    if guessed and guessed.startswith("image/"):
        return guessed

    return "image/jpeg"


def is_http_url(value: str) -> bool:
    """判断是否为 HTTP URL。"""

    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def mask_api_key(api_key: str) -> str:
    """返回便于核对的 API Key 掩码。"""

    normalized = api_key.strip()
    if len(normalized) <= 12:
        return normalized
    return f"{normalized[:8]}...{normalized[-4:]}"


if __name__ == "__main__":
    main()
