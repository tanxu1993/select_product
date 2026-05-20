"""从 Shopbang 历史页提取价格区间关键词。"""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import urlparse

import requests
from playwright.sync_api import Page, Playwright

from config.settings import Settings, get_settings
from ozon_selection.collectors.ozon.shopbang_auth import ShopbangLoginManager


class ShopbangHistoryKeywordCollector:
    """负责从 Shopbang 历史页提取关键词。"""

    DEFAULT_EXCLUDED_KEYWORD_FRAGMENTS = (
        "衣服",
        "服装",
        "女装",
        "男装",
        "童装",
        "女夏季",
        "女军团",
        "夏装",
        "夏季大甩卖",
        "大码女装",
        "衬衫",
        "女衬衫",
        "短上衣",
        "上衣",
        "t恤",
        "T恤",
        "背心",
        "吊带",
        "毛衣",
        "针织衫",
        "开衫",
        "卫衣",
        "夹克",
        "外套",
        "风衣",
        "羽绒服",
        "裤子",
        "女裤",
        "短裤",
        "长裤",
        "牛仔裤",
        "马裤",
        "运动裤",
        "经典长裤",
        "легинсы",
        "裙",
        "连衣裙",
        "半身裙",
        "礼服",
        "晚装",
        "束腰外衣",
        "束身衣",
        "紧身胸衣",
        "泳衣",
        "泳装",
        "比基尼",
        "沙滩束腰外衣",
        "马球",
        "芭蕾舞",
        "女夏日",
        "米莉女式",
        "сарафан",
        "萨拉凡",
        "内衣",
        "内裤",
        "文胸",
        "胸罩",
        "睡衣",
        "家居服",
        "家庭服",
        "运动服",
        "袜子",
        "短袜",
        "棉袜",
        "носки",
        "鞋子",
        "女鞋",
        "男鞋",
        "凉鞋",
        "拖鞋",
        "人字拖",
        "шлепки",
        "тапки",
        "сланцы",
        "кросовки",
        "克罗索夫基",
        "кроксы",
        "克罗克斯",
        "皮鞋",
        "运动鞋",
        "跑鞋",
        "板鞋",
        "帆布鞋",
        "高跟鞋",
        "单鞋",
        "洞洞鞋",
        "乐福鞋",
        "靴子",
        "短靴",
        "长靴",
        "雪地靴",
        "бомбер",
        "炸弹手",
        "药品",
        "手机",
    )

    KEYWORD_FIELD_CANDIDATES = (
        "keyword",
        "keyWord",
        "keywordName",
        "searchKeyword",
        "searchWord",
        "query",
        "word",
        "kw",
        "_id",
    )
    AVG_PRICE_FIELD_CANDIDATES = (
        "avgPrice",
        "averagePrice",
        "goodsAvgPrice",
        "productAvgPrice",
        "avgCaRub",
        "avg_price",
        "average_price",
    )
    PAGE_FIELD_CANDIDATES = ("pageNo", "pageNum", "page", "currentPage", "pageIndex")
    PAGE_SIZE_FIELD_CANDIDATES = ("pageSize", "size", "limit")
    FILTER_TEXT_FIELD_CANDIDATES = ("zhText", "cnText", "textZh", "titleZh", "nameZh")

    def __init__(self, settings: Settings | None = None, *, login_manager: ShopbangLoginManager | None = None) -> None:
        self.settings = settings or get_settings()
        self.login_manager = login_manager or ShopbangLoginManager(settings=self.settings)

    def collect_keywords(
        self,
        playwright: Playwright,
        *,
        min_avg_price: float | None,
        max_avg_price: float | None,
        max_pages: int,
        excluded_keywords: list[str],
        condition_label: str,
    ) -> dict[str, Any]:
        """打开历史页并提取关键词。"""

        self.login_manager.ensure_logged_in(playwright=playwright, allow_manual_fallback=True)
        session = self.login_manager.open_browser_session(playwright=playwright)
        context = session.context

        try:
            page = context.new_page()
            try:
                response_state = self.install_history_response_capture(page)
                self.open_history_page(page)
                query_ready = self.wait_for_history_query(
                    page,
                    response_state=response_state,
                    condition_label=condition_label,
                )
                records = self.collect_paginated_keyword_records(
                    page,
                    response_state=response_state,
                    min_avg_price=min_avg_price,
                    max_avg_price=max_avg_price,
                    max_pages=max_pages,
                    excluded_keywords=excluded_keywords,
                )
                unique_keywords = [str(item.get("keyword") or "").strip() for item in records]
                return {
                    "status": "completed",
                    "history_url": self.settings.shopbang_history_url,
                    "filter_result": {
                        "status": "captured",
                        "request_ready": bool(query_ready.get("request_ready")),
                        "condition_label": condition_label,
                    },
                    "request_endpoint": str(response_state.get("endpoint_url") or "").strip(),
                    "request_method": str(response_state.get("request_method") or "").strip(),
                    "request_body": dict(response_state.get("request_body") or {}),
                    "max_pages": max_pages,
                    "keyword_count": len(unique_keywords),
                    "keywords": unique_keywords,
                    "keyword_records": records,
                }
            finally:
                page.close()
        finally:
            session.close()

    def open_history_page(self, page: Page) -> None:
        """打开历史页并等待页面稳定。"""

        page.goto(
            self.settings.shopbang_history_url,
            wait_until="domcontentloaded",
            timeout=self.settings.playwright_timeout_ms,
        )
        page.wait_for_timeout(2_000)

    def install_history_response_capture(self, page: Page) -> dict[str, Any]:
        """捕获历史页接口请求与响应。"""

        state: dict[str, Any] = {
            "items": [],
            "request_body": {},
            "request_method": "",
            "endpoint_url": "",
            "request_count": 0,
            "response_count": 0,
            "seen_requests": [],
            "seen_responses": [],
            "payload_debug": {},
        }
        request_payloads: dict[str, dict[str, Any]] = {}
        request_methods: dict[str, str] = {}

        def handle_request(request) -> None:
            try:
                if request.resource_type not in {"xhr", "fetch"}:
                    return
                payload = json.loads(request.post_data or "{}")
                if not isinstance(payload, dict):
                    return
                request_payloads[request.url] = payload
                request_methods[request.url] = request.method.upper()
                if "searchReci" in request.url:
                    state["endpoint_url"] = request.url
                    state["request_body"] = dict(payload)
                    state["request_method"] = request.method.upper()
                state["request_count"] = int(state.get("request_count") or 0) + 1
                seen_requests = list(state.get("seen_requests") or [])
                seen_requests.append(
                    {
                        "url": request.url,
                        "method": request.method.upper(),
                        "payload_keys": sorted(payload.keys())[:20],
                    }
                )
                state["seen_requests"] = seen_requests[-20:]
            except Exception:
                return

        def handle_response(response) -> None:
            try:
                content_type = response.headers.get("content-type", "").lower()
                if "application/json" not in content_type:
                    return
                payload = response.json()
                if "searchReci" in response.url:
                    state["endpoint_url"] = response.url
                    state["request_body"] = dict(request_payloads.get(response.url) or {})
                    state["request_method"] = str(request_methods.get(response.url) or "POST")
                    state["payload_debug"] = self.summarize_payload(payload)
                seen_responses = list(state.get("seen_responses") or [])
                preview_keys = sorted(payload.keys())[:20] if isinstance(payload, dict) else []
                seen_responses.append(
                    {
                        "url": response.url,
                        "content_type": content_type,
                        "top_level_keys": preview_keys,
                    }
                )
                state["seen_responses"] = seen_responses[-20:]
                items = self.extract_keyword_items_from_response(payload)
                if not items:
                    return
                state["items"] = items
                state["endpoint_url"] = response.url
                state["request_body"] = dict(request_payloads.get(response.url) or {})
                state["request_method"] = str(request_methods.get(response.url) or "POST")
                state["response_count"] = int(state.get("response_count") or 0) + 1
            except Exception:
                return

        page.on("request", handle_request)
        page.on("response", handle_response)
        return state

    def wait_for_history_query(
        self,
        page: Page,
        *,
        response_state: dict[str, Any],
        condition_label: str,
    ) -> dict[str, Any]:
        """等待历史页筛选查询完成。"""

        if self.settings.shopbang_headless:
            raise RuntimeError("当前为后台模式，但 Shopbang 历史页关键词脚本需要人工选择筛选条件并点击查询。")

        initial_request_count = int(response_state.get("request_count") or 0)
        initial_response_count = int(response_state.get("response_count") or 0)

        print(
            f"请在浏览器中设置 Shopbang 历史页筛选条件：{condition_label}，然后点击查询。",
            flush=True,
        )
        input("查询完成后按 Enter 继续抓取关键词...")

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            request_body = dict(response_state.get("request_body") or {})
            endpoint_url = str(response_state.get("endpoint_url") or "").strip()
            request_count = int(response_state.get("request_count") or 0)
            response_count = int(response_state.get("response_count") or 0)
            if endpoint_url and request_body and (
                request_count > initial_request_count or response_count > initial_response_count
            ):
                return {"request_ready": True}
            page.wait_for_timeout(1_000)

        raise TimeoutError(self.build_timeout_message(response_state))

    def collect_paginated_keyword_records(
        self,
        page: Page,
        *,
        response_state: dict[str, Any],
        min_avg_price: float,
        max_avg_price: float,
        max_pages: int,
        excluded_keywords: list[str],
    ) -> list[dict[str, Any]]:
        """基于已捕获请求体分页抓取关键词。"""

        endpoint_url = str(response_state.get("endpoint_url") or "").strip()
        request_body = dict(response_state.get("request_body") or {})
        request_method = str(response_state.get("request_method") or "POST").strip().upper()
        if not endpoint_url or not request_body:
            return []

        normalized_exclusions = self.build_excluded_keyword_fragments(excluded_keywords)
        page_size = self.read_page_size(request_body)
        initial_page_no = self.read_page_no(request_body)

        seen_keywords: set[str] = set()
        records: list[dict[str, Any]] = []

        def add_items(items: list[dict[str, Any]], current_page: int) -> None:
            for item in items:
                record = self.build_keyword_record(
                    item,
                    source_page=current_page,
                    source_endpoint=endpoint_url,
                    min_avg_price=min_avg_price,
                    max_avg_price=max_avg_price,
                    excluded_keywords=normalized_exclusions,
                )
                if record is None:
                    continue
                keyword = str(record.get("keyword") or "").strip()
                if keyword in seen_keywords:
                    continue
                seen_keywords.add(keyword)
                records.append(record)

        initial_items = list(response_state.get("items") or [])
        if initial_items:
            add_items(initial_items, initial_page_no)
            print(
                f"[shopbang-history] fetched page {initial_page_no}, keywords={len(records)}, page_size={page_size}",
                flush=True,
            )

        next_page = max(initial_page_no + 1, 1)
        for page_no in range(next_page, max(max_pages, initial_page_no) + 1):
            items = self.fetch_history_items_by_page(
                page,
                endpoint_url=endpoint_url,
                request_method=request_method,
                request_body=request_body,
                page_no=page_no,
            )
            if not items:
                break

            before_count = len(records)
            add_items(items, page_no)
            after_count = len(records)
            print(
                f"[shopbang-history] fetched page {page_no}, new_keywords={after_count - before_count}, total_keywords={after_count}",
                flush=True,
            )
            if len(items) < page_size:
                break

        return records

    def fetch_history_items_by_page(
        self,
        page: Page,
        *,
        endpoint_url: str,
        request_method: str,
        request_body: dict[str, Any],
        page_no: int,
    ) -> list[dict[str, Any]]:
        """按页码重放历史页接口。"""

        payload = self.update_page_no(request_body, page_no=page_no)
        cookies = {
            cookie.get("name"): cookie.get("value")
            for cookie in page.context.cookies(["https://plus.shopbang.cn", "https://shopbang.cn"])
            if cookie.get("name") and cookie.get("value") is not None
        }
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://shopbang.cn",
            "Referer": self.settings.shopbang_history_url,
        }
        try:
            if request_method == "GET":
                response = requests.get(
                    endpoint_url,
                    params=payload,
                    cookies=cookies,
                    headers=headers,
                    timeout=30,
                )
            else:
                response = requests.post(
                    endpoint_url,
                    json=payload,
                    cookies=cookies,
                    headers=headers,
                    timeout=30,
                )
            response.raise_for_status()
            response_payload = response.json()
        except Exception as exc:
            print(f"[shopbang-history] fetch page {page_no} failed: {exc}", flush=True)
            return []
        return self.extract_keyword_items_from_response(response_payload)

    def build_keyword_record(
        self,
        item: dict[str, Any],
        *,
        source_page: int,
        source_endpoint: str,
        min_avg_price: float,
        max_avg_price: float,
        excluded_keywords: list[str],
    ) -> dict[str, Any] | None:
        """把接口项转成待入库关键词记录。"""

        keyword = self.extract_keyword_from_item(item)
        if not keyword:
            return None
        if self.should_exclude_item(item=item, excluded_keywords=excluded_keywords, keyword=keyword):
            return None

        return {
            "keyword": keyword,
            "avg_price": self.extract_avg_price_from_item(item),
            "source_page": source_page,
            "source_endpoint": source_endpoint,
            "price_min": min_avg_price,
            "price_max": max_avg_price,
            "source_count": 1,
            "filters": {
                "min_avg_price": min_avg_price,
                "max_avg_price": max_avg_price,
            },
            "raw_payload": item,
        }

    @classmethod
    def extract_keyword_items_from_response(cls, payload: Any) -> list[dict[str, Any]]:
        """从任意 JSON 响应中递归提取包含关键词的列表。"""

        candidates: list[tuple[int, int, list[dict[str, Any]]]] = []

        def walk(node: Any) -> None:
            if isinstance(node, list):
                dict_items = [item for item in node if isinstance(item, dict)]
                if dict_items:
                    keyword_hits = sum(1 for item in dict_items[:30] if cls.extract_keyword_from_item(item))
                    price_hits = sum(1 for item in dict_items[:30] if cls.extract_avg_price_from_item(item) is not None)
                    score = keyword_hits * 10 + price_hits
                    if score > 0:
                        candidates.append((score, len(dict_items), dict_items))
                for item in node:
                    walk(item)
            elif isinstance(node, dict):
                for value in node.values():
                    walk(value)

        walk(payload)
        if not candidates:
            return []

        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][2]

    @classmethod
    def summarize_payload(cls, payload: Any) -> dict[str, Any]:
        """生成用于调试的响应结构摘要。"""

        summary: dict[str, Any] = {
            "top_level_keys": sorted(payload.keys())[:20] if isinstance(payload, dict) else [],
            "list_candidates": [],
        }

        def walk(node: Any, path: str) -> None:
            if isinstance(node, list):
                dict_items = [item for item in node if isinstance(item, dict)]
                if dict_items:
                    sample = dict_items[0]
                    summary["list_candidates"].append(
                        {
                            "path": path,
                            "length": len(node),
                            "sample_keys": sorted(sample.keys())[:30],
                            "sample_preview": {
                                str(key): sample.get(key)
                                for key in sorted(sample.keys())[:8]
                            },
                        }
                    )
                for index, item in enumerate(node[:3]):
                    walk(item, f"{path}[{index}]")
            elif isinstance(node, dict):
                for key, value in list(node.items())[:20]:
                    child_path = f"{path}.{key}" if path else str(key)
                    walk(value, child_path)

        walk(payload, "")
        return summary

    @classmethod
    def extract_keyword_from_item(cls, item: dict[str, Any]) -> str:
        """从接口项中提取关键词。"""

        for field_name in cls.KEYWORD_FIELD_CANDIDATES:
            keyword = cls.normalize_keyword(item.get(field_name))
            if keyword:
                return keyword

        for key, value in item.items():
            if "keyword" not in str(key).lower() and "word" not in str(key).lower():
                continue
            keyword = cls.normalize_keyword(value)
            if keyword:
                return keyword
        return ""

    @classmethod
    def extract_avg_price_from_item(cls, item: dict[str, Any]) -> float | None:
        """从接口项中提取平均价格。"""

        for field_name in cls.AVG_PRICE_FIELD_CANDIDATES:
            parsed = cls.parse_number(item.get(field_name))
            if parsed is not None:
                return parsed

        for key, value in item.items():
            key_text = str(key).lower()
            if "price" not in key_text:
                continue
            if "avg" not in key_text and "average" not in key_text:
                continue
            parsed = cls.parse_number(value)
            if parsed is not None:
                return parsed
        return None

    @classmethod
    def build_timeout_message(self, response_state: dict[str, Any]) -> str:
        """构建超时调试信息。"""

        return (
            "等待历史页查询结果超时，未捕获到关键词接口请求。"
            f" seen_request_count={len(response_state.get('seen_requests') or [])}"
            f" seen_response_count={len(response_state.get('seen_responses') or [])}"
            f" seen_requests={json.dumps(list(response_state.get('seen_requests') or [])[-5:], ensure_ascii=False)}"
            f" seen_responses={json.dumps(list(response_state.get('seen_responses') or [])[-5:], ensure_ascii=False)}"
            f" endpoint_url={response_state.get('endpoint_url') or ''}"
            f" request_body={json.dumps(response_state.get('request_body') or {}, ensure_ascii=False)}"
            f" payload_debug={json.dumps(response_state.get('payload_debug') or {}, ensure_ascii=False)}"
        )

    @classmethod
    def read_page_no(cls, payload: dict[str, Any]) -> int:
        """读取当前请求体中的页码。"""

        for field_name in cls.PAGE_FIELD_CANDIDATES:
            value = payload.get(field_name)
            if value is None:
                continue
            try:
                return max(int(value), 1)
            except Exception:
                continue
        return 1

    @classmethod
    def read_page_size(cls, payload: dict[str, Any]) -> int:
        """读取当前请求体中的每页条数。"""

        for field_name in cls.PAGE_SIZE_FIELD_CANDIDATES:
            value = payload.get(field_name)
            if value is None:
                continue
            try:
                return max(int(value), 1)
            except Exception:
                continue
        return 20

    @classmethod
    def update_page_no(cls, payload: dict[str, Any], *, page_no: int) -> dict[str, Any]:
        """更新请求体页码。"""

        updated = json.loads(json.dumps(payload, ensure_ascii=False))
        for field_name in cls.PAGE_FIELD_CANDIDATES:
            if field_name in updated:
                updated[field_name] = page_no
                return updated
        updated["pageNo"] = page_no
        return updated

    @staticmethod
    def normalize_keyword(value: Any) -> str:
        """清洗关键词文本。"""

        text = str(value or "").strip()
        text = re.sub(r"\s+", " ", text)
        return text

    @classmethod
    def extract_filter_texts_from_item(cls, item: dict[str, Any], *, keyword: str = "") -> list[str]:
        """提取用于排除匹配的文本。"""

        texts: list[str] = []
        if keyword:
            texts.append(cls.normalize_keyword(keyword))

        for field_name in cls.FILTER_TEXT_FIELD_CANDIDATES:
            value = cls.normalize_keyword(item.get(field_name))
            if value:
                texts.append(value)

        return texts

    @classmethod
    def should_exclude_item(cls, *, item: dict[str, Any], excluded_keywords: list[str], keyword: str = "") -> bool:
        """判断当前接口项是否应被排除。"""

        normalized_texts = [text.lower() for text in cls.extract_filter_texts_from_item(item, keyword=keyword) if text]
        for text in normalized_texts:
            if any(token and token in text for token in excluded_keywords):
                return True
        return False

    @classmethod
    def build_excluded_keyword_fragments(cls, extra_keywords: list[str] | None = None) -> list[str]:
        """构造有效的排除词片段列表。"""

        merged_keywords = list(cls.DEFAULT_EXCLUDED_KEYWORD_FRAGMENTS)
        if extra_keywords:
            merged_keywords.extend(extra_keywords)

        normalized_items: list[str] = []
        seen_items: set[str] = set()
        for item in merged_keywords:
            normalized = cls.normalize_keyword(item).lower()
            if not normalized or normalized in seen_items:
                continue
            seen_items.add(normalized)
            normalized_items.append(normalized)
        return normalized_items

    @staticmethod
    def parse_number(value: Any) -> float | None:
        """从混合文案中提取数字。"""

        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)

        text = str(value).strip()
        if not text:
            return None
        normalized = text.replace("\xa0", "").replace(" ", "")
        if "," in normalized and "." not in normalized:
            comma_index = normalized.rfind(",")
            suffix = normalized[comma_index + 1:]
            if suffix.isdigit() and len(suffix) == 3:
                normalized = normalized.replace(",", "")
            else:
                normalized = normalized.replace(",", ".")
        elif "," in normalized and "." in normalized:
            normalized = normalized.replace(",", "")
        matched = re.search(r"[+-]?\d+(?:\.\d+)?", normalized)
        if not matched:
            return None
        try:
            return float(matched.group(0))
        except Exception:
            return None

    @staticmethod
    def is_url(value: str) -> bool:
        """判断文本是否是 URL。"""

        parsed = urlparse(str(value or "").strip())
        return bool(parsed.scheme and parsed.netloc)
