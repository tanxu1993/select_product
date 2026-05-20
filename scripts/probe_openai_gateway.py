"""探测 OpenAI 兼容网关可用性的最小脚本。

目标：
1. 验证当前 OPENAI_BASE_URL 是否可达
2. 验证网关是否支持 `/models`
3. 验证网关是否支持 `/responses`
4. 验证网关是否支持 `/chat/completions`
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings


def main() -> None:
    """执行网关探测，并保存结果。"""

    settings = get_settings()
    base_url = settings.openai_base_url.strip().rstrip("/")
    api_key = settings.openai_api_key.strip()

    if not base_url:
        raise RuntimeError("未配置 OPENAI_BASE_URL")
    if not api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    probes = [
        {
            "name": "root_get",
            "method": "GET",
            "path": "",
            "json": None,
        },
        {
            "name": "models_get",
            "method": "GET",
            "path": "/models",
            "json": None,
        },
        {
            "name": "responses_post",
            "method": "POST",
            "path": "/responses",
            "json": {
                "model": settings.openai_product_parse_model,
                "input": "Reply with the single word pong.",
                "max_output_tokens": 16,
            },
        },
        {
            "name": "chat_completions_post",
            "method": "POST",
            "path": "/chat/completions",
            "json": {
                "model": settings.openai_product_parse_model,
                "messages": [
                    {"role": "user", "content": "Reply with the single word pong."},
                ],
                "max_tokens": 16,
            },
        },
    ]

    results: list[dict] = []
    with httpx.Client(timeout=settings.openai_timeout_seconds, follow_redirects=True) as client:
        for probe in probes:
            results.append(run_probe(client, base_url, headers, probe))

    output_dir = settings.product_parser_test_output_path
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"openai_gateway_probe_{time.strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"OPENAI_BASE_URL: {base_url}")
    for result in results:
        print(
            f"[{result['name']}] {result['method']} {result['url']} -> "
            f"{result.get('status_code', 'error')}"
        )
        if result.get("error"):
            print(f"  error: {result['error']}")
        elif result.get("body_preview"):
            print(f"  body: {result['body_preview']}")
    print(f"Saved probe result to: {output_path}")


def run_probe(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    probe: dict,
) -> dict:
    """执行单个探测请求。"""

    url = urljoin(f"{base_url}/", probe["path"].lstrip("/"))
    result = {
        "name": probe["name"],
        "method": probe["method"],
        "url": url,
    }

    try:
        response = client.request(
            method=probe["method"],
            url=url,
            headers=headers,
            json=probe["json"],
        )
        body_text = response.text.strip()
        result["status_code"] = response.status_code
        result["headers"] = dict(response.headers)
        result["body_preview"] = body_text[:1000]
    except Exception as exc:
        result["error"] = str(exc)

    return result


if __name__ == "__main__":
    main()
