"""抓取 Ozon 首页目录中的一级/二级类目并导出到 XLSX。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

import pandas as pd
from playwright.sync_api import Browser
from playwright.sync_api import BrowserContext
from playwright.sync_api import Page
from playwright.sync_api import Playwright
from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import Settings
from config.settings import get_settings
from ozon_selection.api.clients.openai_client import OpenAIClient
from ozon_selection.collectors.ozon.shopbang_auth import ShopbangLoginManager


TRANSLATION_CACHE_FILE = "ozon_home_category_translation_cache.json"


@dataclass(slots=True)
class LocalBrowserSession:
    """封装本脚本使用的浏览器会话。"""

    context: BrowserContext
    browser: Browser | None = None
    close_browser: bool = False

    def close(self) -> None:
        """释放上下文和浏览器。"""

        try:
            self.context.close()
        finally:
            if self.close_browser and self.browser is not None:
                self.browser.close()


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    settings = get_settings()
    parser = argparse.ArgumentParser(description="抓取 Ozon 首页目录中的一级/二级类目并导出到 XLSX。")
    parser.add_argument(
        "--start-url",
        type=str,
        default=settings.ozon_base_url,
        help="脚本打开的 Ozon 首页 URL。",
    )
    parser.add_argument(
        "--output-xlsx",
        type=str,
        default="",
        help="可选输出路径；不传则默认写入 data/exports/。",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="后台模式运行浏览器。不建议开启，因为可能需要人工处理风控页。",
    )
    parser.add_argument(
        "--skip-translation",
        action="store_true",
        help="跳过 OpenAI 翻译，中文列直接回退为俄文原文。",
    )
    return parser.parse_args()


def normalize_ozon_url(url: str, base_url: str) -> str:
    """归一化 Ozon URL，去掉 query 和 fragment。"""

    if not url:
        return ""
    absolute = url if url.startswith("http") else f"{base_url.rstrip('/')}/{url.lstrip('/')}"
    parsed = urlsplit(absolute)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def get_translation_cache_path(settings: Settings) -> Path:
    """返回类目翻译缓存文件路径。"""

    return settings.processed_data_path / TRANSLATION_CACHE_FILE


def load_translation_cache(settings: Settings) -> dict[str, str]:
    """读取本地翻译缓存。"""

    cache_path = get_translation_cache_path(settings)
    if not cache_path.exists():
        return {}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        str(key).strip(): str(value).strip()
        for key, value in payload.items()
        if str(key).strip() and str(value).strip()
    }


def save_translation_cache(settings: Settings, cache: dict[str, str]) -> None:
    """保存翻译缓存。"""

    cache_path = get_translation_cache_path(settings)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def looks_like_chinese(text: str) -> bool:
    """判断文本是否已包含中文。"""

    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def chunk_items(items: list[str], size: int) -> list[list[str]]:
    """把列表切成固定大小的块。"""

    return [items[index:index + size] for index in range(0, len(items), size)]


def extract_json_object(text: str) -> dict[str, str]:
    """从模型输出里提取 JSON 对象。"""

    normalized = str(text or "").strip()
    if not normalized:
        return {}
    try:
        payload = json.loads(normalized)
        if isinstance(payload, dict):
            return {str(key): str(value) for key, value in payload.items()}
    except Exception:
        pass

    matched = re.search(r"\{.*\}", normalized, re.S)
    if not matched:
        return {}
    try:
        payload = json.loads(matched.group(0))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items()}


def translate_category_batch(settings: Settings, texts: list[str]) -> dict[str, str]:
    """调用 OpenAI 批量翻译类目。"""

    if not texts:
        return {}
    if not settings.openai_api_key.strip():
        return {text: text for text in texts}

    client = OpenAIClient(settings=settings)
    system_prompt = (
        "你是跨境电商类目翻译助手。"
        "把输入的俄文商品类目短语翻译成简洁准确的中文。"
        "只返回 JSON 对象，不要输出解释，不要输出 Markdown。"
    )
    user_prompt = (
        "请把下面这些 Ozon 商品类目翻译成中文，返回格式必须是 JSON 对象，"
        "键为原文，值为中文：\n"
        f"{json.dumps(texts, ensure_ascii=False)}"
    )
    try:
        response = client.stream_chat_completion(
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=2000,
        )
        payload = extract_json_object(response.output_text)
    except Exception:
        payload = {}

    result: dict[str, str] = {}
    for text in texts:
        translated = str(payload.get(text) or "").strip()
        result[text] = translated or text
    return result


def build_translation_map(
    *,
    settings: Settings,
    texts: list[str],
    skip_translation: bool,
) -> dict[str, str]:
    """构建类目中文翻译映射。"""

    normalized_texts = []
    seen: set[str] = set()
    for text in texts:
        normalized = str(text or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_texts.append(normalized)

    cache = load_translation_cache(settings)
    if skip_translation:
        for text in normalized_texts:
            cache.setdefault(text, text)
        return cache

    missing_texts = [
        text
        for text in normalized_texts
        if not str(cache.get(text) or "").strip() and not looks_like_chinese(text)
    ]
    if missing_texts:
        print(f"[translation] missing_count={len(missing_texts)}", flush=True)
        for batch in chunk_items(missing_texts, size=40):
            cache.update(translate_category_batch(settings, batch))
        save_translation_cache(settings, cache)
    return cache


def get_translated_text(text: str, translation_map: dict[str, str]) -> str:
    """读取中文翻译。"""

    normalized = str(text or "").strip()
    if not normalized:
        return ""
    translated = str(translation_map.get(normalized) or "").strip()
    return translated or normalized


def build_output_path(settings: Settings, output_xlsx: str) -> Path:
    """构建输出 XLSX 路径。"""

    if output_xlsx.strip():
        return Path(output_xlsx).expanduser().resolve()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return settings.ozon_scrape_output_path / f"ozon_home_categories_{timestamp}.xlsx"


def open_browser_session(playwright: Playwright, settings: Settings) -> LocalBrowserSession:
    """优先复用现有持久化 profile；如果不可用则回退为普通浏览器。"""

    login_manager = ShopbangLoginManager(settings)
    try:
        login_manager.validate_collection_prerequisites()
        session = login_manager.open_browser_session(playwright=playwright)
        print("[browser] using persistent profile", flush=True)
        return LocalBrowserSession(context=session.context, browser=session.browser, close_browser=False)
    except Exception as exc:
        print(f"[browser] fallback to plain browser: {exc}", flush=True)

    browser_type = getattr(playwright, settings.playwright_browser)
    browser = browser_type.launch(
        headless=settings.shopbang_headless,
        channel=settings.playwright_channel or None,
        executable_path=settings.playwright_executable_path or None,
        slow_mo=settings.playwright_slow_mo_ms,
        proxy={"server": settings.playwright_proxy_url} if settings.playwright_proxy_url else None,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        locale=settings.ozon_browser_locale,
        timezone_id=settings.ozon_browser_timezone,
        viewport={"width": 1440, "height": 900},
        user_agent=settings.ozon_user_agent or None,
    )
    return LocalBrowserSession(context=context, browser=browser, close_browser=True)


def is_access_limited(page: Page) -> bool:
    """判断当前是否命中 Ozon 风控页。"""

    title = ""
    try:
        title = page.title()
    except Exception:
        title = ""
    normalized_title = str(title or "").strip().lower()
    return "доступ ограничен" in normalized_title or "__rr=1" in (page.url or "")


def wait_for_manual_recovery(page: Page, *, background: bool) -> None:
    """允许用户手动处理风控页或弹窗。"""

    if background:
        raise RuntimeError("当前页面疑似被 Ozon 风控拦截，后台模式无法人工恢复，请去掉 --background 后重试。")

    print("检测到页面需要人工处理。请在浏览器里完成以下操作后回到终端：", flush=True)
    print("1. 处理 Ozon 风控/验证页（如果有）", flush=True)
    print("2. 确认首页已经正常显示，并且能看到“Каталог”按钮", flush=True)
    input("处理完成后按 Enter 继续...")


def goto_homepage(page: Page, start_url: str, settings: Settings, *, background: bool) -> None:
    """打开 Ozon 首页并处理可能的人工恢复。"""

    page.goto(start_url, wait_until="domcontentloaded", timeout=settings.playwright_timeout_ms)
    page.wait_for_timeout(4_000)
    if is_access_limited(page):
        wait_for_manual_recovery(page, background=background)
        page.wait_for_timeout(2_000)


def count_visible_category_links(
    page: Page,
    *,
    min_x: int | None = None,
    max_x: int | None = None,
) -> int:
    """统计当前可见的类目链接数量。"""

    return int(
        page.evaluate(
            """({minX, maxX}) => {
            return Array.from(document.querySelectorAll('a[href*="/category/"]'))
              .map((el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                return {
                  text,
                  x: rect.x,
                  width: rect.width,
                  height: rect.height,
                  hidden: style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') <= 0,
                };
              })
              .filter((item) => {
                if (!item.text || item.hidden || item.width <= 1 || item.height <= 1) return false;
                if (minX !== null && item.x < minX) return false;
                if (maxX !== null && item.x >= maxX) return false;
                return true;
              }).length;
          }""",
            {"minX": min_x, "maxX": max_x},
        )
    )


def wait_for_category_links_stable(
    page: Page,
    *,
    min_count: int,
    min_x: int | None = None,
    max_x: int | None = None,
    timeout_ms: int = 15_000,
    stable_rounds: int = 3,
    poll_ms: int = 400,
) -> int:
    """等待类目链接数量稳定，避免页面未加载完成就开始采集。"""

    deadline = time.time() + timeout_ms / 1000
    last_count = -1
    stable_count = 0
    observed_max = 0

    while time.time() < deadline:
        current_count = count_visible_category_links(page, min_x=min_x, max_x=max_x)
        observed_max = max(observed_max, current_count)
        if current_count >= min_count:
            if current_count == last_count:
                stable_count += 1
            else:
                stable_count = 1
            if stable_count >= stable_rounds:
                return current_count
        else:
            stable_count = 0
        last_count = current_count
        page.wait_for_timeout(poll_ms)

    raise RuntimeError(
        f"等待类目区域加载超时，期望至少 {min_count} 个链接，实际最高只看到 {observed_max} 个。"
    )


def click_expand_buttons(page: Page, *, max_rounds: int = 6) -> int:
    """点击目录中的“更多/展开”按钮，展开被折叠的类目。"""

    total_clicked = 0
    for _ in range(max_rounds):
        clicked = int(
            page.evaluate(
                """() => {
                const normalize = (text) => (text || '').replace(/\\s+/g, ' ').trim();
                const pattern = /(ещ[её]|показать\\s+ещ[её]|развернуть|показать\\s+все|все\\s+категории|все\\s+подкатегории|show\\s+more|more)/i;
                const candidates = Array.from(document.querySelectorAll('button, [role="button"], summary'))
                  .filter((el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    const text = normalize(el.innerText || el.textContent || '');
                    return (
                      text &&
                      pattern.test(text) &&
                      rect.width > 1 &&
                      rect.height > 1 &&
                      rect.x >= 320 &&
                      rect.y >= 120 &&
                      style.display !== 'none' &&
                      style.visibility !== 'hidden' &&
                      Number(style.opacity || '1') > 0
                    );
                  });

                let count = 0;
                for (const element of candidates) {
                  try {
                    element.scrollIntoView({block: 'center'});
                    element.click();
                    count += 1;
                  } catch (error) {
                  }
                }
                return count;
              }"""
            )
        )
        if clicked <= 0:
            break
        total_clicked += clicked
        page.wait_for_timeout(900)
    return total_clicked


def build_category_signature(rows: list[dict[str, str]]) -> str:
    """构建类目列表签名，用于判断 hover 后右侧内容是否刷新。"""

    parts: list[str] = []
    for row in rows[:20]:
        name = str(row.get("name_ru") or "").strip()
        url = str(row.get("url") or "").strip()
        if name or url:
            parts.append(f"{name}||{url}")
    return "|".join(parts)


def wait_for_second_level_refresh(
    page: Page,
    settings: Settings,
    *,
    previous_signature: str = "",
    timeout_ms: int = 12_000,
) -> list[dict[str, str]]:
    """等待右侧二级类目区域刷新并稳定。"""

    deadline = time.time() + timeout_ms / 1000
    last_signature = ""
    stable_count = 0
    last_rows: list[dict[str, str]] = []

    while time.time() < deadline:
        rows = collect_second_level_categories(page, settings)
        signature = build_category_signature(rows)
        if rows:
            last_rows = rows
        if rows and signature and signature != previous_signature:
            if signature == last_signature:
                stable_count += 1
            else:
                stable_count = 1
            if stable_count >= 2:
                return rows
        else:
            stable_count = 0
        last_signature = signature
        page.wait_for_timeout(450)

    if last_rows:
        return last_rows
    raise RuntimeError("等待二级类目区域刷新超时，未拿到稳定结果。")


def open_catalog(page: Page) -> None:
    """点击首页“Каталог”按钮。"""

    candidates = [
        page.get_by_role("button", name=re.compile(r"^Каталог$", re.I)),
        page.locator("button").filter(has_text=re.compile(r"^Каталог$", re.I)),
        page.locator("[role=button]").filter(has_text=re.compile(r"^Каталог$", re.I)),
        page.locator("text=Каталог"),
    ]
    for locator in candidates:
        try:
            if locator.count() <= 0:
                continue
            locator.first.click(timeout=10_000)
            wait_for_category_links_stable(page, min_count=8, max_x=320)
            return
        except Exception:
            continue
    raise RuntimeError("未找到首页“Каталог”按钮。")


def collect_visible_top_categories(page: Page, settings: Settings) -> list[dict[str, str]]:
    """采集当前可视区域中的左侧一级类目。"""

    rows = page.evaluate(
        """(baseUrl) => {
        const normalize = (href) => {
          if (!href) return '';
          const absolute = new URL(href, baseUrl).toString();
          const parsed = new URL(absolute);
          return `${parsed.origin}${parsed.pathname}`;
        };

        const items = Array.from(document.querySelectorAll('a[href*="/category/"]'))
          .map((el) => {
            const rect = el.getBoundingClientRect();
            const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
            return {
              text,
              url: normalize(el.href || ''),
              x: rect.x,
              y: rect.y,
              width: rect.width,
              height: rect.height,
            };
          })
          .filter((item) => item.text && item.url && item.x < 320 && item.y >= 140 && item.width >= 200)
          .sort((a, b) => a.y - b.y);

        const deduped = [];
        const seen = new Set();
        for (const item of items) {
          const key = `${item.text}||${item.url}`;
          if (seen.has(key)) continue;
          seen.add(key);
          deduped.push({name_ru: item.text, url: item.url});
        }
        return deduped;
      }""",
        settings.ozon_base_url,
    )
    return [dict(row) for row in rows]


def reset_top_category_panel(page: Page) -> bool:
    """把左侧一级类目滚动容器重置到顶部。"""

    return bool(
        page.evaluate(
            """() => {
            const links = Array.from(document.querySelectorAll('a[href*="/category/"]'))
              .filter((el) => {
                const rect = el.getBoundingClientRect();
                const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                return text && rect.x < 320;
              });

            const findScrollable = (element) => {
              let current = element?.parentElement || null;
              while (current) {
                const rect = current.getBoundingClientRect();
                const style = window.getComputedStyle(current);
                const overflowY = style.overflowY || '';
                const canScroll = current.scrollHeight - current.clientHeight > 40;
                const looksLikeSidebar = rect.x < 360 && rect.width <= 420 && rect.height >= 180;
                if (canScroll && looksLikeSidebar && /(auto|scroll|overlay)/i.test(overflowY)) {
                  return current;
                }
                current = current.parentElement;
              }
              return null;
            };

            for (const link of links) {
              const panel = findScrollable(link);
              if (!panel) continue;
              panel.scrollTop = 0;
              return true;
            }
            return false;
          }"""
        )
    )


def scroll_top_category_panel(page: Page) -> bool:
    """把左侧一级类目滚动容器向下滚动一屏。"""

    return bool(
        page.evaluate(
            """() => {
            const links = Array.from(document.querySelectorAll('a[href*="/category/"]'))
              .filter((el) => {
                const rect = el.getBoundingClientRect();
                const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                return text && rect.x < 320;
              });

            const findScrollable = (element) => {
              let current = element?.parentElement || null;
              while (current) {
                const rect = current.getBoundingClientRect();
                const style = window.getComputedStyle(current);
                const overflowY = style.overflowY || '';
                const canScroll = current.scrollHeight - current.clientHeight > 40;
                const looksLikeSidebar = rect.x < 360 && rect.width <= 420 && rect.height >= 180;
                if (canScroll && looksLikeSidebar && /(auto|scroll|overlay)/i.test(overflowY)) {
                  return current;
                }
                current = current.parentElement;
              }
              return null;
            };

            for (const link of links) {
              const panel = findScrollable(link);
              if (!panel) continue;
              const before = panel.scrollTop;
              const step = Math.max(Math.floor(panel.clientHeight * 0.85), 140);
              panel.scrollTop = Math.min(before + step, panel.scrollHeight);
              return panel.scrollTop > before;
            }
            return false;
          }"""
        )
    )


def collect_top_categories(page: Page, settings: Settings) -> list[dict[str, str]]:
    """滚动采集全部左侧一级类目。"""

    reset_top_category_panel(page)
    page.wait_for_timeout(400)

    collected: dict[str, dict[str, str]] = {}
    unchanged_rounds = 0

    for _ in range(40):
        visible_rows = collect_visible_top_categories(page, settings)
        before_count = len(collected)
        for row in visible_rows:
            name = str(row.get("name_ru") or "").strip()
            url = str(row.get("url") or "").strip()
            if not name or not url:
                continue
            collected.setdefault(f"{name}||{url}", {"name_ru": name, "url": url})

        if len(collected) == before_count:
            unchanged_rounds += 1
        else:
            unchanged_rounds = 0

        moved = scroll_top_category_panel(page)
        if not moved:
            break
        page.wait_for_timeout(500)
        try:
            wait_for_category_links_stable(page, min_count=1, max_x=320, timeout_ms=5_000, stable_rounds=2, poll_ms=300)
        except Exception:
            pass
        if unchanged_rounds >= 3:
            break

    reset_top_category_panel(page)
    page.wait_for_timeout(400)
    return list(collected.values())


def activate_top_category(page: Page, *, category_name: str, category_url: str) -> None:
    """通过 hover 激活左侧一级类目，而不是点击跳转。"""

    reset_top_category_panel(page)
    page.wait_for_timeout(250)

    activated = False
    for _ in range(40):
        activated = bool(
            page.evaluate(
                """({categoryName, categoryUrl}) => {
                const normalize = (href) => {
                  if (!href) return '';
                  const parsed = new URL(href, window.location.origin);
                  return `${parsed.origin}${parsed.pathname}`;
                };

                const target = Array.from(document.querySelectorAll('a[href*="/category/"]')).find((el) => {
                  const rect = el.getBoundingClientRect();
                  const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                  return text === categoryName && normalize(el.href || '') === categoryUrl && rect.x < 320;
                });
                if (!target) return false;

                target.scrollIntoView({block: 'center'});
                ['mouseover', 'mouseenter', 'mousemove'].forEach((eventName) => {
                  target.dispatchEvent(new MouseEvent(eventName, {bubbles: true, cancelable: true, view: window}));
                });
                return true;
              }""",
                {"categoryName": category_name, "categoryUrl": category_url},
            )
        )
        if activated:
            break
        moved = scroll_top_category_panel(page)
        if not moved:
            break
        page.wait_for_timeout(350)

    if not activated:
        raise RuntimeError(f"未能激活一级类目：{category_name}")
    page.wait_for_timeout(1_200)


def collect_second_level_categories(page: Page, settings: Settings) -> list[dict[str, str]]:
    """采集当前一级类目对应的二级类目标题。"""

    rows = page.evaluate(
        """(baseUrl) => {
        const normalize = (href) => {
          if (!href) return '';
          const absolute = new URL(href, baseUrl).toString();
          const parsed = new URL(absolute);
          return `${parsed.origin}${parsed.pathname}`;
        };

        const items = Array.from(document.querySelectorAll('a[href*="/category/"]'))
          .map((el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
            return {
              text,
              url: normalize(el.href || ''),
              x: rect.x,
              y: rect.y,
              width: rect.width,
              height: rect.height,
              fontWeight: Number.parseInt(style.fontWeight || '0', 10) || 0,
              fontSize: Number.parseFloat(style.fontSize || '0') || 0,
            };
          })
          .filter((item) => item.text && item.url && item.x >= 320 && item.y >= 140)
          .sort((a, b) => a.x - b.x || a.y - b.y);

        const deduped = [];
        const seen = new Set();
        for (const item of items) {
          const isHeading = item.fontWeight >= 600 || item.fontSize >= 16;
          if (!isHeading) continue;
          const key = `${item.text}||${item.url}`;
          if (seen.has(key)) continue;
          seen.add(key);
          deduped.push({name_ru: item.text, url: item.url});
        }
        return deduped;
      }""",
        settings.ozon_base_url,
    )
    return [dict(row) for row in rows]


def collect_catalog_rows(page: Page, settings: Settings) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """抓取所有一级类目及其对应二级类目。"""

    wait_for_category_links_stable(page, min_count=8, max_x=320)
    top_categories = collect_top_categories(page, settings)
    if not top_categories:
        raise RuntimeError("目录已打开，但未采集到一级类目。")

    rows: list[dict[str, str]] = []
    previous_second_level_signature = ""
    for index, top_category in enumerate(top_categories, start=1):
        top_name = str(top_category.get("name_ru") or "").strip()
        top_url = normalize_ozon_url(str(top_category.get("url") or "").strip(), settings.ozon_base_url)
        if not top_name or not top_url:
            continue

        print(f"[{index}/{len(top_categories)}] top_category={top_name}", flush=True)
        activate_top_category(page, category_name=top_name, category_url=top_url)
        try:
            second_levels = wait_for_second_level_refresh(
                page,
                settings,
                previous_signature=previous_second_level_signature,
            )
        except Exception as exc:
            print(
                f"[{index}/{len(top_categories)}] skip_second_level_refresh "
                f"top_category={top_name} error={exc}",
                flush=True,
            )
            previous_second_level_signature = ""
            rows.append(
                {
                    "top_name_ru": top_name,
                    "top_url": top_url,
                    "second_name_ru": "",
                    "second_url": "",
                }
            )
            continue
        expanded_count = click_expand_buttons(page)
        if expanded_count > 0:
            print(f"[{index}/{len(top_categories)}] expanded_count={expanded_count}", flush=True)
            wait_for_category_links_stable(page, min_count=1, min_x=320, timeout_ms=8_000, stable_rounds=2)
            second_levels = collect_second_level_categories(page, settings)
        previous_second_level_signature = build_category_signature(second_levels)
        if not second_levels:
            rows.append(
                {
                    "top_name_ru": top_name,
                    "top_url": top_url,
                    "second_name_ru": "",
                    "second_url": "",
                }
            )
            continue

        for second_level in second_levels:
            rows.append(
                {
                    "top_name_ru": top_name,
                    "top_url": top_url,
                    "second_name_ru": str(second_level.get("name_ru") or "").strip(),
                    "second_url": normalize_ozon_url(str(second_level.get("url") or "").strip(), settings.ozon_base_url),
                }
            )

    return top_categories, rows


def build_export_rows(
    *,
    top_categories: list[dict[str, str]],
    category_rows: list[dict[str, str]],
    translation_map: dict[str, str],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """构建导出行。"""

    top_rows = [
        {
            "一级类目俄文": str(item.get("name_ru") or "").strip(),
            "一级类目中文": get_translated_text(str(item.get("name_ru") or "").strip(), translation_map),
            "一级类目URL": str(item.get("url") or "").strip(),
        }
        for item in top_categories
    ]

    detail_rows = [
        {
            "一级类目俄文": str(item.get("top_name_ru") or "").strip(),
            "一级类目中文": get_translated_text(str(item.get("top_name_ru") or "").strip(), translation_map),
            "一级类目URL": str(item.get("top_url") or "").strip(),
            "二级类目俄文": str(item.get("second_name_ru") or "").strip(),
            "二级类目中文": get_translated_text(str(item.get("second_name_ru") or "").strip(), translation_map),
            "二级类目URL": str(item.get("second_url") or "").strip(),
        }
        for item in category_rows
    ]
    return top_rows, detail_rows


def write_excel(*, output_path: Path, top_rows: list[dict[str, str]], detail_rows: list[dict[str, str]]) -> Path:
    """写入 XLSX。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        pd.DataFrame(top_rows).to_excel(writer, sheet_name="一级类目", index=False)
        pd.DataFrame(detail_rows).to_excel(writer, sheet_name="一级二级类目", index=False)
    return output_path


