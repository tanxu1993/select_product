"""1688 第三方 API 客户端。

设计目标：
1. 尽量兼容不同第三方 1688 图搜图接口
2. 配置驱动，避免把某一家服务商的字段名写死在代码里
3. 提供统一的规范化结果，方便后续补全与比价
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from config.settings import Settings, get_settings


class Api1688Client:
    """封装 1688 数据查询逻辑。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: httpx.Client | None = None

    def _ensure_client(self) -> httpx.Client:
        """按需初始化 HTTP 客户端。"""

        if self._client is not None:
            return self._client

        if not self.settings.api1688_base_url.strip():
            raise RuntimeError("未配置 API1688_BASE_URL，无法调用 1688 图搜图接口。")

        self._client = httpx.Client(
            base_url=self.settings.api1688_base_url.strip().rstrip("/"),
            timeout=self.settings.api1688_timeout_seconds,
            follow_redirects=True,
        )
        return self._client

    def fetch_supplier_data(self, keyword: str) -> dict:
        """兼容旧占位入口。"""

        return {"status": "todo", "keyword": keyword}

    def search_products_by_image(
        self,
        *,
        image_url: str | None = None,
        image_bytes: bytes | None = None,
        page: int = 1,
        page_size: int | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """调用 1688 图搜图接口。

        支持两种常见模式：
        1. `image_url`: 接口直接读取远程图片
        2. `image_base64`: 先下载图片，再把 base64 字符串发给接口
        """

        payload_mode = self.settings.api1688_image_search_payload_mode.strip().lower()
        if payload_mode not in {"image_url", "image_base64"}:
            raise ValueError("API1688_IMAGE_SEARCH_PAYLOAD_MODE 仅支持 image_url 或 image_base64。")

        if payload_mode == "image_url" and not image_url:
            raise ValueError("当前 1688 图搜图模式为 image_url，但未提供 image_url。")

        if payload_mode == "image_base64" and not image_bytes:
            raise ValueError("当前 1688 图搜图模式为 image_base64，但未提供 image_bytes。")

        client = self._ensure_client()
        request_path = self._normalize_path(self.settings.api1688_image_search_path)
        params, headers = self._build_auth()
        payload = self._build_image_search_payload(
            image_url=image_url,
            image_bytes=image_bytes,
            page=page,
            page_size=page_size or self.settings.api1688_image_search_default_page_size,
            extra_params=extra_params or {},
        )

        method = self.settings.api1688_image_search_method.strip().upper()
        response = client.request(
            method=method,
            url=request_path,
            params=params,
            headers=headers,
            json=payload if method in {"POST", "PUT", "PATCH"} else None,
            data=payload if method not in {"POST", "PUT", "PATCH"} else None,
        )
        response.raise_for_status()

        raw_payload = response.json()
        normalized_items = self._normalize_image_search_items(raw_payload)
        return {
            "request": {
                "path": request_path,
                "method": method,
                "payload_mode": payload_mode,
                "page": page,
                "page_size": page_size or self.settings.api1688_image_search_default_page_size,
            },
            "raw": raw_payload,
            "items": normalized_items,
        }

    def _build_auth(self) -> tuple[dict[str, Any], dict[str, str]]:
        """根据配置构造认证参数。"""

        mode = self.settings.api1688_auth_mode.strip().lower()
        params: dict[str, Any] = {}
        headers: dict[str, str] = {}

        if mode in {"query", "both"}:
            if self.settings.api1688_app_key:
                params["app_key"] = self.settings.api1688_app_key
            if self.settings.api1688_app_secret:
                params["app_secret"] = self.settings.api1688_app_secret

        if mode in {"headers", "both"}:
            if self.settings.api1688_app_key:
                headers["X-App-Key"] = self.settings.api1688_app_key
            if self.settings.api1688_app_secret:
                headers["X-App-Secret"] = self.settings.api1688_app_secret

        return params, headers

    def _build_image_search_payload(
        self,
        *,
        image_url: str | None,
        image_bytes: bytes | None,
        page: int,
        page_size: int,
        extra_params: dict[str, Any],
    ) -> dict[str, Any]:
        """构造图搜图请求体。"""

        payload = {
            self.settings.api1688_image_search_page_field: page,
            self.settings.api1688_image_search_page_size_field: page_size,
        }
        payload.update(extra_params)

        payload_mode = self.settings.api1688_image_search_payload_mode.strip().lower()
        if payload_mode == "image_url":
            payload[self.settings.api1688_image_search_image_url_field] = image_url
        else:
            assert image_bytes is not None
            payload[self.settings.api1688_image_search_image_base64_field] = base64.b64encode(image_bytes).decode(
                "ascii"
            )

        return payload

    def _normalize_image_search_items(self, raw_payload: dict[str, Any]) -> list[dict[str, Any]]:
        """把第三方接口结果规范化。"""

        items = self._extract_by_path(raw_payload, self.settings.api1688_image_search_results_path)
        if not isinstance(items, list):
            items = self._find_first_list_candidate(raw_payload)
        if not isinstance(items, list):
            return []

        return [self._normalize_single_item(item) for item in items if isinstance(item, dict)]

    @staticmethod
    def _normalize_single_item(item: dict[str, Any]) -> dict[str, Any]:
        """抽取最常见的 1688 商品字段。"""

        return {
            "offer_id": Api1688Client._pick(item, "offerId", "offer_id", "id", "goods_id", "productId"),
            "title": Api1688Client._pick(item, "title", "subject", "name", "productTitle"),
            "detail_url": Api1688Client._pick(
                item,
                "detailUrl",
                "detail_url",
                "offerUrl",
                "offer_url",
                "url",
            ),
            "image_url": Api1688Client._pick(
                item,
                "imageUrl",
                "image_url",
                "picUrl",
                "pic_url",
                "mainPic",
            ),
            "price": Api1688Client._pick(
                item,
                "price",
                "priceInfo.price",
                "promotionPrice",
                "promotion_price",
            ),
            "min_order": Api1688Client._pick(
                item,
                "minOrderQuantity",
                "min_order_quantity",
                "moq",
                "minOrder",
            ),
            "seller": Api1688Client._pick(
                item,
                "sellerName",
                "seller_name",
                "companyName",
                "shopName",
            ),
            "raw": item,
        }

    @staticmethod
    def _pick(payload: dict[str, Any], *paths: str) -> Any:
        """按顺序尝试读取多个字段路径。"""

        for path in paths:
            value = Api1688Client._extract_by_path(payload, path)
            if value not in (None, "", []):
                return value
        return None

    @staticmethod
    def _extract_by_path(payload: Any, path: str) -> Any:
        """按 `a.b.c` 形式提取嵌套字段。"""

        current = payload
        for segment in path.split("."):
            if isinstance(current, dict) and segment in current:
                current = current[segment]
            else:
                return None
        return current

    @staticmethod
    def _find_first_list_candidate(payload: Any) -> list[Any] | None:
        """递归寻找最像商品列表的节点。"""

        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for value in payload.values():
                found = Api1688Client._find_first_list_candidate(value)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _normalize_path(path: str) -> str:
        """把路径标准化为以 `/` 开头。"""

        stripped = (path or "").strip()
        if not stripped:
            return "/image_search"
        return stripped if stripped.startswith("/") else f"/{stripped}"
