"""在 Ozon 当前专题页中查找有评论的跟卖店铺。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
from openpyxl import load_workbook
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import Settings, get_settings
from ozon_selection.collectors.ozon.shopbang_auth import ShopbangLoginManager
from ozon_selection.repositories.ozon_reviewed_seller_repository import OzonReviewedSellerRepository


DEFAULT_START_URL = (
    "https://www.ozon.ru/highlight/tovary-iz-kitaya-935133/"
    "?currency_price=500.000%3B8000.000&opened=category"
)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="在 Ozon 当前专题页中查找有评论的跟卖店铺。")
    parser.add_argument(
        "--start-url",
        type=str,
        default=DEFAULT_START_URL,
        help="Ozon 专题页或列表页 URL，默认是“товары из китая”专题页。",
    )
    parser.add_argument(
        "--max-products",
        type=int,
        default=20,
        help="最多处理多少个商品详情页，默认 20。",
    )
    parser.add_argument(
        "--max-scroll-rounds",
        type=int,
        default=8,
        help="进入跟卖面板后，最多向下滚动多少轮以加载更多卖家。",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="后台模式运行浏览器。",
    )
    return parser.parse_args()


def normalize_ozon_url(url: str, base_url: str) -> str:
    """归一化 Ozon URL，去掉 query 和 fragment。"""

    if not url:
        return ""
    absolute = url if url.startswith("http") else f"{base_url.rstrip('/')}/{url.lstrip('/')}"
    parsed = urlsplit(absolute)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def extract_product_sku(product_url: str) -> str:
    """从 Ozon 商品 URL 中提取 sku。"""

    matched = re.search(r"-(\d+)(?:/)?$", urlsplit(product_url).path.rstrip("/"))
    return matched.group(1) if matched else ""


def wait_listing_ready(page: Page) -> None:
    """等待列表页首屏商品出现。"""

    page.wait_for_selector(".tile-root[data-index]", timeout=20_000)
    page.wait_for_timeout(2_000)


def collect_listing_product_urls(page: Page, settings: Settings, max_products: int | None = None) -> list[str]:
    """读取当前列表页中的商品详情链接。"""

    urls = page.evaluate(
        """() => {
        const tiles = [...document.querySelectorAll('.tile-root[data-index]')];
        return tiles
          .map((tile) => tile.querySelector('a[href*="/product/"]')?.href || '')
          .filter(Boolean);
    }"""
    )

    deduped: list[str] = []
    seen: set[str] = set()
    for raw_url in urls:
        normalized = normalize_ozon_url(str(raw_url), settings.ozon_base_url)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
        if max_products is not None and max_products > 0 and len(deduped) >= max_products:
            break
    return deduped


def collect_unprocessed_listing_product_urls(
    *,
    page: Page,
    settings: Settings,
    processed_skus: set[str],
    target_count: int,
) -> tuple[list[str], list[str], int]:
    """持续滚动列表页，直到收集到足够多的未处理商品或页面不再加载新商品。"""

    desired_count = max(int(target_count), 1)
    candidate_product_urls: list[str] = []
    unprocessed_product_urls: list[str] = []
    duplicate_product_count = 0
    previous_candidate_count = 0
    stale_rounds = 0
    max_stale_rounds = 4

    while True:
        candidate_product_urls = collect_listing_product_urls(page, settings, max_products=None)
        unprocessed_product_urls, duplicate_product_count = filter_unprocessed_product_urls(
            product_urls=candidate_product_urls,
            processed_skus=processed_skus,
        )

        print(
            f"[listing] rendered_candidates={len(candidate_product_urls)} "
            f"unprocessed={len(unprocessed_product_urls)} duplicates={duplicate_product_count}",
            flush=True,
        )

        if len(unprocessed_product_urls) >= desired_count:
            break

        current_candidate_count = len(candidate_product_urls)
        if current_candidate_count <= previous_candidate_count:
            stale_rounds += 1
        else:
            stale_rounds = 0
        previous_candidate_count = current_candidate_count

        if stale_rounds >= max_stale_rounds:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2_500)

            candidate_after_bottom = collect_listing_product_urls(page, settings, max_products=None)
            if len(candidate_after_bottom) <= current_candidate_count:
                break

            stale_rounds = 0
            previous_candidate_count = len(candidate_after_bottom)
            continue

        page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 2, 1800))")
        page.wait_for_timeout(1_500)

    return candidate_product_urls, unprocessed_product_urls[:desired_count], duplicate_product_count


def filter_unprocessed_product_urls(
    *,
    product_urls: list[str],
    processed_skus: set[str],
) -> tuple[list[str], int]:
    """按 SKU 过滤已处理过的商品。"""

    filtered: list[str] = []
    skipped_count = 0
    seen_skus_in_run: set[str] = set()

    for product_url in product_urls:
        sku = extract_product_sku(product_url)
        if sku:
            if sku in processed_skus or sku in seen_skus_in_run:
                skipped_count += 1
                continue
            seen_skus_in_run.add(sku)
        filtered.append(product_url)

    return filtered, skipped_count


def find_offer_button(page: Page):
    """定位“有更便宜/更快”卖家展开按钮。"""

    candidates = [
        page.locator('div[data-widget="webBestSeller"] button'),
        page.get_by_role("button", name=re.compile(r"(Есть дешевле|Есть другие предложения|быстрее)", re.I)),
    ]
    for locator in candidates:
        try:
            if locator.count() > 0:
                return locator.first
        except Exception:
            continue
    return None


def open_offer_panel(page: Page) -> str:
    """点击展开跟卖者面板。"""

    button = find_offer_button(page)
    if button is None:
        return ""

    button_text = ""
    try:
        button_text = re.sub(r"\s+", " ", button.inner_text(timeout=3_000)).strip()
    except Exception:
        button_text = ""

    button.scroll_into_view_if_needed(timeout=3_000)
    page.wait_for_timeout(500)
    button.click(timeout=10_000)
    page.wait_for_timeout(2_000)
    return button_text


def extract_reviewed_sellers(page: Page, settings: Settings) -> list[dict[str, Any]]:
    """从当前详情页已展开的跟卖区域中提取有评论的卖家。"""

    return page.evaluate(
        """(baseUrl) => {
        const normalize = (href) => {
          if (!href) return '';
          const absolute = new URL(href, baseUrl).toString();
          const parsed = new URL(absolute);
          return `${parsed.origin}${parsed.pathname}`;
        };

        const parseReviewCount = (text) => {
          if (!text) return 0;
          const matched = text.match(/(\\d[\\d\\s\\u00a0]*)\\s+отзыв(?:а|ов)?/i);
          if (!matched) return 0;
          const digits = matched[1].replace(/[\\s\\u00a0]/g, '');
          return Number.parseInt(digits, 10) || 0;
        };

        const collectReviewTexts = (row) => {
          const texts = [];
          const elements = [row, ...row.querySelectorAll('*')];
          for (const element of elements) {
            const text = (element.textContent || '').replace(/\\s+/g, ' ').trim();
            if (!text) continue;
            if (/^\\d[\\d\\s\\u00a0]*\\s+отзыв(?:а|ов)?$/i.test(text)) {
              texts.push(text);
            }
          }
          return texts;
        };

        const results = [];
        const seen = new Set();
        const anchors = [...document.querySelectorAll('a[href*="/seller/"]')];

        for (const anchor of anchors) {
          const rawName = (
            anchor.getAttribute('title') ||
            anchor.textContent ||
            anchor.querySelector('img[alt]')?.getAttribute('alt') ||
            ''
          ).replace(/\\s+/g, ' ').trim();
          if (!rawName) continue;

          let row = null;
          let current = anchor;
          for (let i = 0; i < 9 && current; i += 1) {
            const text = (current.textContent || '').replace(/\\s+/g, ' ').trim();
            if (/Перейти в магазин/i.test(text) && (/отзыв/i.test(text) || /В корзину/i.test(text))) {
              row = current;
              break;
            }
            current = current.parentElement;
          }
          if (!row) continue;

          const rowText = (row.textContent || '').replace(/\\s+/g, ' ').trim();
          const reviewTexts = collectReviewTexts(row);
          const reviewText = reviewTexts.find((text) => parseReviewCount(text) > 0) || '';
          const reviewCount = parseReviewCount(reviewText);
          if (reviewCount <= 0) continue;

          const sellerUrl = normalize(anchor.getAttribute('href') || '');
          if (!sellerUrl || seen.has(sellerUrl)) continue;
          seen.add(sellerUrl);

          results.push({
            seller_name: rawName,
            seller_url: sellerUrl,
            review_count: reviewCount,
            review_text: reviewText,
            row_text: rowText.slice(0, 500),
          });
        }

        return results;
      }""",
        settings.ozon_base_url,
    )


def collect_product_title(page: Page) -> str:
    """提取当前商品标题。"""

    title_selectors = [
        "h1",
        'meta[property="og:title"]',
        'title',
    ]
    for selector in title_selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() == 0:
                continue
            if selector.startswith("meta"):
                text = locator.get_attribute("content") or ""
            else:
                text = locator.inner_text(timeout=2_000)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                return text
        except Exception:
            continue
    return ""


def collect_sellers_for_product(
    *,
    page: Page,
    product_url: str,
    settings: Settings,
    max_scroll_rounds: int,
) -> dict[str, Any]:
    """处理单个商品详情页，返回有评论的跟卖店铺。"""

    page.goto(product_url, wait_until="domcontentloaded", timeout=settings.playwright_timeout_ms)
    page.wait_for_timeout(3_000)

    product_title = collect_product_title(page)
    offer_button_text = open_offer_panel(page)
    if not offer_button_text:
        return {
            "product_url": product_url,
            "product_sku": extract_product_sku(product_url),
            "product_title": product_title,
            "offer_button_text": "",
            "reviewed_sellers": [],
            "skipped_reason": "未找到跟卖展开按钮",
        }

    collected: dict[str, dict[str, Any]] = {}
    stable_rounds = 0
    previous_count = 0

    for _ in range(max_scroll_rounds):
        for seller in extract_reviewed_sellers(page, settings):
            collected[seller["seller_url"]] = seller

        current_count = len(collected)
        if current_count == previous_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
        if stable_rounds >= 2:
            break

        previous_count = current_count
        page.evaluate("window.scrollBy(0, Math.max(window.innerHeight, 900))")
        page.wait_for_timeout(1_200)

    return {
        "product_url": product_url,
        "product_sku": extract_product_sku(product_url),
        "product_title": product_title,
        "offer_button_text": offer_button_text,
        "reviewed_sellers": list(collected.values()),
        "skipped_reason": "",
    }


def build_export_rows(product_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把结果摊平成 Excel/JSON 友好的行。"""

    rows: list[dict[str, Any]] = []
    for product_index, product in enumerate(product_results, start=1):
        sellers = product.get("reviewed_sellers") or []
        if not sellers:
            rows.append(
                {
                    "序号": product_index,
                    "商品SKU": product.get("product_sku", ""),
                    "商品标题": product.get("product_title", ""),
                    "商品URL": product.get("product_url", ""),
                    "跟卖入口文案": product.get("offer_button_text", ""),
                    "店铺名": "",
                    "店铺URL": "",
                    "评论数": 0,
                    "评论文本": "",
                    "备注": product.get("skipped_reason", "未找到有评论的跟卖店铺"),
                }
            )
            continue

        for seller in sellers:
            rows.append(
                {
                    "序号": product_index,
                    "商品SKU": product.get("product_sku", ""),
                    "商品标题": product.get("product_title", ""),
                    "商品URL": product.get("product_url", ""),
                    "跟卖入口文案": product.get("offer_button_text", ""),
                    "店铺名": seller.get("seller_name", ""),
                    "店铺URL": seller.get("seller_url", ""),
                    "评论数": seller.get("review_count", 0),
                    "评论文本": seller.get("review_text", ""),
                    "备注": "",
                }
            )
    return rows