def main() -> None:
    """脚本入口。"""

    args = parse_args()
    settings = get_settings().model_copy(
        deep=True,
        update={
            "shopbang_headless": bool(args.background),
        },
    )

    with sync_playwright() as playwright:
        session = open_browser_session(playwright, settings)
        page = session.context.new_page()
        try:
            goto_homepage(page, args.start_url, settings, background=bool(args.background))
            open_catalog(page)
            top_categories, category_rows = collect_catalog_rows(page, settings)

            texts = [
                *[str(item.get("name_ru") or "").strip() for item in top_categories],
                *[str(item.get("second_name_ru") or "").strip() for item in category_rows],
            ]
            translation_map = build_translation_map(
                settings=settings,
                texts=texts,
                skip_translation=bool(args.skip_translation),
            )
            top_rows, detail_rows = build_export_rows(
                top_categories=top_categories,
                category_rows=category_rows,
                translation_map=translation_map,
            )

            output_path = build_output_path(settings, args.output_xlsx)
            write_excel(output_path=output_path, top_rows=top_rows, detail_rows=detail_rows)

            print(f"top_category_count: {len(top_rows)}")
            print(f"second_category_count: {sum(1 for row in detail_rows if row.get('二级类目俄文'))}")
            print(f"row_count: {len(detail_rows)}")
            print(f"xlsx_path: {output_path}")
        finally:
            session.close()


if __name__ == "__main__":
    main()
