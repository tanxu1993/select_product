"""从上品帮热销页提取可用于 Ozon 采集的关键词。"""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.parse import urljoin

import requests
from playwright.sync_api import BrowserContext, Page, Playwright

from config.settings import Settings, get_settings
from ozon_selection.collectors.ozon.shopbang_auth import ShopbangLoginManager
from ozon_selection.repositories.ozon_keyword_pool_repository import OzonKeywordPoolRepository
from ozon_selection.repositories.shopbang_hot_category_progress_repository import (
    ShopbangHotCategoryProgressRepository,
)


class ShopbangHotKeywordCollector:
    """负责从上品帮热销页提取上一级和上两级关键词。"""

    EXCLUDED_CATEGORY_KEYWORDS = ("服装", "电子产品", "食品", "药品")
    EXCLUDED_CATEGORY_ALIASES = {
        "服装": ("服装", "服饰", "clothing", "apparel", "fashion", "odezhda", "одежда"),
        "电子产品": ("电子产品", "electronics", "electronic", "elektronika", "электроника"),
        "食品": ("食品", "food", "food products", "produkty pitaniya", "продукты питания"),
        "药品": ("药品", "pharmacy", "medicine", "medicines", "apteka", "аптека"),
    }
    GENERIC_BREADCRUMB_TEXTS = {
        "首页",
        "热销",
        "热卖",
        "爆款",
        "商品详情",
        "详情",
        "返回",
        "上品帮",
        "shopbang",
        "ozon",
    }

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        login_manager: ShopbangLoginManager | None = None,
        keyword_pool_repository: OzonKeywordPoolRepository | None = None,
        progress_repository: ShopbangHotCategoryProgressRepository | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.login_manager = login_manager or ShopbangLoginManager(settings=self.settings)
        self.keyword_pool_repository = keyword_pool_repository or OzonKeywordPoolRepository(settings=self.settings)
        self.progress_repository = progress_repository or ShopbangHotCategoryProgressRepository(settings=self.settings)

    def collect_keywords(
        self,
        playwright: Playwright,
        *,
        max_pages: int | None = None,
    ) -> dict[str, Any]:
        """打开热销页并提取上级类目关键词。"""

        self.login_manager.ensure_logged_in(playwright=playwright, allow_manual_fallback=True)
        session = self.login_manager.open_browser_session(playwright=playwright)
        context = session.context
        try:
            page = context.new_page()
            try:
                response_state = self.install_remai_response_capture(page)
                self.open_remai_page(page)
                categories = self.collect_remai_categories(page)
                processed_urls = set(self.keyword_pool_repository.list_processed_source_urls())
                seen_urls: set[str] = set()
                manual_selection = self.wait_for_manual_category_selection(page, response_state=response_state)
                category_name = str(manual_selection.get("category_name") or "").strip()

                if self.is_excluded_category_text(category_name):
                    return {
                        "status": "skipped",
                        "remai_url": self.settings.shopbang_remai_url,
                        "filter_result": {
                            "status": "skipped",
                            "reason": "excluded_category",
                            "selected_category": category_name,
                        },
                        "category_count": len(categories),
                        "processed_url_count": len(processed_urls),
                        "detail_candidates": 0,
                        "skipped_entries": 0,
                        "keyword_count": 0,
                        "keywords": [],
                        "keyword_records": [],
                        "processed_categories": [],
                        "selected_category": category_name,
                        "resume_from_page": 1,
                        "last_completed_page": 0,
                        "progress_status": "skipped",
                    }

                progress = self.progress_repository.get_progress(category_name=category_name) or {}
                resume_from_page = max(int(progress.get("last_completed_page") or 0) + 1, 1)
                print(
                    f"[shopbang-hot] selected_category={category_name} resume_from_page={resume_from_page}",
                    flush=True,
                )
                category_results = self.collect_keywords_for_selected_category(
                    context=context,
                    page=page,
                    response_state=response_state,
                    category_name=category_name,
                    processed_urls=processed_urls,
                    seen_urls=seen_urls,
                    max_pages=max_pages,
                    resume_from_page=resume_from_page,
                )
                self.progress_repository.save_progress(
                    category_name=category_name,
                    request_body=category_results.get("request_body") or manual_selection.get("request_body") or {},
                    last_completed_page=int(category_results.get("last_completed_page") or 0),
                    last_page_size=int(category_results.get("last_page_size") or 0),
                    status=str(category_results.get("progress_status") or "completed"),
                    error="",
                )

                return {
                    "status": "completed",
                    "remai_url": self.settings.shopbang_remai_url,
                    "filter_result": {
                        "status": "manual_selection",
                        "selected_category": category_name,
                    },
                    "category_count": len(categories),
                    "processed_url_count": len(processed_urls),
                    "detail_candidates": int(category_results["detail_candidates"]),
                    "skipped_entries": int(category_results["skipped_entries"]),
                    "keyword_count": len(category_results["keywords"]),
                    "keywords": category_results["keywords"],
                    "keyword_records": category_results["keyword_records"],
                    "processed_categories": category_results["processed_categories"],
                    "selected_category": category_name,
                    "resume_from_page": resume_from_page,
                    "last_completed_page": int(category_results.get("last_completed_page") or 0),
                    "progress_status": category_results.get("progress_status") or "completed",
                }
            except Exception as exc:
                category_name = ""
                try:
                    selected_categories = self.read_selected_remai_categories(page)
                    category_name = selected_categories[0] if selected_categories else ""
                except Exception:
                    category_name = ""
                if category_name:
                    existing_progress = self.progress_repository.get_progress(category_name=category_name) or {}
                    self.progress_repository.save_progress(
                        category_name=category_name,
                        request_body=response_state.get("request_body") or {},
                        last_completed_page=int(existing_progress.get("last_completed_page") or 0),
                        last_page_size=int(existing_progress.get("last_page_size") or 0),
                        status="failed",
                        error=str(exc),
                    )
                raise
            finally:
                page.close()
        finally:
            session.close()

    def open_remai_page(self, page: Page) -> None:
        """打开热销页并等待页面稳定。"""

        page.goto(
            self.settings.shopbang_remai_url,
            wait_until="domcontentloaded",
            timeout=self.settings.playwright_timeout_ms,
        )
        page.wait_for_timeout(2_000)

    def wait_for_manual_category_selection(
        self,
        page: Page,
        *,
        response_state: dict[str, Any],
        timeout_ms: int = 600_000,
    ) -> dict[str, Any]:
        """等待人工在页面上选择一级类目并点击查询。"""

        if self.settings.shopbang_headless:
            raise RuntimeError(
                "当前启用了后台模式，但热销关键词脚本需要你在上品帮页面手动选择类目并点击“查询”。"
                " 这一步无法后台完成，请前台运行该脚本。"
            )

        initial_request_count = int(response_state.get("request_count") or 0)
        initial_response_count = int(response_state.get("response_count") or 0)
        print(
            "[shopbang-hot] 请在浏览器中手动选择 1 个一级类目并点击“查询”，脚本会在捕获到查询结果后继续。",
            flush=True,
        )
        try:
            page.bring_to_front()
        except Exception:
            pass

        deadline = time.monotonic() + max(timeout_ms, 1) / 1000
        while time.monotonic() < deadline:
            selected_categories = self.read_selected_remai_categories(page)
            if len(selected_categories) > 1:
                raise ValueError(f"当前检测到多个已选类目: {', '.join(selected_categories)}，请只保留一个一级类目。")

            request_count = int(response_state.get("request_count") or 0)
            response_count = int(response_state.get("response_count") or 0)
            request_body = dict(response_state.get("request_body") or {})

            if (
                selected_categories
                and request_count > initial_request_count
                and response_count > initial_response_count
                and request_body
            ):
                return {
                    "category_name": selected_categories[0],
                    "request_body": request_body,
                    "request_count": request_count,
                    "response_count": response_count,
                    "response_items_count": len(response_state.get("items") or []),
                }

            page.wait_for_timeout(1_000)

        raise TimeoutError("等待人工选择类目并点击查询超时。")

    def read_selected_remai_categories(self, page: Page) -> list[str]:
        """读取当前 cascader 中已选择的类目名称。"""

        try:
            items = page.evaluate(
                """
                () => {
                  const selectors = [
                    '.ant-select.ant-cascader .ant-select-selection-item',
                    '.ant-select.ant-cascader .ant-select-selection-overflow-item',
                    '.ant-select.ant-cascader [class*="selection-item"]',
                  ];
                  const values = [];
                  for (const selector of selectors) {
                    for (const node of Array.from(document.querySelectorAll(selector))) {
                      const text = (
                        node.getAttribute('title') ||
                        node.textContent ||
                        ''
                      ).replace(/\\s+/g, ' ').trim();
                      if (text) {
                        values.push(text);
                      }
                    }
                  }
                  return values;
                }
                """
            )
        except Exception:
            return []

        selected_categories: list[str] = []
        for item in items:
            normalized = self.normalize_keyword(str(item or ""))
            if not normalized or normalized in selected_categories:
                continue
            selected_categories.append(normalized)
        return selected_categories

    def try_configure_remai_filters(self, page: Page) -> dict[str, Any]:
        """尽力跳过指定大类，并返回过滤执行情况。"""

        excluded = list(self.EXCLUDED_CATEGORY_KEYWORDS)
        try:
            clicked = page.evaluate(
                """
                (excludedTexts) => {
                  const normalized = (value) => (value || '').replace(/\\s+/g, '').trim();
                  const excludedSet = new Set(excludedTexts.map(normalized));
                  let clicked = 0;
                  const nodes = Array.from(
                    document.querySelectorAll('label, span, div, li, a, button')
                  );
                  for (const node of nodes) {
                    const text = normalized(node.textContent || '');
                    if (!text || !excludedSet.has(text)) continue;
                    const clickable = node.closest('label, button, a, li, .el-checkbox, .el-tree-node, .ant-tree-treenode') || node;
                    const ariaChecked = clickable.getAttribute('aria-checked') || '';
                    const ariaSelected = clickable.getAttribute('aria-selected') || '';
                    const className = String(clickable.className || '');
                    const isSelected =
                      ariaChecked === 'true' ||
                      ariaSelected === 'true' ||
                      /(checked|selected|active)/i.test(className);
                    if (!isSelected) continue;
                    clickable.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                    clicked += 1;
                  }
                  return { clicked };
                }
                """,
                excluded,
            )
        except Exception as exc:
            return {"status": "partial_failed", "error": str(exc), "excluded_categories": excluded, "clicked": 0}

        return {
            "status": "completed",
            "excluded_categories": excluded,
            "clicked": int((clicked or {}).get("clicked") or 0),
        }

    def click_query_button(self, page: Page) -> None:
        """点击热销页查询按钮。"""

        candidates = [
            page.get_by_role("button", name=re.compile(r"查\s*询|搜\s*索")),
            page.locator("button").filter(has_text=re.compile(r"查\s*询|搜\s*索")),
            page.locator("a").filter(has_text=re.compile(r"查\s*询|搜\s*索")),
        ]

        for locator in candidates:
            try:
                target = locator.first
                if target.count() <= 0:
                    continue
                target.click(timeout=3_000)
                page.wait_for_timeout(2_000)
                return
            except Exception:
                continue

        try:
            page.evaluate(
                """
                () => {
                  const elements = Array.from(document.querySelectorAll('button, a, span, div'));
                  const target = elements.find((element) => /查询|搜索/.test((element.textContent || '').trim()));
                  if (target) {
                    target.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                    return true;
                  }
                  return false;
                }
                """
            )
            page.wait_for_timeout(2_000)
        except Exception:
            pass

    def collect_remai_categories(self, page: Page) -> list[dict[str, Any]]:
        """从类目脚本中读取一级类目和其全量后代 ID。"""

        dropdown_categories = self.collect_remai_category_names_from_dropdown(page)
        if dropdown_categories:
            return [
                {
                    "index": index,
                    "name": category_name,
                    "category_ids": [],
                    "top_category_id": None,
                }
                for index, category_name in enumerate(dropdown_categories, start=1)
            ]

        nodes = self.load_remai_category_tree(page)
        categories: list[dict[str, Any]] = []
        for index, node in enumerate(nodes, start=1):
            category_name = self.normalize_keyword(str(node.get("name") or node.get("category_name") or ""))
            if not category_name or self.is_excluded_category_text(category_name):
                continue
            category_ids = self.collect_descendant_category_ids(node)
            if not category_ids:
                continue
            categories.append(
                {
                    "index": index,
                    "name": category_name,
                    "category_ids": category_ids,
                    "top_category_id": node.get("category_id") or node.get("description_category_id"),
                }
            )
        return categories

    def collect_remai_category_names_from_dropdown(self, page: Page) -> list[str]:
        """直接从类目下拉读取一级类目名称。"""

        try:
            selector = page.locator(".ant-select.ant-cascader .ant-select-selector").first
            selector.wait_for(state="visible", timeout=8_000)
            selector.click(timeout=5_000)
            page.wait_for_timeout(1_000)
            page.locator(".ant-cascader-menu-item[title]").first.wait_for(state="visible", timeout=5_000)
            raw_names = page.evaluate(
                """
                () => Array.from(document.querySelectorAll('.ant-cascader-menu-item[title]'))
                  .map((node) => (node.getAttribute('title') || '').replace(/\\s+/g, ' ').trim())
                  .filter(Boolean)
                """
            )
        except Exception:
            return []
        finally:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass

        categories: list[str] = []
        for item in raw_names:
            normalized = self.normalize_keyword(str(item or ""))
            if not normalized or self.is_excluded_category_text(normalized):
                continue
            if normalized not in categories:
                categories.append(normalized)
        return categories

    def load_remai_category_tree(self, page: Page) -> list[dict[str, Any]]:
        """读取 `ozoncategory.js` 中的类目树。"""

        script_url = "https://shopbang.cn/resources/docs/js/ozoncategory.js"
        script_page: Page | None = None
        try:
            script_page = page.context.new_page()
            script_page.goto(script_url, wait_until="domcontentloaded", timeout=self.settings.playwright_timeout_ms)
            script_page.wait_for_timeout(1_000)
            script_text = script_page.locator("body").inner_text(timeout=5_000)
        except Exception:
            return []
        finally:
            if script_page is not None:
                try:
                    script_page.close()
                except Exception:
                    pass

        if not script_text:
            return []
        matched = re.search(r"const\s+arr\s*=\s*(\[.*\])\s*;?\s*$", str(script_text), flags=re.DOTALL)
        if not matched:
            return []
        try:
            payload = json.loads(matched.group(1))
        except Exception:
            return []
        return payload if isinstance(payload, list) else []

    def select_remai_category(self, page: Page, category_name: str) -> bool:
        """通过热销页类目 cascader 选择一级类目。"""

        for attempt in range(2):
            try:
                selector = page.locator(".ant-select.ant-cascader .ant-select-selector").first
                selector.wait_for(state="visible", timeout=8_000)
                selector.click(timeout=5_000)
                page.wait_for_timeout(800)
                page.locator(".ant-cascader-menu-item[title]").first.wait_for(state="visible", timeout=5_000)
            except Exception:
                if attempt == 0:
                    page.wait_for_timeout(1_000)
                    continue
                return False

            escaped = category_name.replace('"', '\\"')
            selectors = [
                f'.ant-cascader-menu-item[title="{escaped}"]',
                f'.ant-cascader-menu-item[title="{category_name}"]',
            ]
            for item_selector in selectors:
                try:
                    locator = page.locator(item_selector).first
                    if locator.count() <= 0:
                        continue
                    locator.click(timeout=5_000)
                    page.wait_for_timeout(800)
                    return True
                except Exception:
                    continue
            if attempt == 0:
                page.wait_for_timeout(1_000)
        return False

    def collect_keywords_by_categories(
        self,
        *,
        context: BrowserContext,
        page: Page,
        response_state: dict[str, Any],
        categories: list[dict[str, Any]],
        processed_urls: set[str],
        seen_urls: set[str],
        max_products: int | None,
    ) -> dict[str, Any]:
        """按类目逐个查询，并跳过已处理 URL。"""

        if not categories:
            categories = [{"name": "default", "category_ids": []}]

        detail_candidates = 0
        skipped_entries = 0
        processed_categories: list[str] = []
        keyword_records: list[dict[str, Any]] = []
        keywords: list[str] = []

        for category in categories:
            category_name = str(category.get("name") or "").strip()
            self.open_remai_page(page)
            response_state["items"] = []
            selected = True
            if category_name and category_name != "default":
                selected = self.select_remai_category(page, category_name)
            if not selected:
                print(f"[shopbang-hot] category select skipped: {category_name}", flush=True)
                continue

            self.click_query_button(page)
            page.wait_for_timeout(2_000)
            api_items = self.collect_paginated_api_items_for_current_category(
                page,
                response_state=response_state,
                max_products=max_products,
                processed_urls=processed_urls,
                seen_urls=seen_urls,
                category_name=category_name,
            )
            entries = self.collect_detail_entries(
                page,
                max_products=max_products,
                api_items=api_items,
            )
            entries = self.filter_entries_by_urls(
                entries,
                processed_urls=processed_urls,
                seen_urls=seen_urls,
            )
            if not entries:
                continue

            processed_categories.append(category_name or "default")
            detail_candidates += len(entries)
            for index, entry in enumerate(entries, start=1):
                print(
                    f"[shopbang-hot] category={category_name or '-'} detail {index}/{len(entries)} "
                    f"title={entry.get('title') or '-'} href={entry.get('href') or '-'}",
                    flush=True,
                )
                keyword_record = self.collect_keyword_record_from_entry(context, page, entry)
                if not keyword_record:
                    skipped_entries += 1
                    continue
                keyword_records.append(keyword_record)
                normalized_url = self.normalize_source_url(str(keyword_record.get("source_product_url") or ""))
                if normalized_url:
                    processed_urls.add(normalized_url)
                    seen_urls.add(normalized_url)
                for keyword in keyword_record.get("keywords") or []:
                    if keyword not in keywords:
                        keywords.append(keyword)

        return {
            "detail_candidates": detail_candidates,
            "skipped_entries": skipped_entries,
            "processed_categories": processed_categories,
            "keyword_records": keyword_records,
            "keywords": keywords,
        }

    def collect_keywords_for_selected_category(
        self,
        *,
        context: BrowserContext,
        page: Page,
        response_state: dict[str, Any],
        category_name: str,
        processed_urls: set[str],
        seen_urls: set[str],
        max_pages: int | None,
        resume_from_page: int,
    ) -> dict[str, Any]:
        """处理人工选中的单个一级类目，并按上次页码继续。"""

        pagination_result = self.collect_paginated_api_items_with_progress(
            page,
            response_state=response_state,
            max_pages=max_pages,
            processed_urls=processed_urls,
            seen_urls=seen_urls,
            category_name=category_name,
            start_page=resume_from_page,
        )

        api_items = list(pagination_result.get("items") or [])
        entries = self.collect_detail_entries(
            page,
            api_items=api_items,
        )
        entries = self.filter_entries_by_urls(
            entries,
            processed_urls=processed_urls,
            seen_urls=seen_urls,
        )

        if not entries:
            return {
                "detail_candidates": 0,
                "skipped_entries": 0,
                "processed_categories": [category_name] if category_name else [],
                "keyword_records": [],
                "keywords": [],
                "request_body": pagination_result.get("request_body") or {},
                "last_completed_page": int(pagination_result.get("last_completed_page") or 0),
                "last_page_size": int(pagination_result.get("last_page_size") or 0),
                "progress_status": "completed_no_entries",
            }

        detail_candidates = len(entries)
        skipped_entries = 0
        keyword_records: list[dict[str, Any]] = []
        keywords: list[str] = []

        for index, entry in enumerate(entries, start=1):
            print(
                f"[shopbang-hot] category={category_name or '-'} detail {index}/{len(entries)} "
                f"title={entry.get('title') or '-'} href={entry.get('href') or '-'}",
                flush=True,
            )
            keyword_record = self.collect_keyword_record_from_entry(context, page, entry)
            if not keyword_record:
                skipped_entries += 1
                continue
            keyword_records.append(keyword_record)
            normalized_url = self.normalize_source_url(str(keyword_record.get("source_product_url") or ""))
            if normalized_url:
                processed_urls.add(normalized_url)
                seen_urls.add(normalized_url)
            for keyword in keyword_record.get("keywords") or []:
                if keyword not in keywords:
                    keywords.append(keyword)

        return {
            "detail_candidates": detail_candidates,
            "skipped_entries": skipped_entries,
            "processed_categories": [category_name] if category_name else [],
            "keyword_records": keyword_records,
            "keywords": keywords,
            "request_body": pagination_result.get("request_body") or {},
            "last_completed_page": int(pagination_result.get("last_completed_page") or 0),
            "last_page_size": int(pagination_result.get("last_page_size") or 0),
            "progress_status": "completed",
        }

    def filter_entries_by_urls(
        self,
        entries: list[dict[str, Any]],
        *,
        processed_urls: set[str],
        seen_urls: set[str],
    ) -> list[dict[str, Any]]:
        """过滤掉已处理过或当前轮次已见过的 URL。"""

        filtered: list[dict[str, Any]] = []
        for entry in entries:
            normalized_url = self.normalize_source_url(str(entry.get("href") or ""))
            if normalized_url:
                if normalized_url in processed_urls or normalized_url in seen_urls:
                    continue
                seen_urls.add(normalized_url)
            filtered.append(entry)
        return filtered

    def install_remai_response_capture(self, page: Page) -> dict[str, Any]:
        """监听热销接口响应，提取商品列表数据。"""

        state: dict[str, Any] = {
            "items": [],
            "request_body": {},
            "request_count": 0,
            "response_count": 0,
        }

        def handle_request(request) -> None:
            try:
                if "getReMaiData" not in request.url:
                    return
                payload = json.loads(request.post_data or "{}")
                if isinstance(payload, dict):
                    state["request_body"] = payload
                state["request_count"] = int(state.get("request_count") or 0) + 1
            except Exception:
                pass

        def handle_response(response) -> None:
            try:
                if "getReMaiData" not in response.url:
                    return
                payload = response.json()
                items = (((payload or {}).get("data") or {}).get("list")) or []
                if isinstance(items, list) and items:
                    state["items"] = items
                elif isinstance(items, list):
                    state["items"] = []
                state["response_count"] = int(state.get("response_count") or 0) + 1
            except Exception:
                pass

        page.on("request", handle_request)
        page.on("response", handle_response)
        return state

    def collect_paginated_api_items_for_current_category(
        self,
        page: Page,
        *,
        response_state: dict[str, Any],
        max_pages: int | None,
        processed_urls: set[str],
        seen_urls: set[str],
        category_name: str,
    ) -> list[dict[str, Any]]:
        """基于当前类目查询条件，按页拉取更多商品。"""

        result = self.collect_paginated_api_items_with_progress(
            page,
            response_state=response_state,
            max_pages=max_pages,
            processed_urls=processed_urls,
            seen_urls=seen_urls,
            category_name=category_name,
            start_page=1,
        )
        return list(result.get("items") or [])

    def collect_paginated_api_items_with_progress(
        self,
        page: Page,
        *,
        response_state: dict[str, Any],
        max_pages: int | None,
        processed_urls: set[str],
        seen_urls: set[str],
        category_name: str,
        start_page: int,
    ) -> dict[str, Any]:
        """按页拉取当前类目的热销商品，并返回续跑元信息。"""

        request_body = dict(response_state.get("request_body") or {})
        if not request_body:
            return {
                "items": [],
                "request_body": {},
                "last_completed_page": max(int(start_page) - 1, 0),
                "last_page_size": 0,
            }

        initial_page_no = max(int(request_body.get("pageNo") or 1), 1)
        start_page = max(int(start_page), 1)
        page_limit = max(int(max_pages or 0), 1)
        last_completed_page = max(start_page - 1, 0)
        last_page_size = 0

        collected_items: list[dict[str, Any]]
        if start_page <= initial_page_no:
            collected_items = list(response_state.get("items") or [])
            if collected_items:
                last_completed_page = initial_page_no
                last_page_size = len(collected_items)
        else:
            collected_items = []

        candidate_entries = self.filter_entries_by_urls(
            self.collect_api_entries(collected_items),
            processed_urls=processed_urls,
            seen_urls=set(seen_urls),
        )
        processed_page_count = 1 if start_page <= initial_page_no and collected_items else 0
        if processed_page_count >= page_limit:
            return {
                "items": collected_items,
                "request_body": request_body,
                "last_completed_page": last_completed_page,
                "last_page_size": last_page_size,
            }

        seen_entry_urls = {
            self.normalize_source_url(str(item.get("link") or ""))
            for item in collected_items
            if self.normalize_source_url(str(item.get("link") or ""))
        }

        next_page = max(initial_page_no + 1, start_page)
        remaining_page_count = max(page_limit - processed_page_count, 0)
        end_page = next_page + remaining_page_count - 1
        for page_no in range(next_page, end_page + 1):
            page_items = self.fetch_remai_items_by_page(page, request_body=request_body, page_no=page_no)
            if not page_items:
                break

            last_completed_page = page_no
            last_page_size = len(page_items)
            new_items: list[dict[str, Any]] = []
            for item in page_items:
                normalized_url = self.normalize_source_url(str(item.get("link") or ""))
                if normalized_url and normalized_url in seen_entry_urls:
                    continue
                if normalized_url:
                    seen_entry_urls.add(normalized_url)
                new_items.append(item)

            if not new_items:
                break

            collected_items.extend(new_items)
            candidate_entries = self.filter_entries_by_urls(
                self.collect_api_entries(collected_items),
                processed_urls=processed_urls,
                seen_urls=set(seen_urls),
            )
            print(
                f"[shopbang-hot] category={category_name or '-'} fetched page {page_no}, "
                f"api_items={len(collected_items)}, new_entries={len(candidate_entries)}",
                flush=True,
            )

        return {
            "items": collected_items,
            "request_body": request_body,
            "last_completed_page": last_completed_page,
            "last_page_size": last_page_size,
        }

    def fetch_remai_items_by_page(self, page: Page, *, request_body: dict[str, Any], page_no: int) -> list[dict[str, Any]]:
        """按指定页码请求热销接口。"""

        payload = dict(request_body)
        payload["pageNo"] = page_no
        try:
            cookies = {
                cookie.get("name"): cookie.get("value")
                for cookie in page.context.cookies(["https://plus.shopbang.cn"])
                if cookie.get("name") and cookie.get("value") is not None
            }
            response = requests.post(
                "https://plus.shopbang.cn/api/goods/hotSales/ozon/getReMaiData",
                json=payload,
                cookies=cookies,
                headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "Origin": "https://shopbang.cn",
                    "Referer": self.settings.shopbang_remai_url,
                },
                timeout=30,
            )
            response.raise_for_status()
            response_payload = response.json()
        except Exception as exc:
            print(f"[shopbang-hot] fetch page {page_no} failed: {exc}", flush=True)
            return []

        items = (((response_payload or {}).get("data") or {}).get("list")) or []
        return items if isinstance(items, list) else []

    def collect_detail_entries(
        self,
        page: Page,
        *,
        max_products: int | None = None,
        api_items: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """从热销列表中提取详情入口。"""

        page.wait_for_timeout(2_000)
        api_entries = self.collect_api_entries(api_items or [], max_products=max_products)
        if api_entries:
            return api_entries

        title_entries = self.collect_shopbang_title_entries(page, max_products=max_products)
        if title_entries:
            return title_entries

        entries = page.evaluate(
            """
            () => {
              const isVisible = (element) => {
                if (!(element instanceof HTMLElement)) return false;
                const style = window.getComputedStyle(element);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = element.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
              };

              const textOf = (element) => (element?.textContent || '').replace(/\\s+/g, ' ').trim();
              const titleFromContainer = (element) => {
                const container = element.closest('tr, li, .el-table__row, .ant-table-row, .card, .list-item, .table-row') || element.parentElement;
                if (!container) return '';
                const texts = Array.from(container.querySelectorAll('a, span, div, p'))
                  .map((node) => textOf(node))
                  .filter((text) => text.length >= 4);
                texts.sort((left, right) => right.length - left.length);
                return texts[0] || '';
              };

              const hrefHints = /detail|goods|product|item|sku|spu|remai/i;
              const actionHints = /详情|查看|商品/;
              const seen = new Set();
              const tagged = [];
              let index = 0;

              for (const element of Array.from(document.querySelectorAll('a[href], button, [role="button"], span, div'))) {
                if (!isVisible(element)) continue;
                const text = textOf(element);
                const anchor = element.closest('a[href]') || element.querySelector?.('a[href]');
                const href = anchor?.href || '';
                if (!href && !actionHints.test(text)) continue;
                if (href && !hrefHints.test(href) && !actionHints.test(text)) continue;
                const title = titleFromContainer(element);
                const key = `${href}||${title}||${text}`;
                if (seen.has(key)) continue;
                seen.add(key);
                element.setAttribute('data-shopbang-hot-detail-index', String(index));
                tagged.push({
                  index,
                  href,
                  actionText: text,
                  title,
                });
                index += 1;
              }

              return tagged;
            }
            """
        )

        normalized_entries = [
            {
                "index": int(item.get("index") or 0),
                "href": str(item.get("href") or "").strip(),
                "action_text": str(item.get("actionText") or "").strip(),
                "title": str(item.get("title") or "").strip(),
            }
            for item in entries
        ]
        if max_products is not None and max_products > 0:
            return normalized_entries[:max_products]
        return normalized_entries

    def collect_api_entries(
        self,
        api_items: list[dict[str, Any]],
        *,
        max_products: int | None = None,
    ) -> list[dict[str, Any]]:
        """优先从热销接口响应中提取标题和跳转 URL。"""

        entries: list[dict[str, Any]] = []
        for index, item in enumerate(api_items):
            title = self.normalize_keyword(str(item.get("name") or item.get("skuName") or ""))
            href = str(item.get("link") or "").strip()
            if not title or not href:
                continue

            category_parts = [
                self.normalize_keyword(str(item.get("category1") or "")),
                self.normalize_keyword(str(item.get("category3") or "")),
            ]
            category_parts = [part for part in category_parts if part]
            category_text = " > ".join(category_parts)
            if self.is_excluded_category_text(category_text):
                continue

            entries.append(
                {
                    "index": index,
                    "title": title,
                    "action_text": title,
                    "href": href,
                    "category_text": category_text,
                    "row_text": title,
                    "sku": str(item.get("sku") or "").strip(),
                    "row_key": str(item.get("_id") or "").strip(),
                }
            )

        if max_products is not None and max_products > 0:
            return entries[:max_products]
        return entries

    def collect_shopbang_title_entries(self, page: Page, *, max_products: int | None = None) -> list[dict[str, Any]]:
        """优先从热销表格的商品标题中提取入口。"""

        entries = page.evaluate(
            """
            () => {
              const textOf = (element) => (element?.textContent || '').replace(/\\s+/g, ' ').trim();
              const visible = (element) => {
                if (!(element instanceof HTMLElement)) return false;
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
              };

              return Array.from(document.querySelectorAll('.surely-table-row .goods-title'))
                .filter(visible)
                .map((element, index) => {
                  const row = element.closest('.surely-table-row');
                  const cells = row
                    ? Array.from(row.querySelectorAll('[data-column-key]'))
                        .map((cell) => ({
                          key: String(cell.getAttribute('data-column-key') || ''),
                          text: textOf(cell),
                        }))
                    : [];
                  return {
                    index,
                    title: textOf(element),
                    rowText: textOf(row),
                    cells,
                  };
                });
            }
            """
        )

        normalized_entries = []
        for item in entries:
            title = self.normalize_keyword(str(item.get("title") or ""))
            if not title:
                continue

            cell_map = {
                str(cell.get("key") or "").strip(): str(cell.get("text") or "").strip()
                for cell in (item.get("cells") or [])
                if str(cell.get("key") or "").strip()
            }
            category_text = cell_map.get("category", "") or str(item.get("rowText") or "")
            if self.is_excluded_category_text(category_text):
                continue

            normalized_entries.append(
                {
                    "index": int(item.get("index") or 0),
                    "shopbang_index": int(item.get("index") or 0),
                    "title": title,
                    "action_text": title,
                    "href": "",
                    "category_text": category_text,
                    "row_text": str(item.get("rowText") or "").strip(),
                }
            )

        if max_products is not None and max_products > 0:
            return normalized_entries[:max_products]
        return normalized_entries

    def collect_keyword_record_from_entry(
        self,
        context: BrowserContext,
        page: Page,
        entry: dict[str, Any],
    ) -> dict[str, Any] | None:
        """进入详情并提取结构化关键词记录。"""

        shopbang_index = entry.get("shopbang_index")
        if shopbang_index is not None:
            return self.collect_keyword_record_from_shopbang_title_click(
                context,
                page,
                int(shopbang_index),
                str(entry.get("title") or ""),
            )

        detail_page: Page | None = None
        used_original_page = False
        original_url = page.url
        href = str(entry.get("href") or "").strip()
        try:
            if href and href.lower() not in {"javascript:void(0)", "#"}:
                detail_page = context.new_page()
                detail_page.goto(href, wait_until="domcontentloaded", timeout=self.settings.playwright_timeout_ms)
            else:
                detail_page = page
                used_original_page = True
                detail_page.locator(f"[data-shopbang-hot-detail-index='{int(entry.get('index') or 0)}']").click(timeout=3_000)

            detail_page.wait_for_timeout(1_500)
            if href and "ozon." in href:
                return self.build_keyword_record_from_ozon_page(
                    detail_page,
                    source_product_title=str(entry.get("title") or ""),
                    source_product_url=detail_page.url,
                    source_product_sku=str(entry.get("sku") or ""),
                )

            breadcrumbs = self.extract_breadcrumb_items(detail_page)
            if self.is_excluded_breadcrumbs(breadcrumbs):
                return None

            breadcrumb_texts = [str(item.get("text") or "").strip() for item in breadcrumbs]
            return self.build_keyword_record(
                breadcrumb_texts=breadcrumb_texts,
                source_product_title=str(entry.get("title") or ""),
                source_product_url=detail_page.url,
                source_product_sku=str(entry.get("sku") or ""),
            )
        except Exception as exc:
            print(f"[shopbang-hot] detail extraction failed: {exc}", flush=True)
            return None
        finally:
            if detail_page is not None and used_original_page:
                try:
                    detail_page.goto(original_url, wait_until="domcontentloaded", timeout=self.settings.playwright_timeout_ms)
                    detail_page.wait_for_timeout(1_000)
                    self.click_query_button(detail_page)
                    self.collect_detail_entries(detail_page)
                except Exception:
                    pass
            elif detail_page is not None:
                detail_page.close()

    def collect_keywords_from_entry(self, context: BrowserContext, page: Page, entry: dict[str, Any]) -> list[str]:
        """兼容旧接口：进入详情并返回关键词列表。"""

        record = self.collect_keyword_record_from_entry(context, page, entry)
        if not record:
            return []
        return list(record.get("keywords") or [])

    def collect_keyword_record_from_shopbang_title_click(
        self,
        context: BrowserContext,
        page: Page,
        shopbang_index: int,
        title: str,
    ) -> dict[str, Any] | None:
        """点击热销商品标题，转到 Ozon 页面后提取结构化关键词。"""

        ozon_page: Page | None = None
        try:
            self.open_remai_page(page)
            self.click_query_button(page)
            self.wait_for_shopbang_titles(page, shopbang_index)
            self.dismiss_obstructing_overlays(page)
            safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
            title_locator = page.locator(f'.goods-title[title="{safe_title}"]').first
            if title_locator.count() <= 0:
                title_locator = page.locator(".goods-title").nth(shopbang_index)
            with context.expect_page(timeout=10_000) as page_info:
                title_locator.click(timeout=5_000, force=True)
            ozon_page = page_info.value
            ozon_page.wait_for_load_state("domcontentloaded", timeout=self.settings.playwright_timeout_ms)
            ozon_page.wait_for_timeout(4_000)
            return self.build_keyword_record_from_ozon_page(
                ozon_page,
                source_product_title=title,
                source_product_url=ozon_page.url,
                source_product_sku=self.extract_product_sku_from_url(ozon_page.url),
            )
        except Exception as exc:
            print(f"[shopbang-hot] title click extraction failed: {exc}", flush=True)
            return None
        finally:
            if ozon_page is not None:
                try:
                    ozon_page.close()
                except Exception:
                    pass

    def collect_keywords_from_shopbang_title_click(
        self,
        context: BrowserContext,
        page: Page,
        shopbang_index: int,
        title: str,
    ) -> list[str]:
        """兼容旧接口：点击热销商品标题后返回关键词列表。"""

        record = self.collect_keyword_record_from_shopbang_title_click(context, page, shopbang_index, title)
        if not record:
            return []
        return list(record.get("keywords") or [])

    def wait_for_shopbang_titles(self, page: Page, minimum_index: int) -> None:
        """等待热销商品标题渲染完成。"""

        try:
            page.wait_for_function(
                """
                (index) => document.querySelectorAll('.goods-title').length > index
                """,
                minimum_index,
                timeout=15_000,
            )
        except Exception:
            page.wait_for_timeout(2_000)

    def dismiss_obstructing_overlays(self, page: Page) -> None:
        """尽力关闭会拦截点击的弹层。"""

        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

        try:
            page.evaluate(
                """
                () => {
                  const closeSelectors = [
                    '.ant-modal-close',
                    '.ant-drawer-close',
                    '[aria-label="Close"]',
                    '[aria-label="关闭"]',
                    '[class*="close"]',
                  ];

                  for (const selector of closeSelectors) {
                    for (const element of Array.from(document.querySelectorAll(selector))) {
                      if (element instanceof HTMLElement) {
                        element.click();
                      }
                    }
                  }

                  const textPatterns = [/我知道了/, /知道了/, /关闭/, /暂不/, /取消/, /稍后/];
                  for (const element of Array.from(document.querySelectorAll('button, span, div'))) {
                    const text = (element.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (!text) continue;
                    if (textPatterns.some((pattern) => pattern.test(text)) && element instanceof HTMLElement) {
                      element.click();
                    }
                  }

                  for (const selector of ['.ant-modal-mask', '.ant-modal-wrap', '.ant-drawer-mask']) {
                    for (const element of Array.from(document.querySelectorAll(selector))) {
                      if (element instanceof HTMLElement) {
                        element.style.display = 'none';
                        element.remove();
                      }
                    }
                  }
                }
                """
            )
            page.wait_for_timeout(300)
        except Exception:
            pass

    def collect_keywords_from_ozon_page(self, page: Page) -> list[str]:
        """从热销标题打开的 Ozon 页面提取可复用关键词。"""

        record = self.build_keyword_record_from_ozon_page(
            page,
            source_product_title="",
            source_product_url=page.url,
            source_product_sku=self.extract_product_sku_from_url(page.url),
        )
        if record:
            return list(record.get("keywords") or [])

        ozon_levels = self.extract_ozon_primary_category_levels(page)
        if ozon_levels:
            return self.dedupe_keywords(ozon_levels)

        keywords: list[str] = []

        search_keyword = self.extract_ozon_search_keyword(page.url)
        if search_keyword:
            keywords.append(search_keyword)

        page_title_keyword = self.extract_ozon_page_title_keyword(page)
        if page_title_keyword:
            keywords.append(page_title_keyword)

        keywords.extend(self.extract_shopbang_panel_keywords(page))
        return self.dedupe_keywords(keywords)

    def build_keyword_record_from_ozon_page(
        self,
        page: Page,
        *,
        source_product_title: str,
        source_product_url: str,
        source_product_sku: str,
    ) -> dict[str, Any] | None:
        """从 Ozon 页面构建结构化关键词记录。"""

        try:
            items = page.evaluate(
                """
                () => {
                  const container = document.querySelector('[data-widget="breadCrumbs"]');
                  if (!container) return [];

                  return Array.from(container.querySelectorAll('ol li a, ol li span'))
                    .map((node) => (node.textContent || '').replace(/\\s+/g, ' ').trim())
                    .filter(Boolean);
                }
                """
            )
        except Exception:
            return None

        return self.build_keyword_record(
            breadcrumb_texts=[str(item or "").strip() for item in items],
            source_product_title=source_product_title,
            source_product_url=source_product_url,
            source_product_sku=source_product_sku,
        )

    def extract_ozon_primary_category_levels(self, page: Page) -> list[str]:
        """从 Ozon 详情页 breadCrumbs 中提取一级、二级类目。"""

        try:
            items = page.evaluate(
                """
                () => {
                  const container = document.querySelector('[data-widget="breadCrumbs"]');
                  if (!container) return [];

                  return Array.from(container.querySelectorAll('ol li a'))
                    .map((anchor) => {
                      const text = (anchor.textContent || '').replace(/\\s+/g, ' ').trim();
                      const href = anchor.href || anchor.getAttribute('href') || '';
                      return { text, href };
                    })
                    .filter((item) => item.text);
                }
                """
            )
        except Exception:
            return []

        breadcrumb_texts = [str(item.get("text") or "").strip() for item in items]
        return self.select_primary_category_levels(breadcrumb_texts)

    @classmethod
    def select_primary_category_levels(cls, breadcrumb_texts: list[str], *, max_levels: int = 2) -> list[str]:
        """从当前品类向上回推一级、二级类目。"""

        normalized_items = cls.normalize_keyword_chain(breadcrumb_texts)

        if len(normalized_items) <= 1:
            return []

        levels: list[str] = []
        ancestor_candidates = list(reversed(normalized_items[:-1]))
        for item in ancestor_candidates:
            levels.append(item)
            if len(levels) >= max_levels:
                break
        return levels

    @classmethod
    def build_keyword_record(
        cls,
        *,
        breadcrumb_texts: list[str],
        source_product_title: str,
        source_product_url: str,
        source_product_sku: str,
    ) -> dict[str, Any] | None:
        """根据类目链路构建结构化关键词记录。"""

        normalized_items = cls.normalize_keyword_chain(breadcrumb_texts)
        if len(normalized_items) <= 1:
            return None

        current_category = normalized_items[-1]
        levels = cls.select_primary_category_levels(normalized_items)
        parent_category = levels[0] if len(levels) >= 1 else ""
        grandparent_category = levels[1] if len(levels) >= 2 else ""

        if not parent_category and not grandparent_category:
            return None
        if any(
            cls.is_excluded_category_text(text)
            for text in (parent_category, grandparent_category)
            if text
        ):
            return None

        keywords = cls.dedupe_keywords([parent_category, grandparent_category])
        if not keywords:
            return None

        return {
            "current_category": current_category,
            "parent_category": parent_category,
            "grandparent_category": grandparent_category,
            "source_product_title": cls.normalize_keyword(source_product_title),
            "source_product_url": source_product_url.strip(),
            "source_product_sku": source_product_sku.strip(),
            "source_batch_type": "shopbang_hot",
            "keywords": keywords,
        }

    @classmethod
    def extract_ozon_search_keyword(cls, url: str) -> str | None:
        """从 Ozon 搜索 URL 中提取 text 参数。"""

        try:
            query = parse_qs(urlparse(url).query)
        except Exception:
            return None
        text_values = query.get("text") or []
        if not text_values:
            return None
        return cls.normalize_keyword(text_values[0])

    @classmethod
    def extract_ozon_page_title_keyword(cls, page: Page) -> str | None:
        """从 Ozon 页面标题里提取主关键词。"""

        try:
            page_title = page.title()
        except Exception:
            return None

        normalized_title = page_title.strip()
        normalized_title = re.sub(r"\s+купить на OZON.*$", "", normalized_title, flags=re.IGNORECASE).strip()
        normalized_title = normalized_title.split(" - ", 1)[0].strip()
        return cls.simplify_product_keyword(normalized_title)

    @classmethod
    def simplify_product_keyword(cls, text: str) -> str | None:
        """把商品标题压缩成更像搜索词的短关键词。"""

        normalized = cls.normalize_keyword(text)
        if not normalized:
            return None

        quoted_match = re.search(r"\"([^\"]{2,80})\"", normalized)
        if quoted_match:
            quoted_text = cls.normalize_keyword(quoted_match.group(1))
            if quoted_text:
                return quoted_text

        brandless = re.sub(r"^[A-Z][A-Za-z0-9.+-]*(?:\s+[A-Z][A-Za-z0-9.+-]*)?\s+", "", normalized).strip()

        prep_match = re.search(
            r"([A-Za-zА-Яа-яЁё]{2,}(?:\s+[A-Za-zА-Яа-яЁё]{2,})?\s+для\s+[A-Za-zА-Яа-яЁё]{2,})",
            brandless,
        )
        if prep_match:
            keyword = cls.normalize_keyword(prep_match.group(1))
            if keyword:
                return keyword

        shortened = brandless.split(",", 1)[0].strip()
        shortened = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:кг|г|л|мл|шт|pcs|tb|gb)\b.*$", "", shortened, flags=re.IGNORECASE).strip()
        words = shortened.split()
        if not words:
            return None

        if len(words) >= 2:
            candidate = cls.normalize_keyword(" ".join(words[:2]))
            if candidate:
                return candidate

        return cls.normalize_keyword(words[0])

    def extract_shopbang_panel_keywords(self, page: Page) -> list[str]:
        """从上品帮在 Ozon 页面注入的类目信息里提取关键词。"""

        try:
            body_text = page.locator("body").inner_text(timeout=15_000)
        except Exception:
            return []

        normalized_body = re.sub(r"\s+", " ", body_text or "")
        match = re.search(r"类目[:：]\s*(.*?)\s*品牌[:：]", normalized_body)
        if not match:
            return []

        category_path = match.group(1).strip()
        if not category_path:
            return []

        items = [
            self.normalize_keyword(item)
            for item in re.split(r"\s*>\s*", category_path)
        ]
        filtered_items = [item for item in items if item]
        filtered_items.reverse()
        return filtered_items

    def collect_ancestor_keywords(
        self,
        context: BrowserContext,
        detail_page: Page,
        breadcrumbs: list[dict[str, str]],
    ) -> list[str]:
        """收集父级和祖父级类目关键词。"""

        ancestors = self.pick_ancestor_items(breadcrumbs)
        keywords: list[str] = []

        for item in ancestors:
            normalized_text = self.normalize_keyword(item.get("text") or "")
            if normalized_text:
                keywords.append(normalized_text)

            href = str(item.get("href") or "").strip()
            if not href:
                continue

            absolute_href = urljoin(detail_page.url, href)
            ancestor_page: Page | None = None
            try:
                ancestor_page = context.new_page()
                ancestor_page.goto(
                    absolute_href,
                    wait_until="domcontentloaded",
                    timeout=self.settings.playwright_timeout_ms,
                )
                ancestor_page.wait_for_timeout(1_000)
                page_keyword = self.extract_page_keyword(ancestor_page)
                if page_keyword:
                    keywords.append(page_keyword)
            except Exception:
                pass
            finally:
                try:
                    if ancestor_page is not None:
                        ancestor_page.close()
                except Exception:
                    pass

        return keywords

    def extract_breadcrumb_items(self, page: Page) -> list[dict[str, str]]:
        """提取详情页面包屑文本和链接。"""

        raw_items = page.evaluate(
            """
            () => {
              const selectors = [
                'nav a',
                'nav span',
                '.breadcrumb a',
                '.breadcrumb span',
                '[class*="breadcrumb"] a',
                '[class*="breadcrumb"] span',
                '.el-breadcrumb__item',
                '[class*="crumb"] a',
                '[class*="crumb"] span',
              ];
              const seen = new Set();
              const results = [];
              for (const selector of selectors) {
                for (const node of Array.from(document.querySelectorAll(selector))) {
                  const text = (node.textContent || '').replace(/\\s+/g, ' ').trim();
                  if (!text) continue;
                  const href = node.href || node.closest('a[href]')?.href || '';
                  const key = `${text}||${href}`;
                  if (seen.has(key)) continue;
                  seen.add(key);
                  results.push({ text, href });
                }
              }
              return results;
            }
            """
        )
        return [
            {
                "text": str(item.get("text") or "").strip(),
                "href": str(item.get("href") or "").strip(),
            }
            for item in raw_items
            if self.normalize_keyword(str(item.get("text") or ""))
        ]

    def extract_page_keyword(self, page: Page) -> str | None:
        """从父级类目页面尽力提取一个可用关键词。"""

        candidates = page.evaluate(
            """
            () => {
              const values = [];
              const pushText = (value) => {
                const text = (value || '').replace(/\\s+/g, ' ').trim();
                if (text) values.push(text);
              };

              pushText(document.title);
              for (const selector of ['h1', 'h2', '[class*="title"]', '[class*="header"]', 'input[value]', 'input[placeholder]']) {
                const element = document.querySelector(selector);
                if (!element) continue;
                if (element instanceof HTMLInputElement) {
                  pushText(element.value || element.placeholder || '');
                } else {
                  pushText(element.textContent || '');
                }
              }
              return values;
            }
            """
        )

        for candidate in candidates:
            normalized = self.normalize_keyword(str(candidate or ""))
            if normalized:
                return normalized
        return None

    @classmethod
    def pick_ancestor_items(cls, breadcrumbs: list[dict[str, str]]) -> list[dict[str, str]]:
        """从面包屑中选出上一级和上两级。"""

        normalized_items = [item for item in breadcrumbs if cls.normalize_keyword(item.get("text") or "")]
        if not normalized_items:
            return []

        # 默认最后一个为当前商品或当前页，向前取父级和祖父级。
        if len(normalized_items) >= 3:
            return [normalized_items[-2], normalized_items[-3]]
        if len(normalized_items) == 2:
            return [normalized_items[-2]]
        return []

    @classmethod
    def is_excluded_breadcrumbs(cls, breadcrumbs: list[dict[str, str]]) -> bool:
        """判断面包屑链路中是否含有应排除类目。"""

        texts = [cls.normalize_keyword(item.get("text") or "") for item in breadcrumbs]
        return any(cls.is_excluded_category_text(text) for text in texts if text)

    @classmethod
    def is_excluded_category_text(cls, text: str) -> bool:
        """判断是否命中排除类目。"""

        normalized = cls.normalize_keyword(text)
        lowered = normalized.lower()
        for aliases in cls.EXCLUDED_CATEGORY_ALIASES.values():
            if any(alias.lower() in lowered for alias in aliases):
                return True
        return any(keyword in normalized for keyword in cls.EXCLUDED_CATEGORY_KEYWORDS)

    @classmethod
    def dedupe_keywords(cls, keywords: list[str]) -> list[str]:
        """去重并保留顺序。"""

        result: list[str] = []
        for keyword in keywords:
            normalized = cls.normalize_keyword(keyword)
            if not normalized or cls.is_excluded_category_text(normalized):
                continue
            if normalized not in result:
                result.append(normalized)
        return result

    @classmethod
    def normalize_keyword_chain(cls, items: list[str]) -> list[str]:
        """清洗并去重类目链路文本。"""

        normalized_items: list[str] = []
        for text in items:
            normalized = cls.normalize_keyword(text)
            if not normalized:
                continue
            if normalized in normalized_items:
                continue
            normalized_items.append(normalized)
        return normalized_items

    @staticmethod
    def extract_product_sku_from_url(url: str) -> str:
        """从 Ozon 商品 URL 中提取 SKU。"""

        match = re.search(r"/product/(?:[^/?#]+-)?(\d+)", url or "")
        if match:
            return match.group(1)
        return ""

    @staticmethod
    def normalize_source_url(url: str) -> str:
        """规范化来源 URL。"""

        return OzonKeywordPoolRepository.normalize_source_url(url)

    @classmethod
    def collect_descendant_category_ids(cls, node: dict[str, Any]) -> list[int]:
        """收集一个类目节点下所有叶子/子孙类目 ID。"""

        ids: list[int] = []
        children = node.get("children") or []
        if not children:
            category_id = cls._coerce_category_id(node.get("category_id") or node.get("type_id"))
            return [category_id] if category_id is not None else []

        for child in children:
            child_ids = cls.collect_descendant_category_ids(child)
            for category_id in child_ids:
                if category_id not in ids:
                    ids.append(category_id)
        return ids

    @staticmethod
    def _coerce_category_id(value: Any) -> int | None:
        """把类目 ID 转成整数。"""

        try:
            return int(value)
        except Exception:
            return None

    @classmethod
    def normalize_keyword(cls, text: str) -> str:
        """清洗关键词文本。"""

        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        normalized = re.sub(r"[>»/|]+", " ", normalized).strip()
        normalized = re.sub(r"\(.*?\)", "", normalized).strip()
        normalized = re.sub(r"\[.*?\]", "", normalized).strip()
        if not normalized:
            return ""
        lowered = normalized.lower()
        if lowered in cls.GENERIC_BREADCRUMB_TEXTS:
            return ""
        if re.fullmatch(r"[\d\W_]+", normalized):
            return ""
        return normalized
