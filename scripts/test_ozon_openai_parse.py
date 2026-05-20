"""端到端验证脚本：从 Ozon 获取商品，再调用 OpenAI 解析。

流程说明：
1. 打开 Ozon 搜索页，抓取一个带上品帮真实数据的商品
2. 进入商品详情页，尽量提取规格参数
3. 调用 parse_product() 做结构化采购解析
4. 保存原始输入与解析输出，方便人工核对
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings
from ozon_selection.collectors.ozon.product_collector import ProductCollector
from ozon_selection.services.product_parser import parse_product


MAX_CARD_SCAN = 20
MAX_SPECS = 30


def main() -> None:
    """执行一次 Ozon -> OpenAI 的真实联调。"""

    settings = get_settings()
    settings.product_parser_test_output_path.mkdir(parents=True, exist_ok=True)

    collector = ProductCollector(settings=settings)

    with sync_playwright() as playwright:
        product = collect_one_product(collector, playwright)
        detail = fetch_product_detail_snapshot(collector, playwright, product["url"])
        parsed = parse_product(
            title=detail["title"] or product["name"],
            image=detail["imageUrl"] or product.get("imageUrl") or None,
            specs=detail["specs"],
            price=detail["price"] or product.get("price"),
            settings=settings,
        )

    result = {
        "tested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "keyword": settings.ozon_scrape_keyword,
        "product_input": {
            "sku": product.get("sku"),
            "title_from_search_card": product.get("name"),
            "url": product.get("url"),
            "image_from_search_card": product.get("imageUrl"),
            "price_from_search_card": product.get("price"),
            "category_from_plugin": product.get("category"),
            "brand_from_plugin": product.get("brand"),
            "title_from_detail_page": detail["title"],
            "image_from_detail_page": detail["imageUrl"],
            "price_from_detail_page": detail["price"],
            "specs": detail["specs"],
            "plugin_metrics": {
                "monthlySales": product.get("monthlySales"),
                "growthRate": product.get("growthRate"),
                "returnRate": product.get("returnRate"),
                "weight": product.get("weight"),
                "shippingMode": product.get("shippingMode"),
                "sellers": product.get("sellers"),
            },
        },
        "openai_output": parsed,
    }

    output_path = (
        settings.product_parser_test_output_path
        / f"ozon_openai_parse_{product.get('sku', 'unknown')}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Selected product: {detail['title'] or product['name']}")
    print(f"Product URL: {product['url']}")
    print(f"Specs count: {len(detail['specs'])}")
    print("OpenAI parsed result:")
    print(json.dumps(parsed, ensure_ascii=False, indent=2))
    print(f"Saved result to: {output_path}")


def collect_one_product(collector: ProductCollector, playwright: Playwright) -> dict[str, Any]:
    """从 Ozon 搜索结果页中选择一个适合测试的商品。"""

    search_url = collector.build_search_url(collector.settings.ozon_scrape_keyword)
    context = launch_test_context(collector, playwright)
    page = context.new_page()

    try:
        page.goto(
            search_url,
            wait_until="domcontentloaded",
            timeout=collector.settings.playwright_timeout_ms,
        )
        page.wait_for_selector(".tile-root[data-index]", timeout=20_000)
        page.wait_for_timeout(2_000)
        collector.ensure_plugin_ready(context, page, search_url)

        for _ in range(3):
            page.evaluate("window.scrollBy(0, 1200)")
            page.wait_for_timeout(1200)

        products = collector.extract_all(page)
        candidate = choose_candidate_product(products)
        if candidate is None:
            raise RuntimeError("未能在搜索结果页找到可用于解析测试的商品。")
        return candidate
    finally:
        context.close()


def choose_candidate_product(products: list[dict[str, Any]]) -> dict[str, Any] | None:
    """优先挑选带插件数据、图片和价格的商品。"""

    for product in products[:MAX_CARD_SCAN]:
        if (
            product.get("hasPlugin")
            and product.get("name")
            and product.get("url")
            and product.get("price")
            and product.get("imageUrl")
        ):
            return product
    return None


def fetch_product_detail_snapshot(
    collector: ProductCollector,
    playwright: Playwright,
    product_url: str,
) -> dict[str, Any]:
    """进入商品详情页，提取标题、价格、图片和规格参数。"""

    context = launch_test_context(collector, playwright)
    page = context.new_page()

    try:
        page.goto(
            product_url,
            wait_until="domcontentloaded",
            timeout=collector.settings.playwright_timeout_ms,
        )
        page.wait_for_timeout(4_000)
        try_expand_spec_sections(page)
        page.wait_for_timeout(1_500)
        page.wait_for_load_state("domcontentloaded", timeout=10_000)
        return {
            "title": extract_detail_title(page),
            "price": extract_detail_price(page),
            "imageUrl": extract_detail_image(page),
            "specs": extract_specs_from_page(page)[:MAX_SPECS],
        }
    finally:
        context.close()


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


def extract_specs_from_page(page: Page) -> list[dict[str, str]]:
    """使用较宽松的 DOM 规则抽取规格键值。"""

    specs: list[dict[str, str]] = page.evaluate(
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

          return results.slice(0, {MAX_SPECS});
        }}
        """
    )
    return specs


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


def launch_test_context(collector: ProductCollector, playwright: Playwright) -> BrowserContext:
    """基于已登录 profile 的副本启动测试浏览器。

    原始 profile 可能正被用户手动打开的浏览器占用，因此这里复制一份再启动，
    避免 Chromium 的 SingletonLock 冲突。
    """

    collector.login_manager.validate_extension_assets()
    source_profile = collector.settings.shopbang_user_data_path
    if not source_profile.exists():
        raise FileNotFoundError(f"未找到浏览器 profile: {source_profile}")

    copied_profile = prepare_profile_copy(collector.settings)

    launch_kwargs: dict[str, Any] = {
        "headless": False,
        "args": collector.login_manager._build_extension_args(),
        "locale": collector.settings.ozon_browser_locale,
        "timezone_id": collector.settings.ozon_browser_timezone,
        "viewport": {"width": 1440, "height": 900},
        "slow_mo": collector.settings.playwright_slow_mo_ms,
    }
    if collector.settings.ozon_user_agent:
        launch_kwargs["user_agent"] = collector.settings.ozon_user_agent
    if collector.settings.playwright_proxy_url:
        launch_kwargs["proxy"] = {"server": collector.settings.playwright_proxy_url}

    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(copied_profile),
        **launch_kwargs,
    )


def prepare_profile_copy(settings) -> Path:
    """创建当前浏览器 profile 的可复用副本。"""

    target_dir = settings.project_root / "browser-profile-e2e"
    if target_dir.exists():
        shutil.rmtree(target_dir)

    shutil.copytree(
        settings.shopbang_user_data_path,
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


if __name__ == "__main__":
    main()
