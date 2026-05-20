"""Ozon 选品采集器。

Python 版本对齐 `scrape-ozon.js` 的主要行为：
1. 使用 Playwright + 上品帮插件抓取 Ozon 搜索结果页
2. 提取 Ozon 卡片字段与插件指标
3. 应用选品红线和评分规则
4. 可选保存商品图片
5. 导出 Excel 结果文件
"""

from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd
import requests
from openpyxl import load_workbook
from playwright.sync_api import BrowserContext, Page, Playwright, Response, TimeoutError as PlaywrightTimeoutError, sync_playwright

from config.settings import Settings, get_settings
from ozon_selection.collectors.base import BaseCollector
from ozon_selection.collectors.ozon.shopbang_auth import ShopbangLoginManager


PRICE_MIN = 500
PRICE_MAX = 20000
MONTHLY_SALES_MIN = 5
MONTHLY_SALES_MAX = 500
MIN_SELLERS = 1
MAX_SELLERS = 50
MAX_RETURN_RATE = 20
MIN_LISTED_DAYS = 1
MAX_LISTED_DAYS = 365
MIN_LISTED_DAYS_WARN = 180
MIN_PROFIT_MARGIN = 0.10
MAX_DETAIL_SPECS = 30


@dataclass(slots=True)
class ProfitEstimate:
    """利润测算结果。"""

    max_cost: int | None
    shipping: float | None
    tier: str | None


@dataclass(slots=True)
class EvaluationResult:
    """选品规则评估结果。"""

    fails: list[str]
    warns: list[str]
    score: int
    max_cost: int | None
    shipping: float | None
    tier: str | None


