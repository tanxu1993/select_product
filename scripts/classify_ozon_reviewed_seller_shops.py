"""读取 SQLite 店铺并按杂货铺/垂直店写回分类结果。"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings
from ozon_selection.repositories.ozon_reviewed_seller_repository import OzonReviewedSellerRepository
from ozon_selection.services.ozon_candidate_pipeline import OzonCandidatePipeline


SHOP_TYPE_MISC = "杂货铺"
SHOP_TYPE_VERTICAL = "垂直店"
TITLE_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+")
PROMO_TITLES = {
    "novinka",
    "новинка",
    "цена что надо",
    "вау-цены",
    "вау цены",
}
TITLE_STOPWORDS = {
    "для",
    "это",
    "что",
    "надо",
    "без",
    "при",
    "под",
    "над",
    "the",
    "and",
    "with",
    "шт",
    "штx",
    "см",
    "мм",
    "мл",
    "гр",
    "кг",
}


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="读取 SQLite 店铺并按杂货铺/垂直店写回分类结果。")
    parser.add_argument(
        "--max-shops",
        type=int,
        default=0,
        help="最多处理多少家店铺，0 表示处理全部。",
    )
    parser.add_argument(
        "--recheck-all",
        action="store_true",
        help="默认只处理未分类店铺；传入后会重跑全部店铺。",
    )
    parser.add_argument(
        "--sample-target",
        type=int,
        default=36,
        help="每个店铺最多抽样多少个商品用于分类判断。",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="后台模式运行浏览器。",
    )
    parser.add_argument(
        "--worker-count",
        type=int,
        default=1,
        help="把待分类店铺分成多少个分片并行处理，默认 1。",
    )
    parser.add_argument(
        "--worker-index",
        type=int,
        default=1,
        help="当前进程处理第几个分片，取值范围 1..worker-count，默认 1。",
    )
    return parser.parse_args()


def normalize_seller_url(url: str) -> str:
    """归一化店铺 URL。"""

    normalized = str(url or "").strip()
    return normalized.rstrip("/") + "/" if normalized else ""


def normalize_text(value: Any) -> str:
    """清洗文本。"""

    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def extract_primary_category(value: Any) -> str:
    """提取一级类目。"""

    normalized = normalize_text(value)
    if not normalized:
        return ""
    for separator in ("›", ">", "/", "|", "—", "–"):
        if separator in normalized:
            head = normalize_text(normalized.split(separator, 1)[0])
            if head:
                return head
    return normalized


def normalize_title(value: Any) -> str:
    """清洗商品标题。"""

    title = normalize_text(value)
    if not title:
        return ""
    lowered = title.lower()
    if lowered in PROMO_TITLES:
        return ""
    if "осталась" in lowered and "распродажа" in lowered:
        return ""
    return title


def extract_title_tokens(value: Any) -> list[str]:
    """提取标题中的有效 token。"""

    title = normalize_title(value).lower()
    if not title:
        return []

    tokens: list[str] = []
    for raw_token in TITLE_TOKEN_RE.findall(title):
        token = raw_token.strip().lower()
        if not token or len(token) < 3:
            continue
        if token.isdigit():
            continue
        if token in TITLE_STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def build_title_profile(products: list[dict[str, Any]]) -> dict[str, Any]:
    """基于商品标题估算店铺主题分散度。"""

    token_counter: Counter[str] = Counter()
    prefix_counter: Counter[str] = Counter()
    valid_title_count = 0

    for item in products:
        tokens = extract_title_tokens(item.get("name"))
        if not tokens:
            continue

        valid_title_count += 1
        token_counter.update(set(tokens))

        prefix = " ".join(tokens[:2]) if len(tokens) >= 2 else tokens[0]
        if prefix:
            prefix_counter[prefix] += 1

    top_token = token_counter.most_common(1)[0] if token_counter else ("", 0)
    top_prefix = prefix_counter.most_common(1)[0] if prefix_counter else ("", 0)
    top_token_share = (top_token[1] / valid_title_count) if valid_title_count > 0 else 0
    top_prefix_share = (top_prefix[1] / valid_title_count) if valid_title_count > 0 else 0

    return {
        "valid_title_count": valid_title_count,
        "unique_token_count": len(token_counter),
        "unique_prefix_count": len(prefix_counter),
        "top_token": {"name": top_token[0], "count": top_token[1], "share": round(top_token_share, 4)},
        "top_prefix": {"name": top_prefix[0], "count": top_prefix[1], "share": round(top_prefix_share, 4)},
        "top_tokens": dict(token_counter.most_common(10)),
        "top_prefixes": dict(prefix_counter.most_common(10)),
    }


def classify_shop(products: list[dict[str, Any]]) -> dict[str, Any]:
    """根据商品样本把店铺判定为杂货铺或垂直店。"""

    primary_categories = [extract_primary_category(item.get("category")) for item in products]
    primary_categories = [item for item in primary_categories if item]
    brands = [normalize_text(item.get("brand")) for item in products]
    brands = [item for item in brands if item]

    category_counter = Counter(primary_categories)
    brand_counter = Counter(brands)
    sample_size = len(products)
    category_count = len(category_counter)
    brand_count = len(brand_counter)
    top_category = category_counter.most_common(1)[0] if category_counter else ("", 0)
    top_brand = brand_counter.most_common(1)[0] if brand_counter else ("", 0)
    top_category_share = (top_category[1] / sample_size) if sample_size > 0 else 0
    top_brand_share = (top_brand[1] / sample_size) if sample_size > 0 else 0
    title_profile = build_title_profile(products)
    valid_title_count = int(title_profile["valid_title_count"] or 0)
    unique_token_count = int(title_profile["unique_token_count"] or 0)
    unique_prefix_count = int(title_profile["unique_prefix_count"] or 0)
    top_title_token_share = float((title_profile.get("top_token") or {}).get("share") or 0)
    top_title_prefix_share = float((title_profile.get("top_prefix") or {}).get("share") or 0)

    original_misc = (
        sample_size >= 12
        and category_count >= 4
        and top_category_share <= 0.65
        and (brand_count >= 4 or top_brand_share <= 0.7)
    )

    category_mixed_score = 0
    if category_count >= 4:
        category_mixed_score += 2
    elif category_count == 3:
        category_mixed_score += 1
    if category_count >= 1 and top_category_share <= 0.35:
        category_mixed_score += 1
    if brand_count >= 4 or (brand_count >= 1 and top_brand_share <= 0.7):
        category_mixed_score += 1

    title_mixed_score = 0
    if valid_title_count >= 12:
        title_mixed_score += 1
    if unique_token_count >= 16:
        title_mixed_score += 1
    if top_title_token_share <= 0.35:
        title_mixed_score += 1
    if unique_prefix_count >= 8:
        title_mixed_score += 1
    if top_title_prefix_share <= 0.35:
        title_mixed_score += 1

    title_only_misc = sample_size >= 12 and title_mixed_score >= 5
    mixed_signal_misc = sample_size >= 12 and category_mixed_score >= 2 and title_mixed_score >= 3
    is_misc = original_misc or title_only_misc or mixed_signal_misc

    if is_misc:
        shop_type = SHOP_TYPE_MISC
        if original_misc:
            reason = (
                f"mixed_categories:sample={sample_size},category_count={category_count},"
                f"top_category_share={top_category_share:.2f},brand_count={brand_count},top_brand_share={top_brand_share:.2f}"
            )
        elif title_only_misc:
            reason = (
                f"mixed_titles:sample={sample_size},valid_titles={valid_title_count},"
                f"unique_title_tokens={unique_token_count},unique_title_prefixes={unique_prefix_count},"
                f"top_title_token_share={top_title_token_share:.2f},top_title_prefix_share={top_title_prefix_share:.2f}"
            )
        else:
            reason = (
                f"mixed_category_and_title:sample={sample_size},category_count={category_count},"
                f"top_category_share={top_category_share:.2f},valid_titles={valid_title_count},"
                f"top_title_token_share={top_title_token_share:.2f},top_title_prefix_share={top_title_prefix_share:.2f}"
            )
    else:
        shop_type = SHOP_TYPE_VERTICAL
        reason = (
            f"vertical_by_concentration:sample={sample_size},category_count={category_count},"
            f"top_category_share={top_category_share:.2f},brand_count={brand_count},top_brand_share={top_brand_share:.2f},"
            f"valid_titles={valid_title_count},top_title_token_share={top_title_token_share:.2f},"
            f"top_title_prefix_share={top_title_prefix_share:.2f}"
        )

    return {
        "shop_type": shop_type,
        "reason": reason,
        "sample_size": sample_size,
        "primary_category_count": category_count,
        "brand_count": brand_count,
        "profile": {
            "primary_categories": dict(category_counter.most_common(10)),
            "brands": dict(brand_counter.most_common(10)),
            "top_category": {"name": top_category[0], "count": top_category[1], "share": round(top_category_share, 4)},
            "top_brand": {"name": top_brand[0], "count": top_brand[1], "share": round(top_brand_share, 4)},
            "title_profile": title_profile,
            "category_mixed_score": category_mixed_score,
            "title_mixed_score": title_mixed_score,
        },
    }


def open_shop_page(page: Page, url: str, timeout_ms: int) -> None:
    """打开店铺页。"""

    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(2_500)


def load_target_shops(
    repository: OzonReviewedSellerRepository,
    *,
    recheck_all: bool,
    worker_count: int = 1,
    worker_index: int = 1,
) -> list[dict[str, Any]]:
    """读取待分类店铺。"""

    all_shops = repository.list_shops(crawl_status="all", shop_type="all")
    if recheck_all:
        shops = [shop for shop in all_shops if normalize_seller_url(shop.get("seller_url") or "")]
    else:
        pending_urls = repository.list_pending_shop_type_urls()
        shops = [
            shop
            for shop in all_shops
            if normalize_seller_url(shop.get("seller_url") or "") in pending_urls
        ]

    normalized_worker_count = max(int(worker_count or 1), 1)
    normalized_worker_index = max(int(worker_index or 1), 1)
    if normalized_worker_index > normalized_worker_count:
        raise ValueError("worker_index 不能大于 worker_count。")
    if normalized_worker_count == 1:
        return shops

    filtered: list[dict[str, Any]] = []
    for position, shop in enumerate(shops):
        if position % normalized_worker_count == normalized_worker_index - 1:
            filtered.append(shop)
    return filtered


def main() -> None:
    """脚本入口。"""

    args = parse_args()
    settings = get_settings().model_copy(
        deep=True,
        update={"shopbang_headless": bool(args.background)},
    )
    repository = OzonReviewedSellerRepository(settings=settings)
    collector = OzonCandidatePipeline(settings=settings).collector

    shops = load_target_shops(
        repository,
        recheck_all=bool(args.recheck_all),
        worker_count=int(args.worker_count or 1),
        worker_index=int(args.worker_index or 1),
    )
    if args.max_shops and int(args.max_shops) > 0:
        shops = shops[: int(args.max_shops)]

    if not shops:
        print("Ozon seller shop type classification: skipped")
        print("shop_count: 0")
        print(f"worker_count: {max(int(args.worker_count or 1), 1)}")
        print(f"worker_index: {max(int(args.worker_index or 1), 1)}")
        print(f"sqlite_db_path: {settings.sqlite_db_path}")
        return

    with sync_playwright() as playwright:
        collector.login_manager.validate_collection_prerequisites()
        session = collector.login_manager.open_browser_session(playwright=playwright)
        context = session.context
        page = context.new_page()

        try:
            misc_count = 0
            vertical_count = 0
            failure_count = 0

            for index, shop in enumerate(shops, start=1):
                seller_url = normalize_seller_url(shop.get("seller_url") or "")
                seller_name = normalize_text(shop.get("seller_name") or "")
                print(
                    f"[shop-type] {index}/{len(shops)} start seller_name={seller_name or '-'} seller_url={seller_url}",
                    flush=True,
                )
                try:
                    open_shop_page(page, seller_url, settings.playwright_timeout_ms)
                    collector.wait_search_results_ready(page)
                    collector.ensure_plugin_ready(context, page, page.url)
                    collector.scroll_to_load(page, min(max(int(args.sample_target or 0), 12), 60))
                    products = collector.extract_all(page)
                    if int(args.sample_target or 0) > 0:
                        products = products[: int(args.sample_target)]
                    result = classify_shop(products)
                    repository.mark_shop_type(
                        seller_url=seller_url,
                        shop_type=result["shop_type"],
                        reason=result["reason"],
                        sample_size=result["sample_size"],
                        primary_category_count=result["primary_category_count"],
                        brand_count=result["brand_count"],
                        profile=result["profile"],
                    )
                    if result["shop_type"] == SHOP_TYPE_MISC:
                        misc_count += 1
                    else:
                        vertical_count += 1
                    print(
                        f"[shop-type] {index}/{len(shops)} completed "
                        f"seller_name={seller_name or '-'} shop_type={result['shop_type']} "
                        f"sample={result['sample_size']} primary_categories={result['primary_category_count']} "
                        f"brands={result['brand_count']}",
                        flush=True,
                    )
                except Exception as exc:
                    failure_count += 1
                    print(
                        f"[shop-type] {index}/{len(shops)} failed seller_name={seller_name or '-'} "
                        f"seller_url={seller_url} error={exc}",
                        flush=True,
                    )

            print("Ozon seller shop type classification: completed")
            print(f"shop_count: {len(shops)}")
            print(f"worker_count: {max(int(args.worker_count or 1), 1)}")
            print(f"worker_index: {max(int(args.worker_index or 1), 1)}")
            print(f"misc_count: {misc_count}")
            print(f"vertical_count: {vertical_count}")
            print(f"failure_count: {failure_count}")
            print(f"sqlite_db_path: {settings.sqlite_db_path}")
        finally:
            try:
                if not page.is_closed():
                    page.close()
            except Exception:
                pass
            session.close()


if __name__ == "__main__":
    main()