def write_json_report(*, settings: Settings, payload: dict[str, Any]) -> Path:
    """导出 JSON 报告。"""

    output_dir = settings.ozon_scrape_output_path
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"ozon_reviewed_sellers_{timestamp}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def export_to_excel(*, settings: Settings, rows: list[dict[str, Any]]) -> Path:
    """导出 Excel 报告。"""

    output_dir = settings.ozon_scrape_output_path
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"ozon_reviewed_sellers_{timestamp}.xlsx"

    frame = pd.DataFrame(rows)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="有评论跟卖店铺", index=False)

    workbook = load_workbook(output_path)
    sheet = workbook["有评论跟卖店铺"]
    widths = [8, 16, 42, 42, 28, 28, 42, 12, 16, 32]
    for column_index, width in enumerate(widths, start=1):
        sheet.column_dimensions[column_letter(column_index)].width = width
    workbook.save(output_path)
    return output_path


def column_letter(index: int) -> str:
    """把列号转成 Excel 列字母。"""

    letters = ""
    current = index
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def main() -> None:
    """执行脚本入口。"""

    args = parse_args()
    settings = get_settings().model_copy(
        deep=True,
        update={
            "shopbang_headless": bool(args.background),
        },
    )
    login_manager = ShopbangLoginManager(settings)
    repository = OzonReviewedSellerRepository(settings=settings)

    with sync_playwright() as playwright:
        login_manager.validate_collection_prerequisites()
        session = login_manager.open_browser_session(playwright=playwright)
        context = session.context
        listing_page = context.new_page()
        detail_page = context.new_page()

        try:
            listing_page.goto(
                args.start_url,
                wait_until="domcontentloaded",
                timeout=settings.playwright_timeout_ms,
            )
            wait_listing_ready(listing_page)
            processed_skus = repository.list_processed_skus()
            candidate_product_urls, product_urls, duplicate_product_count = collect_unprocessed_listing_product_urls(
                page=listing_page,
                settings=settings,
                processed_skus=processed_skus,
                target_count=max(1, args.max_products),
            )
            if not candidate_product_urls:
                raise RuntimeError(f"当前页面未找到商品链接: {args.start_url}")

            print(f"listing_url: {listing_page.url}")
            print(f"candidate_product_count: {len(candidate_product_urls)}")
            print(f"duplicate_product_count: {duplicate_product_count}")
            print(f"product_count: {len(product_urls)}")

            product_results: list[dict[str, Any]] = []
            for index, product_url in enumerate(product_urls, start=1):
                print(f"[{index}/{len(product_urls)}] {product_url}", flush=True)
                try:
                    result = collect_sellers_for_product(
                        page=detail_page,
                        product_url=product_url,
                        settings=settings,
                        max_scroll_rounds=max(1, args.max_scroll_rounds),
                    )
                except PlaywrightTimeoutError as exc:
                    result = {
                        "product_url": product_url,
                        "product_sku": extract_product_sku(product_url),
                        "product_title": "",
                        "offer_button_text": "",
                        "reviewed_sellers": [],
                        "skipped_reason": f"详情页超时: {exc}",
                    }
                except Exception as exc:
                    result = {
                        "product_url": product_url,
                        "product_sku": extract_product_sku(product_url),
                        "product_title": "",
                        "offer_button_text": "",
                        "reviewed_sellers": [],
                        "skipped_reason": str(exc),
                    }

                print(
                    f"  sellers_with_reviews: {len(result.get('reviewed_sellers') or [])}"
                    f" note={result.get('skipped_reason', '')}",
                    flush=True,
                )
                product_results.append(result)

            rows = build_export_rows(product_results)
            sqlite_result = repository.save_results(
                product_results=product_results,
                start_url=args.start_url,
                listing_url=listing_page.url,
            )
            payload = {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "start_url": args.start_url,
                "listing_url": listing_page.url,
                "candidate_product_count": len(candidate_product_urls),
                "duplicate_product_count": duplicate_product_count,
                "product_count": len(product_urls),
                "matched_product_count": sum(1 for item in product_results if item.get("reviewed_sellers")),
                "matched_seller_count": sum(len(item.get("reviewed_sellers") or []) for item in product_results),
                "sqlite_result": sqlite_result,
                "rows": rows,
                "products": product_results,
            }
            json_path = write_json_report(settings=settings, payload=payload)
            excel_path = export_to_excel(settings=settings, rows=rows)

            print(f"matched_product_count: {payload['matched_product_count']}")
            print(f"matched_seller_count: {payload['matched_seller_count']}")
            print(f"sqlite_status: {sqlite_result['status']}")
            if sqlite_result.get("saved_product_count") is not None:
                print(f"sqlite_saved_product_count: {sqlite_result['saved_product_count']}")
            if sqlite_result.get("saved_shop_count") is not None:
                print(f"sqlite_saved_shop_count: {sqlite_result['saved_shop_count']}")
            if sqlite_result.get("new_shop_count") is not None:
                print(f"sqlite_new_shop_count: {sqlite_result['new_shop_count']}")
            if sqlite_result.get("existing_shop_count") is not None:
                print(f"sqlite_existing_shop_count: {sqlite_result['existing_shop_count']}")
            if sqlite_result.get("reason"):
                print(f"sqlite_note: {sqlite_result['reason']}")
            print(f"json_path: {json_path}")
            print(f"excel_path: {excel_path}")
        finally:
            try:
                if not listing_page.is_closed():
                    listing_page.close()
            except Exception:
                pass
            try:
                if not detail_page.is_closed():
                    detail_page.close()
            except Exception:
                pass
            session.close()


if __name__ == "__main__":
    main()