class ProductCollector(BaseCollector):
    """负责 Ozon 关键词选品抓取、评估和导出。"""

    PLUGIN_LOGIN_MARKERS = (
        "月销量：登录",
        "类目：登录",
        "品牌：登录",
        "发货模式：登录",
        "跟卖者：登录",
    )

    IMAGE_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.ozon.ru/",
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    }

    COLUMN_WIDTHS = [
        4,
        12,
        35,
        25,
        8,
        14,
        70,
        42,
        55,
        30,
        15,
        12,
        12,
        18,
        10,
        10,
        12,
        10,
        10,
        12,
        12,
        10,
        14,
        10,
        10,
        10,
        12,
        12,
        10,
        10,
        15,
        8,
        8,
        10,
        32,
        32,
        28,
        12,
    ]

    STANDARD_ROWS = [
        ["指标", "理想值", "红线（直接淘汰）", "说明"],
        ["月销量", "50-300件", "<5件或>500件", ""],
        ["月增速", ">20%", "负增长", ""],
        ["跟卖者", "5-15个", "<1个或>50个", ""],
        ["退货取消率", "<8%", ">20%", ""],
        ["成交率", ">80%", "", ""],
        ["点击率", "3%-6%", "", ""],
        ["加购率", ">8%", "", ""],
        ["促销天数", "<15天/月", "", ""],
        ["广告份额", "<20%", "", ""],
        ["售价", "600-5000₽", "<500₽或>20000₽", ""],
        ["发货模式", "FBS / FBO", "", ""],
        ["上架时间", "1-365天", "<1天或>365天", ""],
        ["利润率", ">10%", "≤10%", "售价×80%-成本-运费"],
        ["", "", "", ""],
        ["物流档位", "售价", "重量", "运费公式"],
        ["Extra Small", "≤1500₽", "≤500g", "3+0.035×W(g)"],
        ["Small", "1501-20000₽", "≤2000g", "16+0.035×W(g)"],
    ]

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.login_manager = ShopbangLoginManager(self.settings)

    def collect(self, start_urls: list[str] | None = None) -> list[dict]:
        """兼容基类接口，返回原始商品数据。"""

        keyword = self.settings.ozon_scrape_keyword
        search_url = start_urls[0] if start_urls else self.build_search_url(keyword)
        return self.scrape_products(search_url)

    def run(self, keyword: str | None = None) -> dict[str, Any]:
        """执行完整抓取流程并导出结果。"""

        scrape_keyword = keyword or self.settings.ozon_scrape_keyword
        search_url = self.build_search_url(scrape_keyword)
        products = self.scrape_products(search_url)
        if self.settings.ozon_scrape_download_images and products:
            passed_products = [product for product in products if not self.evaluate(product).fails]
            if passed_products:
                self.save_product_images(passed_products)
        rows = self.build_result_rows(products)
        output_file = self.export_to_excel(rows, scrape_keyword)

        passed_count = sum(1 for row in rows if "✅" in row["结果"])
        return {
            "keyword": scrape_keyword,
            "search_url": search_url,
            "total": len(rows),
            "passed": passed_count,
            "failed": len(rows) - passed_count,
            "output_file": str(output_file),
            "rows": rows,
        }

    def build_search_url(self, keyword: str) -> str:
        """构建 Ozon 搜索 URL。"""

        from_global = str(self.settings.ozon_scrape_from_global).lower()
        return (
            f"{self.settings.ozon_base_url}/search/"
            f"?text={requests.utils.quote(keyword)}"
            f"&from_global={from_global}"
            f"&sorting={self.settings.ozon_scrape_sorting}"
        )

    def scrape_products(self, search_url: str) -> list[dict]:
        """抓取 Ozon 搜索结果页中的候选商品。"""

        with sync_playwright() as playwright:
            self.login_manager.validate_collection_prerequisites()
            session = self.login_manager.open_browser_session(playwright=playwright)
            context = session.context
            page = context.new_page()

            try:
                page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=self.settings.playwright_timeout_ms,
                )
                self.wait_search_results_ready(page)
                self.ensure_plugin_ready(context, page, search_url)
                return self.collect_products_across_pages(page)
            finally:
                page.close()
                session.close()

    def scrape_products_from_current_page(
        self,
        *,
        context: BrowserContext,
        page: Page,
        target_count: int | None = None,
        skip_skus: set[str] | None = None,
    ) -> list[dict]:
        """从当前已人工准备好的 Ozon 列表页继续抓取商品。"""

        current_url = page.url
        if not current_url:
            raise RuntimeError("当前浏览器页没有有效 URL，无法继续抓取。")

        self.wait_search_results_ready(page)
        self.ensure_plugin_ready(context, page, current_url)
        return self.collect_products_across_pages(
            page,
            target_count=target_count,
            skip_skus=skip_skus,
        )

    def collect_products_across_pages(
        self,
        page: Page,
        *,
        target_count: int | None = None,
        skip_skus: set[str] | None = None,
    ) -> list[dict]:
        """按分页累计抓取商品，直到达到目标数量或没有新页面。"""

        normalized_target = int(target_count) if target_count is not None else int(self.settings.ozon_scrape_target_products)
        if normalized_target <= 0:
            normalized_target = None

        base_url = self.normalize_paged_url(page.url)
        all_products: list[dict[str, Any]] = []
        seen_skus: set[str] = {
            str(sku).strip() for sku in (skip_skus or set()) if str(sku).strip()
        }
        page_number = 1
        empty_page_rounds = 0
        max_pages = max(1, (normalized_target // 16) + 10) if normalized_target is not None else 500

        while (normalized_target is None or len(all_products) < normalized_target) and page_number <= max_pages:
            if page_number > 1:
                paged_url = self.build_paged_url(base_url, page_number)
                print(f"  打开第 {page_number} 页: {paged_url}")
                try:
                    page.goto(
                        paged_url,
                        wait_until="domcontentloaded",
                        timeout=self.settings.playwright_timeout_ms,
                    )
                    self.wait_search_results_ready(page)
                except PlaywrightTimeoutError as exc:
                    print(f"  第 {page_number} 页加载超时，停止后续翻页: {exc}")
                    break
                page.wait_for_timeout(self.settings.ozon_scrape_plugin_wait_ms)

            self.scroll_to_load(page, normalized_target)
            page_products = self.extract_all(page)
            new_products, duplicate_products = self.merge_page_products(
                collected_products=all_products,
                page_products=page_products,
                seen_skus=seen_skus,
            )
            print(
                f"  第 {page_number} 页新增 {new_products} 个商品，"
                f"跳过 {duplicate_products} 个重复 SKU，累计 {len(all_products)} 个"
            )

            if new_products == 0:
                empty_page_rounds += 1
                if empty_page_rounds >= 2:
                    break
            else:
                empty_page_rounds = 0

            page_number += 1

        if normalized_target is None:
            return all_products
        return all_products[:normalized_target]

    @staticmethod
    def merge_page_products(
        *,
        collected_products: list[dict[str, Any]],
        page_products: list[dict[str, Any]],
        seen_skus: set[str],
    ) -> tuple[int, int]:
        """把当前页新商品合并到全量列表，并返回新增/跳过数量。"""

        added = 0
        skipped = 0
        for product in page_products:
            sku = str(product.get("sku") or "")
            if not sku or sku in seen_skus:
                skipped += 1
                continue
            seen_skus.add(sku)
            collected_products.append(product)
            added += 1
        return added, skipped

    @staticmethod
    def normalize_paged_url(url: str) -> str:
        """移除已有 page 参数，作为后续翻页的基础 URL。"""

        parts = urlsplit(url)
        query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "page"]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    @staticmethod
    def build_paged_url(base_url: str, page_number: int) -> str:
        """基于基础 URL 生成指定页码链接。"""

        if page_number <= 1:
            return base_url

        parts = urlsplit(base_url)
        query = parse_qsl(parts.query, keep_blank_values=True)
        query.append(("page", str(page_number)))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    @staticmethod
    def wait_search_results_ready(page: Page) -> None:
        """等待 Ozon 搜索结果页首批商品卡片出现。"""

        page.wait_for_selector(".tile-root[data-index]", timeout=20_000)
        page.wait_for_timeout(2_000)

    def ensure_plugin_ready(self, context: BrowserContext, page: Page, search_url: str) -> None:
        """确认 Ozon 页面上的上品帮插件已返回真实数据。"""

        state = self.wait_until_plugin_ready(page)
        if state["valid"]:
            return

        if self.login_manager.has_login_credentials:
            self.login_manager.login_with_credentials(context)
            page.goto(
                search_url,
                wait_until="domcontentloaded",
                timeout=self.settings.playwright_timeout_ms,
            )
            page.wait_for_selector(".tile-root[data-index]", timeout=20_000)
            state = self.wait_until_plugin_ready(page)
            if state["valid"]:
                self.login_manager.save_auth_state(context)
                return

        raise RuntimeError(
            "上品帮插件未返回真实数据，当前仍处于未登录或失效状态。"
            f" url={page.url}"
            f" tile_count={state['tile_count']}"
            f" selector_match_count={state['selector_match_count']}"
            f" plugin_count={state['plugin_count']}"
            f" real_data_count={state['real_data_count']}"
            f" placeholder_count={state['placeholder_count']}"
            f" samples={state['samples'][:3]}"
        )

    def wait_until_plugin_ready(self, page: Page) -> dict[str, Any]:
        """轮询等待插件数据注入，兼容较慢的页面返回。"""

        wait_ms = max(int(self.settings.ozon_scrape_plugin_wait_ms or 0), 1_000)
        total_timeout_ms = max(wait_ms, 20_000)
        interval_ms = min(wait_ms, 2_000)
        deadline = time.monotonic() + (total_timeout_ms / 1000)
        last_state = self.inspect_plugin_state(page)

        while time.monotonic() < deadline:
            if last_state["valid"]:
                return last_state
            page.wait_for_timeout(interval_ms)
            last_state = self.inspect_plugin_state(page)

        return last_state

    def scroll_to_load(self, page: Page, target_count: int | None) -> None:
        """缓慢滚动页面，让商品卡片和插件数据都完成加载。"""

        previous_count = 0
        stale_rounds = 0
        recovery_attempts = 0

        while True:
            page.evaluate(f"window.scrollBy(0, {self.settings.ozon_scrape_scroll_step_px})")
            page.wait_for_timeout(self.settings.ozon_scrape_scroll_pause_ms)

            count = page.locator(".tile-root[data-index]").count()
            print(f"\r  已渲染 {count} 个商品卡片...", end="")

            if target_count is not None and count >= target_count:
                print("")
                break

            if count == previous_count:
                stale_rounds += 1
                if stale_rounds >= self.settings.ozon_scrape_stale_limit:
                    if self.should_attempt_scroll_recovery(
                        current_count=count,
                        target_count=target_count,
                        recovery_attempts=recovery_attempts,
                    ):
                        recovery_attempts += 1
                        print(f"\n  触发尾部补偿滚动，第 {recovery_attempts} 轮...", end="")
                        if self.try_recover_scroll(page, count):
                            stale_rounds = 0
                            previous_count = page.locator(".tile-root[data-index]").count()
                            continue
                    print("")
                    break
            else:
                stale_rounds = 0

            previous_count = count

        print("  等待插件数据注入...")
        page.wait_for_timeout(self.settings.ozon_scrape_plugin_wait_ms)

    @staticmethod
    def should_attempt_scroll_recovery(
        *,
        current_count: int,
        target_count: int | None,
        recovery_attempts: int,
        max_recovery_attempts: int = 3,
    ) -> bool:
        """判断在疑似触底时是否还值得做尾部补偿滚动。"""

        if recovery_attempts >= max_recovery_attempts:
            return False
        if target_count is None:
            return True
        return current_count < target_count

    def try_recover_scroll(self, page: Page, previous_count: int) -> bool:
        """在页面疑似停止加载时，追加更保守的尾部探测。"""

        recovery_pauses = (
            max(self.settings.ozon_scrape_scroll_pause_ms * 2, 1_500),
            max(self.settings.ozon_scrape_scroll_pause_ms * 3, 2_500),
        )

        for pause_ms in recovery_pauses:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(pause_ms)
            current_count = page.locator(".tile-root[data-index]").count()
            print(f"\r  尾部补偿后已渲染 {current_count} 个商品卡片...", end="")
            if current_count > previous_count:
                return True

        return False

    def extract_all(self, page: Page) -> list[dict]:
        """从当前搜索结果页提取所有商品卡片数据。"""

        return page.evaluate(
            """
            () => {
              function parseNum(val) {
                if (!val || ['无数据', '-', '无跟卖'].includes(val.trim())) return null;
                const matched = val.match(/[+-]?[\\d.]+/);
                return matched ? parseFloat(matched[0]) : null;
              }

              function collectPluginData(card) {
                const pluginData = {};
                const knownLabels = new Set([
                  '类目', '品牌', 'rFBS佣金', 'FBP佣金', '月销量', '月销售额', '日销量', '日销售额',
                  '月销售动态', '商品卡片浏览量', '商品卡片加购率', '搜索和目录浏览量', '搜索和目录加购率',
                  '点击率', '参与促销天数', '参与促销的折扣', '促销活动的转化率', '付费推广天数',
                  '广告份额', '成交率', '退货取消率', '平均价格', '包装重量', '长宽高(mm)', '长宽高',
                  '发货模式', '跟卖者', '跟卖最低价', '上架时间', 'SKU'
                ]);
                const selectors = [
                  '.ozon-bang-item',
                  '[data-ozon-bang="true"]',
                  '[class*="ozon-bang-item"]',
                  '[class*="ozon-bang"]',
                  '[class*="shopbang"]',
                  '[data-plugin*="shopbang"]',
                  '[data-plugin*="ozon"]',
                ];

                const candidates = [];
                for (const selector of selectors) {
                  card.querySelectorAll(selector).forEach((node) => candidates.push(node));
                }

                if (candidates.length === 0) {
                  card.querySelectorAll('li, div, span, p').forEach((node) => {
                    const text = (node.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (!text || text.length > 300) return;
                    if (/月销量|跟卖者|发货模式|类目|品牌|退货取消率|成交率|点击率|包装重量/.test(text)) {
                      candidates.push(node);
                    }
                  });
                }

                const lines = [];
                const seenLines = new Set();
                for (const node of candidates) {
                  const rawText = node.innerText || node.textContent || '';
                  if (!rawText) continue;
                  for (const line of rawText.split(/\\n+/)) {
                    const normalized = line.replace(/\\s+/g, ' ').trim();
                    if (!normalized || seenLines.has(normalized)) continue;
                    seenLines.add(normalized);
                    lines.push(normalized);
                  }
                }

                for (let index = 0; index < lines.length; index += 1) {
                  const text = lines[index];
                  const separatorMatch = text.match(/[：:]/);
                  if (!separatorMatch) continue;

                  const idx = separatorMatch.index ?? -1;
                  if (idx < 0) continue;

                  const label = text.slice(0, idx).replace(/\\s+/g, '');
                  if (!label || !knownLabels.has(label)) continue;

                  let value = text.slice(idx + 1).trim();
                  if (!value) {
                    const valueParts = [];
                    for (let nextIndex = index + 1; nextIndex < lines.length; nextIndex += 1) {
                      const nextLine = lines[nextIndex];
                      const nextSeparatorMatch = nextLine.match(/[：:]/);
                      if (nextSeparatorMatch) {
                        const nextLabel = nextLine
                          .slice(0, nextSeparatorMatch.index ?? 0)
                          .replace(/\\s+/g, '');
                        if (knownLabels.has(nextLabel)) {
                          break;
                        }
                      }
                      valueParts.push(nextLine);
                    }
                    value = valueParts.join(' ').replace(/\\s+/g, ' ').trim();
                  }

                  if (!value) continue;
                  pluginData[label] = value;
                }

                return pluginData;
              }

              const seen = new Set();
              const results = [];

              document.querySelectorAll('.tile-root[data-index]').forEach(card => {
                const linkEl = card.querySelector('a[href*="/product/"]');
                if (!linkEl) return;

                const href = linkEl.getAttribute('href') || '';
                const skuMatch = href.match(/-(\\d{5,12})\\/?(?:[?#]|$)/);
                if (!skuMatch) return;

                const sku = skuMatch[1];
                if (seen.has(sku)) return;
                seen.add(sku);

                let name = '';
                const nameSpan = card.querySelector('.bq03_5_1-a span.tsBody500Medium, .bq03_5_1-a span');
                if (nameSpan) {
                  name = nameSpan.textContent?.trim() || '';
                }

                if (!name || name.length < 3) {
                  const nameLinks = card.querySelectorAll('a[href*="/product/"]');
                  for (const link of nameLinks) {
                    const text = link.textContent?.trim() || '';
                    if (text.length > 5 && !/^Распродажа|^-\\d+%|^Осталось|^Завтра/.test(text)) {
                      name = text;
                      break;
                    }
                  }
                }

                let imageUrl = '';
                const imageElement =
                  card.querySelector('img[src*="ozonstatic.cn"]') ||
                  card.querySelector('img[src*="ozone.ru"]');
                if (imageElement) {
                  imageUrl = imageElement.src.replace(/wc\\d+/, 'wc1000');
                }

                let price = null;
                const mainPriceElement = card.querySelector('span.tsHeadline500Medium');
                if (mainPriceElement) {
                  price = parseInt(mainPriceElement.textContent.replace(/[^\\d]/g, '')) || null;
                }

                let rating = null;
                let reviews = null;
                card.querySelectorAll('span').forEach(span => {
                  if (rating !== null) return;
                  const text = span.textContent?.trim();
                  if (/^[1-5](\\.\\d)?$/.test(text)) rating = parseFloat(text);
                });

                const reviewMatch = card.textContent.match(/(\\d[\\d\\s]*)\\s*отзыв/i);
                if (reviewMatch) reviews = parseInt(reviewMatch[1].replace(/\\s/g, ''));

                const pluginData = collectPluginData(card);

                const monthlySales = parseNum(pluginData['月销量']);
                const dailySales = parseNum(pluginData['日销量']);
                const growthRate = parseNum(pluginData['月销售动态']);
                const returnRate = parseNum(pluginData['退货取消率']);
                const conversionRate = parseNum(pluginData['成交率']);
                const ctr = parseNum(pluginData['点击率']);
                const cartAddRate = parseNum(pluginData['商品卡片加购率']);
                const searchViews = parseNum(pluginData['搜索和目录浏览量']);
                const adShare = parseNum(pluginData['广告份额']);
                const promotionDays = parseNum(pluginData['参与促销天数']);
                const weight = parseNum(pluginData['包装重量']);
                const shippingMode =
                  pluginData['发货模式'] && !pluginData['发货模式'].includes('非热销')
                    ? pluginData['发货模式']
                    : null;
                const category =
                  pluginData['类目'] && !pluginData['类目'].includes('非热销')
                    ? pluginData['类目']
                    : '';
                const brand =
                  pluginData['品牌'] && !pluginData['品牌'].includes('非热销')
                    ? pluginData['品牌']
                    : '';
                const avgPrice = parseNum(pluginData['平均价格']);
                const lowestCompetitor = pluginData['跟卖最低价'] || null;
                const sellers =
                  pluginData['跟卖者'] === '无跟卖'
                    ? 0
                    : pluginData['跟卖者'] === '无数据'
                      ? null
                      : parseNum(pluginData['跟卖者']);

                let listedDays = null;
                const listedText = pluginData['上架时间'] || '';
                const listedMatch = listedText.match(/\\((\\d+)天\\)/);
                if (listedMatch) listedDays = parseInt(listedMatch[1]);

                results.push({
                  sku,
                  name: name.slice(0, 200),
                  price,
                  rating,
                  reviews,
                  category,
                  brand,
                  monthlySales,
                  dailySales,
                  growthRate,
                  returnRate,
                  conversionRate,
                  ctr,
                  cartAddRate,
                  searchViews,
                  adShare,
                  promotionDays,
                  weight,
                  shippingMode,
                  sellers,
                  lowestCompetitor,
                  listedDays,
                  avgPrice,
                  hasPlugin: Object.keys(pluginData).length > 0,
                  url: href.startsWith('http') ? href : 'https://www.ozon.ru' + href,
                  imageUrl,
                });
              });

              return results;
            }
            """
        )

    def save_product_images(self, products: list[dict], image_cache: dict[str, bytes] | None = None) -> None:
        """保存商品主图。"""

        image_root = self.settings.ozon_scrape_image_path
        image_root.mkdir(parents=True, exist_ok=True)
        cache = image_cache or {}

        success_count = 0
        failed_count = 0

        for product in products:
            image_url = product.get("imageUrl")
            sku = product.get("sku")
            if not image_url or not sku:
                failed_count += 1
                continue

            sku_dir = image_root / str(sku)
            sku_dir.mkdir(parents=True, exist_ok=True)
            destination = sku_dir / "1.jpg"
            product["localImagePath"] = str(destination)
            if destination.exists():
                success_count += 1
                continue

            if self.try_save_from_cache(destination, image_url, cache):
                success_count += 1
                print(f"\r  已保存 {success_count} 张...", end="")
                continue

            if self.download_image(image_url, destination):
                success_count += 1
                print(f"\r  已下载 {success_count} 张...", end="")
            else:
                failed_count += 1

        print("")
        print(f"  完成：{success_count} 张成功，{failed_count} 张失败")
        print(f"  图片目录: {image_root}")

    def try_save_from_cache(self, destination: Path, image_url: str, image_cache: dict[str, bytes]) -> bool:
        """优先使用浏览器响应缓存写入图片。"""

        urls_to_try = [
            image_url,
            image_url.replace("wc1000", "wc500"),
            image_url.replace("wc1000", "wc300"),
        ]
        for url in urls_to_try:
            body = image_cache.get(url)
            if body:
                destination.write_bytes(body)
                return True
        return False

    def download_image(self, image_url: str, destination: Path) -> bool:
        """通过普通 HTTP 请求补下载图片。"""

        try:
            response = requests.get(
                image_url,
                headers=self.IMAGE_HEADERS,
                timeout=10,
                allow_redirects=True,
            )
            response.raise_for_status()
            destination.write_bytes(response.content)
            return True
        except Exception:
            return False

    def build_result_rows(self, products: list[dict]) -> list[dict]:
        """把原始商品数据转换为导出行。"""

        rows: list[dict] = []

        for index, product in enumerate(products, start=1):
            evaluation = EvaluationResult(
                fails=list(product.get("failReasons") or []),
                warns=list(product.get("warnings") or []),
                score=int(product.get("score") or 0),
                max_cost=product.get("estimatedMaxCost"),
                shipping=product.get("estimatedShipping"),
                tier=product.get("shippingTier"),
            )
            if not any(
                [
                    evaluation.fails,
                    evaluation.warns,
                    evaluation.score,
                    evaluation.max_cost is not None,
                    evaluation.shipping is not None,
                    evaluation.tier,
                    "passed" in product,
                ]
            ):
                evaluation = self.evaluate(product)
            passed = bool(product.get("passed")) if "passed" in product else len(evaluation.fails) == 0
            rows.append(
                {
                    "#": index,
                    "结果": "✅ 通过" if passed else "❌ 未通过",
                    "红线原因": " | ".join(evaluation.fails),
                    "注意事项": " | ".join(evaluation.warns),
                    "黄金评分": evaluation.score,
                    "SKU": product.get("sku"),
                    "商品链接": product.get("url"),
                    "主图路径": product.get("localImagePath"),
                    "商品名称": product.get("name"),
                    "属性数": len(product.get("attributes") or []),
                    "商品属性": self.format_attributes(product.get("attributes")),
                    "类目": product.get("category"),
                    "品牌": product.get("brand"),
                    "当前售价(₽)": product.get("price"),
                    "平均价格(₽)": product.get("avgPrice"),
                    "物流档位": evaluation.tier,
                    "包装重量(g)": product.get("weight"),
                    "预估运费(₽)": evaluation.shipping,
                    "最大成本(₽)": evaluation.max_cost,
                    "发货模式": product.get("shippingMode"),
                    "配送信息": product.get("deliveryInfo"),
                    "退货信息": product.get("returnInfo"),
                    "仓库信息": product.get("warehouseInfo"),
                    "俄罗斯本地仓": self.format_bool_text(product.get("isRussianLocalWarehouse")),
                    "上架天数": product.get("listedDays"),
                    "月销量(件)": product.get("monthlySales"),
                    "日销量(件)": product.get("dailySales"),
                    "月增速(%)": product.get("growthRate"),
                    "退货取消率(%)": product.get("returnRate"),
                    "成交率(%)": product.get("conversionRate"),
                    "点击率(%)": product.get("ctr"),
                    "加购率(%)": product.get("cartAddRate"),
                    "搜索浏览量": product.get("searchViews"),
                    "广告份额(%)": product.get("adShare"),
                    "促销天数": product.get("promotionDays"),
                    "跟卖者数": self.format_sellers_text(product.get("sellers")),
                    "跟卖最低价": product.get("lowestCompetitor"),
                    "评分": product.get("rating"),
                    "评价数": product.get("reviews"),
                    "有插件数据": "是" if product.get("hasPlugin") else "否",
                }
            )

        rows.sort(key=lambda item: (0 if "✅" in item["结果"] else 1, -int(item["黄金评分"])))
        return rows

    def enrich_products_with_attributes(
        self,
        products: list[dict[str, Any]],
        *,
        context: BrowserContext | None = None,
    ) -> list[dict[str, Any]]:
        """进入 Ozon 详情页补抓商品属性。"""

        if not products:
            return products

        if context is not None:
            page = context.new_page()
            try:
                for product in products:
                    try:
                        detail = self.fetch_product_detail_snapshot(page, product.get("url") or "")
                    except Exception as exc:
                        print(
                            f"[ozon-detail] sku={product.get('sku') or '-'} "
                            f"url={product.get('url') or '-'} failed: {exc}",
                            flush=True,
                        )
                        detail = {
                            "title": product.get("name") or "",
                            "price": product.get("price"),
                            "imageUrl": product.get("imageUrl") or "",
                            "specs": [],
                            "deliveryInfo": "",
                            "returnInfo": "",
                            "warehouseInfo": "",
                            "isRussianLocalWarehouse": False,
                        }
                    product["detailTitle"] = detail["title"] or product.get("name")
                    product["detailPrice"] = detail["price"] or product.get("price")
                    product["detailImageUrl"] = detail["imageUrl"] or product.get("imageUrl")
                    product["attributes"] = detail["specs"]
                    product["deliveryInfo"] = detail["deliveryInfo"]
                    product["returnInfo"] = detail["returnInfo"]
                    product["warehouseInfo"] = detail["warehouseInfo"]
                    product["isRussianLocalWarehouse"] = detail["isRussianLocalWarehouse"]
                return products
            finally:
                page.close()

        with sync_playwright() as playwright:
            session = self.open_detail_browser_session(playwright)
            context = session.context
            try:
                page = context.new_page()
                try:
                    for product in products:
                        try:
                            detail = self.fetch_product_detail_snapshot(page, product.get("url") or "")
                        except Exception as exc:
                            print(
                                f"[ozon-detail] sku={product.get('sku') or '-'} "
                                f"url={product.get('url') or '-'} failed: {exc}",
                                flush=True,
                            )
                            detail = {
                                "title": product.get("name") or "",
                                "price": product.get("price"),
                                "imageUrl": product.get("imageUrl") or "",
                                "specs": [],
                                "deliveryInfo": "",
                                "returnInfo": "",
                                "warehouseInfo": "",
                                "isRussianLocalWarehouse": False,
                            }
                        product["detailTitle"] = detail["title"] or product.get("name")
                        product["detailPrice"] = detail["price"] or product.get("price")
                        product["detailImageUrl"] = detail["imageUrl"] or product.get("imageUrl")
                        product["attributes"] = detail["specs"]
                        product["deliveryInfo"] = detail["deliveryInfo"]
                        product["returnInfo"] = detail["returnInfo"]
                        product["warehouseInfo"] = detail["warehouseInfo"]
                        product["isRussianLocalWarehouse"] = detail["isRussianLocalWarehouse"]
                    return products
                finally:
                    page.close()
            finally:
                session.close()

    def launch_detail_context(self, playwright: Playwright) -> BrowserContext:
        """基于已登录 profile 的副本启动详情页抓取浏览器。"""

        session = self.open_detail_browser_session(playwright)
        return session.context

    def open_detail_browser_session(self, playwright: Playwright):
        """打开详情抓取所需浏览器会话。"""

        if self.login_manager.should_use_cdp():
            return self.login_manager.open_browser_session(playwright)

        self.login_manager.validate_extension_assets()
        source_profile = self.settings.shopbang_user_data_path
        if not source_profile.exists():
            raise FileNotFoundError(f"未找到浏览器 profile: {source_profile}")

        copied_profile = self.prepare_profile_copy()
        launch_kwargs: dict[str, Any] = {
            "headless": self.settings.shopbang_headless,
            "args": self.login_manager._build_extension_args(),
            "locale": self.settings.ozon_browser_locale,
            "timezone_id": self.settings.ozon_browser_timezone,
            "viewport": {"width": 1440, "height": 900},
            "slow_mo": self.settings.playwright_slow_mo_ms,
        }
        if self.settings.ozon_user_agent:
            launch_kwargs["user_agent"] = self.settings.ozon_user_agent
        if self.settings.playwright_proxy_url:
            launch_kwargs["proxy"] = {"server": self.settings.playwright_proxy_url}
        if self.settings.playwright_channel:
            launch_kwargs["channel"] = self.settings.playwright_channel
        if self.settings.playwright_executable_path:
            launch_kwargs["executable_path"] = self.settings.playwright_executable_path

        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(copied_profile),
            **launch_kwargs,
        )
        from ozon_selection.collectors.ozon.shopbang_auth import ShopbangBrowserSession

        return ShopbangBrowserSession(context=context, owns_context=True)

    def prepare_profile_copy(self) -> Path:
        """创建当前浏览器 profile 的可复用副本。"""

        target_dir = self.settings.project_root / "browser-profile-e2e"
        if target_dir.exists():
            shutil.rmtree(target_dir)

        shutil.copytree(
            self.settings.shopbang_user_data_path,
            target_dir,
            ignore=shutil.ignore_patterns(
                "SingletonLock",
                "SingletonSocket",
                "SingletonCookie",
                "RunningChromeVersion",
            ),
        )

        for pattern in (
            "SingletonLock",
            "SingletonSocket",
            "SingletonCookie",
            "lockfile",
            "Default/LOCK",
            "Default/Code Cache/js/index.lock",
        ):
            for path in target_dir.glob(pattern):
                if path.is_file() or path.is_symlink():
                    path.unlink(missing_ok=True)

        return target_dir

    def fetch_product_detail_snapshot(self, page: Page, product_url: str) -> dict[str, Any]:
        """进入商品详情页并提取标题、价格、主图和属性。"""

        if not product_url:
            return {
                "title": "",
                "price": None,
                "imageUrl": "",
                "specs": [],
                "deliveryInfo": "",
                "returnInfo": "",
                "warehouseInfo": "",
                "isRussianLocalWarehouse": False,
            }

        page.goto(
            product_url,
            wait_until="domcontentloaded",
            timeout=self.settings.playwright_timeout_ms,
        )
        page.wait_for_timeout(4_000)
        self.try_expand_spec_sections(page)
        page.wait_for_timeout(1_500)
        page.wait_for_load_state("domcontentloaded", timeout=10_000)
        logistics = self.extract_detail_logistics(page)
        return {
            "title": self.extract_detail_title(page),
            "price": self.extract_detail_price(page),
            "imageUrl": self.extract_detail_image(page),
            "specs": self.extract_specs_from_page(page)[:MAX_DETAIL_SPECS],
            "deliveryInfo": logistics["deliveryInfo"],
            "returnInfo": logistics["returnInfo"],
            "warehouseInfo": logistics["warehouseInfo"],
            "isRussianLocalWarehouse": logistics["isRussianLocalWarehouse"],
        }

    @staticmethod
    def try_expand_spec_sections(page: Page) -> None:
        """尝试展开 Ozon 详情页中的规格区域。"""

        candidate_texts = [
            "Все характеристики",
            "Характеристики",
            "Показать полностью",
            "Развернуть",
            "Подробнее",
        ]

        for text in candidate_texts:
            try:
                locator = page.get_by_text(text, exact=False).first
                if locator.is_visible(timeout=1000):
                    locator.click(timeout=1000)
                    page.wait_for_timeout(800)
            except Exception:
                continue

    @staticmethod
    def extract_specs_from_page(page: Page) -> list[dict[str, str]]:
        """使用较宽松的 DOM 规则抽取规格键值。"""

        return page.evaluate(
            f"""
            () => {{
              const results = [];
              const seen = new Set();

              const pushSpec = (key, value) => {{
                const normalizedKey = (key || '').replace(/\\s+/g, ' ').trim();
                const normalizedValue = (value || '').replace(/\\s+/g, ' ').trim();
                if (!normalizedKey || !normalizedValue) return;
                if (normalizedKey.length > 60 || normalizedValue.length > 200) return;
                const composite = `${{normalizedKey}}::${{normalizedValue}}`;
                if (seen.has(composite)) return;
                seen.add(composite);
                results.push({{ key: normalizedKey, value: normalizedValue }});
              }};

              document.querySelectorAll('dl').forEach((dl) => {{
                const keys = Array.from(dl.querySelectorAll('dt'));
                const values = Array.from(dl.querySelectorAll('dd'));
                const count = Math.min(keys.length, values.length);
                for (let index = 0; index < count; index += 1) {{
                  pushSpec(keys[index].innerText, values[index].innerText);
                }}
              }});

              document.querySelectorAll('table tr').forEach((row) => {{
                const cells = row.querySelectorAll('th,td');
                if (cells.length >= 2) {{
                  pushSpec(cells[0].innerText, cells[1].innerText);
                }}
              }});

              document.querySelectorAll('[data-widget]').forEach((root) => {{
                root.querySelectorAll('div').forEach((node) => {{
                  const children = Array.from(node.children || []);
                  if (children.length === 2) {{
                    const left = children[0].innerText || '';
                    const right = children[1].innerText || '';
                    if (left && right && left.length <= 60 && right.length <= 200) {{
                      pushSpec(left, right);
                    }}
                  }}
                }});
              }});

              return results.slice(0, {MAX_DETAIL_SPECS});
            }}
            """
        )

    @staticmethod
    def extract_detail_title(page: Page) -> str:
        """从详情页提取标题。"""

        candidates = [
            "h1",
            "[data-widget='webProductHeading'] h1",
            "[data-widget='webProductHeading']",
        ]
        for selector in candidates:
            try:
                locator = page.locator(selector).first
                text = locator.inner_text(timeout=2000).strip()
                if text:
                    return text
            except Exception:
                continue
        return ""

    @staticmethod
    def extract_detail_price(page: Page) -> int | None:
        """从详情页提取价格。"""

        candidates = [
            "[data-widget='webPrice']",
            "span.tsHeadline500Medium",
            "[class*='price']",
        ]
        for selector in candidates:
            try:
                text = page.locator(selector).first.inner_text(timeout=1500)
                digits = "".join(char for char in text if char.isdigit())
                if digits:
                    return int(digits)
            except Exception:
                continue
        return None

    @staticmethod
    def extract_detail_image(page: Page) -> str:
        """从详情页提取主图 URL。"""

        return page.evaluate(
            """
            () => {
              const selectors = [
                'img[src*="ozonstatic"]',
                'img[src*="ozone.ru"]',
                'img',
              ];
              for (const selector of selectors) {
                const images = Array.from(document.querySelectorAll(selector));
                for (const image of images) {
                  const src = image.getAttribute('src') || '';
                  if (src && /^https?:/.test(src)) {
                    return src.replace(/wc\\d+/, 'wc1000');
                  }
                }
              }
              return '';
            }
            """
        )

    @staticmethod
    def extract_detail_logistics(page: Page) -> dict[str, Any]:
        """从详情页提取配送、退货和仓库相关信息。"""

        lines = page.evaluate(
            """
            () => {
              const bodyText = document.body?.innerText || '';
              const rawLines = bodyText.split('\\n');
              const keywords = ['достав', 'возврат', 'склад', 'росси', 'russia', 'warehouse', '退货', '配送', '俄罗斯'];
              const results = [];

              for (const rawLine of rawLines) {
                const line = rawLine.replace(/\\s+/g, ' ').trim();
                if (!line || line.length < 3 || line.length > 240) continue;
                const lowered = line.toLowerCase();
                if (!keywords.some((keyword) => lowered.includes(keyword))) continue;
                if (results.includes(line)) continue;
                results.push(line);
                if (results.length >= 80) break;
              }

              return results;
            }
            """
        )
        return ProductCollector.analyze_detail_logistics_lines(lines)

    @staticmethod
    def analyze_detail_logistics_lines(lines: list[str] | None) -> dict[str, Any]:
        """从详情页文本行中归纳配送、退货和仓库信息。"""

        normalized_lines: list[str] = []
        seen: set[str] = set()
        for line in lines or []:
            normalized = re.sub(r"\s+", " ", str(line or "")).strip()
            if not normalized:
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized_lines.append(normalized)

        delivery_info = ProductCollector.pick_detail_line(
            normalized_lines,
            keywords=("достав", "配送", "delivery"),
        )
        return_info = ProductCollector.pick_detail_line(
            normalized_lines,
            keywords=("возврат", "退货", "return"),
        )
        warehouse_info = ProductCollector.pick_detail_line(
            normalized_lines,
            keywords=("склад", "仓", "warehouse", "本地仓", "俄仓"),
        ) or ProductCollector.pick_detail_line(
            normalized_lines,
            keywords=("росси", "russia"),
        )

        logistics_context = " | ".join(
            line
            for line in normalized_lines
            if ProductCollector.line_contains_keywords(
                line,
                keywords=("достав", "склад", "росси", "russia", "warehouse", "配送", "仓", "俄罗斯", "本地仓", "俄仓"),
            )
        ).lower()

        is_russian_local_warehouse = any(
            re.search(pattern, logistics_context, flags=re.IGNORECASE)
            for pattern in (
                r"со\s+склада\s+в\s+росси",
                r"склад\w*\s+в\s+росси",
                r"российск\w+\s+склад",
                r"достав\w*.*из\s+росси",
                r"из\s+росси",
                r"warehouse.*russia",
                r"local warehouse",
                r"俄罗斯",
                r"本地仓",
                r"俄仓",
            )
        )

        return {
            "deliveryInfo": delivery_info,
            "returnInfo": return_info,
            "warehouseInfo": warehouse_info,
            "isRussianLocalWarehouse": is_russian_local_warehouse,
        }

    @staticmethod
    def pick_detail_line(lines: list[str], *, keywords: tuple[str, ...]) -> str:
        """从文本行中挑出首个包含目标关键词的候选。"""

        for line in lines:
            if ProductCollector.line_contains_keywords(line, keywords=keywords):
                return line
        return ""

    @staticmethod
    def line_contains_keywords(line: str, *, keywords: tuple[str, ...]) -> bool:
        """判断文本是否包含任一关键词。"""

        lowered = line.lower()
        return any(keyword.lower() in lowered for keyword in keywords)

    @staticmethod
    def format_attributes(attributes: list[dict[str, str]] | None) -> str:
        """把属性列表压平成便于查看的文本。"""

        if not attributes:
            return ""
        return " | ".join(
            f"{item.get('key', '').strip()}: {item.get('value', '').strip()}"
            for item in attributes
            if item.get("key") and item.get("value")
        )

    def export_to_excel(self, rows: list[dict], keyword: str) -> Path:
        """导出结果表和选品标准表。"""

        output_dir = self.settings.ozon_scrape_output_path
        output_dir.mkdir(parents=True, exist_ok=True)

        safe_keyword = re.sub(r"\s+", "_", keyword).strip("_") or "ozon"
        output_file = output_dir / f"选品_{safe_keyword}_{datetime.now().date().isoformat()}.xlsx"

        result_frame = pd.DataFrame(rows)
        standard_frame = pd.DataFrame(self.STANDARD_ROWS[1:], columns=self.STANDARD_ROWS[0])

        with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
            result_frame.to_excel(writer, sheet_name="选品结果", index=False)
            standard_frame.to_excel(writer, sheet_name="选品标准", index=False)

        workbook = load_workbook(output_file)
        result_sheet = workbook["选品结果"]
        standard_sheet = workbook["选品标准"]

        for column_index, width in enumerate(self.COLUMN_WIDTHS, start=1):
            result_sheet.column_dimensions[self._column_letter(column_index)].width = width

        standard_widths = [14, 16, 20, 30]
        for column_index, width in enumerate(standard_widths, start=1):
            standard_sheet.column_dimensions[self._column_letter(column_index)].width = width

        workbook.save(output_file)
        return output_file

    def inspect_plugin_state(self, page: Page) -> dict[str, Any]:
        """检查 Ozon 页面上的插件是否已返回真实数据。"""

        plugin_snapshot = page.evaluate(
            """
            () => {
              const dataPattern = /月销量|月销售额|日销量|日销售额|月销售动态|商品卡片浏览量|商品卡片加购率|搜索和目录浏览量|搜索和目录加购率|点击率|参与促销天数|参与促销的折扣|促销活动的转化率|付费推广天数|广告份额|成交率|退货取消率|平均价格|包装重量|长宽高|发货模式|类目|品牌|跟卖者|跟卖最低价|上架时间|SKU/;
              const selectors = [
                '.ozon-bang-item',
                '[data-ozon-bang="true"]',
                '[class*="ozon-bang-item"]',
                '[class*="ozon-bang"]',
                '[class*="shopbang"]',
                '[data-plugin*="shopbang"]',
                '[data-plugin*="ozon"]',
              ];

              const samples = [];
              const seen = new Set();
              let selectorMatchCount = 0;
              let tileCount = 0;

              const pushSample = (rawText) => {
                const sourceText = rawText || '';
                if (!sourceText.trim()) return;
                const parts = sourceText.split(/\\n+/);
                parts.forEach((part) => {
                  const normalized = (part || '').replace(/\\s+/g, ' ').trim();
                  if (!normalized || normalized.length > 400) return;
                  if (!dataPattern.test(normalized)) return;
                  if (seen.has(normalized)) return;
                  seen.add(normalized);
                  samples.push(normalized);
                });
              };

              selectors.forEach((selector) => {
                document.querySelectorAll(selector).forEach((element) => {
                  selectorMatchCount += 1;
                  pushSample(element.innerText || element.textContent || '');
                });
              });

              tileCount = document.querySelectorAll('.tile-root[data-index]').length;

              if (samples.length === 0) {
                document.querySelectorAll(
                  '.tile-root[data-index] li, .tile-root[data-index] div, .tile-root[data-index] span, .tile-root[data-index] p, ' +
                  '[data-ozon-bang="true"] li, [data-ozon-bang="true"] div, [data-ozon-bang="true"] span, [data-ozon-bang="true"] p'
                )
                  .forEach((element) => {
                    pushSample(element.innerText || element.textContent || '');
                  });
              }

              return {
                plugin_count: Math.max(selectorMatchCount, samples.length),
                selector_match_count: selectorMatchCount,
                tile_count: tileCount,
                samples: samples.slice(0, 10),
              };
            }
            """
        )
        samples = list(plugin_snapshot.get("samples") or [])
        plugin_count = int(plugin_snapshot.get("plugin_count") or 0)
        selector_match_count = int(plugin_snapshot.get("selector_match_count") or 0)
        tile_count = int(plugin_snapshot.get("tile_count") or 0)
        real_data_count = sum(1 for sample in samples if self.plugin_card_has_real_data(sample))
        placeholder_count = sum(1 for sample in samples if self.plugin_card_is_login_placeholder(sample))
        return {
            "plugin_count": plugin_count,
            "selector_match_count": selector_match_count,
            "tile_count": tile_count,
            "samples": samples,
            "real_data_count": real_data_count,
            "placeholder_count": placeholder_count,
            "valid": plugin_count > 0 and real_data_count > 0 and placeholder_count < len(samples),
        }

    @staticmethod
    def parse_num(value: str | None) -> float | None:
        """解析插件文本中的数字。"""

        if value is None:
            return None

        normalized = value.strip()
        if normalized in {"无数据", "-", "无跟卖", ""}:
            return None

        matched = re.search(r"[+-]?[\d.]+", normalized)
        return float(matched.group(0)) if matched else None

    @staticmethod
    def format_sellers_text(value: Any) -> Any:
        """格式化导出用的跟卖人数显示文本。"""

        if value is None:
            return "无数据"
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return value
        if numeric_value == 0:
            return "无跟卖"
        if numeric_value.is_integer():
            return int(numeric_value)
        return numeric_value

    def profit_calc(self, price: float | int | None, weight: float | int | None) -> ProfitEstimate:
        """估算最大成本、运费和物流档位。"""

        if not price:
            return ProfitEstimate(max_cost=None, shipping=None, tier=None)

        effective_weight = weight or 300
        tier = "Extra Small(≤500g)" if price <= 1500 else "Small(≤2000g)"
        shipping_cny = 3 + 0.035 * effective_weight if price <= 1500 else 16 + 0.035 * effective_weight
        shipping_rub = shipping_cny * self.settings.default_exchange_rate_cny_to_rub
        max_cost = round(price * (0.8 - MIN_PROFIT_MARGIN) - shipping_rub)
        return ProfitEstimate(max_cost=max_cost, shipping=round(shipping_rub, 1), tier=tier)

    def evaluate(self, product: dict[str, Any]) -> EvaluationResult:
        """应用选品红线与黄金评分逻辑。"""

        fails: list[str] = []
        warns: list[str] = []

        price = product.get("price")
        sellers = product.get("sellers")
        return_rate = product.get("returnRate")
        growth_rate = product.get("growthRate")
        monthly_sales = product.get("monthlySales")
        listed_days = product.get("listedDays")
        weight = product.get("weight")
        is_russian_local_warehouse = bool(product.get("isRussianLocalWarehouse"))

        if not price or price < PRICE_MIN or price > PRICE_MAX:
            fails.append(f"价格{price if price is not None else '未知'}₽ 需500-20000₽")
        if is_russian_local_warehouse:
            fails.append("俄罗斯本地仓")
        if sellers is None:
            fails.append("跟卖者数据缺失")
        elif not MIN_SELLERS <= sellers <= MAX_SELLERS:
            fails.append(f"跟卖者{sellers}个 不在{MIN_SELLERS}-{MAX_SELLERS}之间")
        if return_rate is not None and return_rate > MAX_RETURN_RATE:
            fails.append(f"退货率{return_rate}% >20%")
        if growth_rate is not None and growth_rate < 0:
            fails.append("月销售动态负增长")
        if monthly_sales is None:
            fails.append("月销量数据缺失")
        elif not MONTHLY_SALES_MIN <= monthly_sales <= MONTHLY_SALES_MAX:
            fails.append(f"月销{monthly_sales}件 不在{MONTHLY_SALES_MIN}-{MONTHLY_SALES_MAX}之间")
        if listed_days is not None and not MIN_LISTED_DAYS <= listed_days <= MAX_LISTED_DAYS:
            fails.append(f"上架{listed_days}天(需在{MIN_LISTED_DAYS}-{MAX_LISTED_DAYS}天之间)")
        if price and weight:
            max_weight = 500 if price <= 1500 else 2000
            if weight > max_weight:
                fails.append(f"重量{weight}g >{max_weight}g限制")

        estimate = self.profit_calc(price, weight)
        if estimate.max_cost is not None and estimate.max_cost <= 0:
            fails.append("价格过低，利润率无法大于10%")

        score = 0
        conversion_rate = product.get("conversionRate")
        ctr = product.get("ctr")
        promotion_days = product.get("promotionDays")
        search_views = product.get("searchViews")
        rating = product.get("rating")

        if price and 600 <= price <= 5000:
            score += 2
        if monthly_sales and 300 <= monthly_sales <= 800:
            score += 3
        elif monthly_sales and monthly_sales >= 200:
            score += 1

        if growth_rate and growth_rate >= 20:
            score += 3
        elif growth_rate and growth_rate >= 15:
            score += 1

        if sellers is not None and 5 <= sellers <= 15:
            score += 2
        if return_rate is not None and return_rate < 8:
            score += 2
        if conversion_rate is not None and conversion_rate > 80:
            score += 1
        if ctr is not None and 3 <= ctr <= 6:
            score += 1
        if promotion_days is not None and promotion_days < 15:
            score += 1
        if listed_days and listed_days > 180:
            score += 1
        if search_views and search_views >= 100000:
            score += 1
        if rating and rating >= 4.5:
            score += 1
        if not fails:
            score += 2

        return EvaluationResult(
            fails=fails,
            warns=warns,
            score=score,
            max_cost=estimate.max_cost,
            shipping=estimate.shipping,
            tier=estimate.tier,
        )

    @classmethod
    def plugin_card_is_login_placeholder(cls, text: str) -> bool:
        """判断插件卡片是否仍是未登录占位态。"""

        normalized = "".join(text.split())
        if not normalized:
            return False
        if normalized.count("登录") >= 5:
            return True
        return any(marker in normalized for marker in cls.PLUGIN_LOGIN_MARKERS)

    @classmethod
    def plugin_card_has_real_data(cls, text: str) -> bool:
        """判断插件卡片是否已返回真实业务数据。"""

        normalized = "".join(text.split())
        if not normalized or cls.plugin_card_is_login_placeholder(normalized):
            return False
        return any(
            marker in normalized
            for marker in (
                "无数据",
                "无跟卖",
                "非热销",
                "FBO",
                "FBS",
                "rFBS",
                "%",
                "₽",
                "件",
                "天",
            )
        )

    def _build_image_response_handler(self, image_cache: dict[str, bytes]):
        """返回用于缓存图片响应体的事件处理器。"""

        def handler(response: Response) -> None:
            try:
                url = response.url
                if "ozonstatic.cn" not in url or not re.search(r"\.(jpg|jpeg|webp|png)", url, re.IGNORECASE):
                    return
                if response.status != 200:
                    return
                image_cache[url] = response.body()
            except Exception:
                return

        return handler

    @staticmethod
    def format_bool_text(value: bool | None) -> str:
        """把布尔值格式化为中文文案。"""

        if value is True:
            return "是"
        if value is False:
            return "否"
        return ""

    @staticmethod
    def _column_letter(index: int) -> str:
        """把列序号转成 Excel 列名。"""

        result = ""
        current = index
        while current:
            current, remainder = divmod(current - 1, 26)
            result = chr(65 + remainder) + result
        return result
