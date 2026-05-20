"""Ozon 候选商品采集流水线。"""

from __future__ import annotations

import json
import multiprocessing
import time
from queue import Empty
from pathlib import Path
from typing import Any

from config.settings import Settings, get_settings
from ozon_selection.collectors.ozon.product_collector import ProductCollector
from ozon_selection.repositories.candidate_repository import CandidateRepository
from ozon_selection.repositories.ozon_keyword_pool_repository import OzonKeywordPoolRepository
from ozon_selection.repositories.shopbang_history_keyword_repository import ShopbangHistoryKeywordRepository
from playwright.sync_api import BrowserContext, sync_playwright


def _run_keyword_worker(keyword: str, settings_payload: dict[str, Any], result_queue: Any) -> None:
    """子进程入口：执行单关键词采集并把结果写回队列。"""

    try:
        settings = OzonCandidatePipeline.restore_settings_from_payload(settings_payload)
        pipeline = OzonCandidatePipeline(settings=settings)
        result = pipeline.run(keyword)
        result_queue.put({"status": "completed", "result": result})
    except Exception as exc:
        result_queue.put({"status": "failed", "error": str(exc)})


class OzonCandidatePipeline:
    """负责执行 Ozon 候选商品采集、筛选和落库。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        collector: ProductCollector | None = None,
        repository: CandidateRepository | None = None,
        keyword_pool_repository: OzonKeywordPoolRepository | None = None,
        shopbang_history_keyword_repository: ShopbangHistoryKeywordRepository | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.collector = collector or ProductCollector(settings=self.settings)
        self.repository = repository or CandidateRepository(settings=self.settings)
        self.keyword_pool_repository = keyword_pool_repository or OzonKeywordPoolRepository(settings=self.settings)
        self.shopbang_history_keyword_repository = (
            shopbang_history_keyword_repository or ShopbangHistoryKeywordRepository(settings=self.settings)
        )

    def run(self, keyword: str | None = None) -> dict[str, Any]:
        """执行完整 Ozon 采集流程。"""

        scrape_keyword = keyword or self.settings.ozon_scrape_keyword
        search_url = self.collector.build_search_url(scrape_keyword)
        products = self.collector.scrape_products(search_url)
        return self.finalize_collected_products(
            keyword=scrape_keyword,
            search_url=search_url,
            products=products,
        )

    def finalize_collected_products(
        self,
        *,
        keyword: str,
        search_url: str,
        products: list[dict[str, Any]],
        detail_context: BrowserContext | None = None,
    ) -> dict[str, Any]:
        """对已抓到的商品执行筛选、导出和落库。"""

        evaluated_products = self.evaluate_products(products)
        qualified_products = self.filter_qualified_products(evaluated_products)
        if self.settings.ozon_scrape_download_images and qualified_products:
            self.collector.save_product_images(qualified_products)
        if detail_context is None:
            qualified_products = self.collector.enrich_products_with_attributes(qualified_products)
        else:
            qualified_products = self.collector.enrich_products_with_attributes(
                qualified_products,
                context=detail_context,
            )
        self.merge_enriched_products_into_evaluated(
            evaluated_products=evaluated_products,
            qualified_products=qualified_products,
        )
        excel_rows = self.collector.build_result_rows(evaluated_products)
        excel_path = self.collector.export_to_excel(excel_rows, keyword)
        sqlite_result = self.save_products_to_sqlite(
            keyword=keyword,
            search_url=search_url,
            products=qualified_products,
        )
        database_result = self.save_to_database(qualified_products)

        return {
            "keyword": keyword,
            "search_url": search_url,
            "total_collected": len(products),
            "rejected_count": len(evaluated_products) - len(qualified_products),
            "qualified_count": len(qualified_products),
            "image_dir": str(self.settings.ozon_scrape_image_path),
            "excel_path": str(excel_path),
            "sqlite_result": sqlite_result,
            "database_result": database_result,
            "products": qualified_products,
        }

    @staticmethod
    def restore_settings_from_payload(settings_payload: dict[str, Any]) -> Settings:
        """恢复跨进程设置，保证运行时覆盖值优先于 `.env` 默认值。"""

        return get_settings().model_copy(deep=True, update=dict(settings_payload or {}))

    def run_for_keywords(
        self,
        keywords: list[str] | None = None,
        *,
        ensure_login: bool = True,
        pool_count: int = 5,
    ) -> dict[str, Any]:
        """按关键词列表顺序执行采集流程。"""

        checkpoint_path = self.get_keyword_checkpoint_path()
        normalized_keywords = [keyword.strip() for keyword in (keywords or []) if keyword and keyword.strip()]
        keyword_source = "manual" if normalized_keywords else "sqlite_pool"

        if normalized_keywords:
            scrape_keywords = normalized_keywords
            checkpoint = self.load_keyword_checkpoint()
            completed_keywords = set(checkpoint.get("completed_keywords") or [])
            pending_keywords = [keyword for keyword in scrape_keywords if keyword not in completed_keywords]
            skipped_keywords = [keyword for keyword in scrape_keywords if keyword in completed_keywords]
        else:
            scrape_keywords = self.keyword_pool_repository.pick_random_unused_keywords(limit=pool_count)
            checkpoint = {"completed_keywords": []}
            pending_keywords = list(scrape_keywords)
            skipped_keywords = []

        if not scrape_keywords:
            return {
                "keywords": [],
                "results": [],
                "failures": [],
                "success_count": 0,
                "failure_count": 0,
                "skipped_count": 0,
                "skipped_keywords": [],
                "keyword_source": keyword_source,
                "pool_count": pool_count,
                "sqlite_db_path": str(self.settings.sqlite_db_path),
                "checkpoint_path": "" if keyword_source == "sqlite_pool" else str(checkpoint_path),
            }

        if ensure_login and pending_keywords:
            self.ensure_shopbang_login()

        results: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        total_pending = len(pending_keywords)
        for index, keyword in enumerate(pending_keywords, start=1):
            print(
                f"[ozon-keyword] {index}/{max(total_pending, 1)} start keyword={keyword}",
                flush=True,
            )
            try:
                result = self.run_keyword_with_timeout(keyword)
                results.append(result)
                print(
                    f"[ozon-keyword] {index}/{max(total_pending, 1)} completed keyword={keyword} "
                    f"qualified={int(result.get('qualified_count') or 0)} total={int(result.get('total_collected') or 0)}",
                    flush=True,
                )
                if keyword_source == "manual":
                    self.mark_keyword_completed(keyword=keyword, checkpoint=checkpoint)
                else:
                    self.keyword_pool_repository.mark_keyword_used(
                        keyword=keyword,
                        status="success_empty" if int(result.get("qualified_count") or 0) <= 0 else "success",
                        error="",
                    )
            except Exception as exc:
                print(
                    f"[ozon-keyword] {index}/{max(total_pending, 1)} failed keyword={keyword} error={exc}",
                    flush=True,
                )
                failures.append({"keyword": keyword, "error": str(exc)})
                if keyword_source == "sqlite_pool":
                    self.keyword_pool_repository.mark_keyword_used(
                        keyword=keyword,
                        status="failed",
                        error=str(exc),
                    )

        return {
            "keywords": scrape_keywords,
            "results": results,
            "failures": failures,
            "success_count": len(results),
            "failure_count": len(failures),
            "skipped_count": len(skipped_keywords),
            "skipped_keywords": skipped_keywords,
            "keyword_source": keyword_source,
            "pool_count": pool_count,
            "sqlite_db_path": str(self.settings.sqlite_db_path),
            "checkpoint_path": "" if keyword_source == "sqlite_pool" else str(checkpoint_path),
        }

    def run_for_shopbang_history_keywords(
        self,
        *,
        ensure_login: bool = True,
        pool_count: int = 5,
    ) -> dict[str, Any]:
        """按 Shopbang 历史关键词表中未爬取关键词执行采集流程。"""

        scrape_keywords = self.shopbang_history_keyword_repository.pick_random_unused_keywords(limit=pool_count)
        keyword_source = "shopbang_history"
        if not scrape_keywords:
            return {
                "keywords": [],
                "results": [],
                "failures": [],
                "success_count": 0,
                "failure_count": 0,
                "skipped_count": 0,
                "skipped_keywords": [],
                "keyword_source": keyword_source,
                "pool_count": pool_count,
                "sqlite_db_path": str(self.settings.sqlite_db_path),
                "checkpoint_path": "",
            }

        if ensure_login:
            self.ensure_shopbang_login()

        results: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        total_pending = len(scrape_keywords)
        for index, keyword in enumerate(scrape_keywords, start=1):
            print(
                f"[shopbang-history-keyword] {index}/{max(total_pending, 1)} start keyword={keyword}",
                flush=True,
            )
            try:
                result = self.run_keyword_with_timeout(keyword)
                results.append(result)
                print(
                    f"[shopbang-history-keyword] {index}/{max(total_pending, 1)} completed keyword={keyword} "
                    f"qualified={int(result.get('qualified_count') or 0)} total={int(result.get('total_collected') or 0)}",
                    flush=True,
                )
                self.shopbang_history_keyword_repository.mark_keyword_used(
                    keyword=keyword,
                    status="success_empty" if int(result.get("qualified_count") or 0) <= 0 else "success",
                    error="",
                )
            except Exception as exc:
                print(
                    f"[shopbang-history-keyword] {index}/{max(total_pending, 1)} failed keyword={keyword} error={exc}",
                    flush=True,
                )
                failures.append({"keyword": keyword, "error": str(exc)})
                self.shopbang_history_keyword_repository.mark_keyword_used(
                    keyword=keyword,
                    status="failed",
                    error=str(exc),
                )

        return {
            "keywords": scrape_keywords,
            "results": results,
            "failures": failures,
            "success_count": len(results),
            "failure_count": len(failures),
            "skipped_count": 0,
            "skipped_keywords": [],
            "keyword_source": keyword_source,
            "pool_count": pool_count,
            "sqlite_db_path": str(self.settings.sqlite_db_path),
            "checkpoint_path": "",
        }

    def run_keyword_with_timeout(self, keyword: str) -> dict[str, Any]:
        """执行单个关键词，并在超时时强制终止。"""

        timeout_seconds = max(int(self.settings.ozon_keyword_timeout_seconds or 0), 0)
        if timeout_seconds <= 0:
            return self.run(keyword)

        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        process = context.Process(
            target=_run_keyword_worker,
            args=(keyword, self.settings.model_dump(), result_queue),
            daemon=True,
        )
        process.start()
        process.join(timeout_seconds)

        try:
            if process.is_alive():
                process.terminate()
                process.join(5)
                raise TimeoutError(f"keyword_timeout_after_{timeout_seconds}s")

            try:
                payload = result_queue.get_nowait()
            except Empty as exc:
                raise RuntimeError(
                    f"keyword_process_exited_without_result exitcode={process.exitcode}"
                ) from exc

            if payload.get("status") == "completed":
                return dict(payload.get("result") or {})
            raise RuntimeError(str(payload.get("error") or "keyword_run_failed"))
        finally:
            try:
                result_queue.close()
            except Exception:
                pass

    def get_keyword_checkpoint_path(self) -> Path:
        """返回多关键词采集断点文件路径。"""

        return self.settings.processed_data_path / "ozon_keyword_checkpoint.json"

    def load_keyword_checkpoint(self) -> dict[str, Any]:
        """读取已完成关键词的断点状态。"""

        checkpoint_path = self.get_keyword_checkpoint_path()
        if not checkpoint_path.exists():
            return {"completed_keywords": []}
        try:
            return json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except Exception:
            return {"completed_keywords": []}

    def mark_keyword_completed(self, *, keyword: str, checkpoint: dict[str, Any] | None = None) -> None:
        """把已成功完成的关键词写入断点文件。"""

        checkpoint_path = self.get_keyword_checkpoint_path()
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        state = checkpoint or self.load_keyword_checkpoint()
        completed_keywords = list(state.get("completed_keywords") or [])
        if keyword not in completed_keywords:
            completed_keywords.append(keyword)
        payload = {
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "completed_keywords": completed_keywords,
        }
        checkpoint_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def ensure_shopbang_login(self) -> None:
        """在整轮采集前检查并修复上品帮登录态。"""

        with sync_playwright() as playwright:
            self.collector.login_manager.ensure_logged_in(playwright=playwright, allow_manual_fallback=True)

    def evaluate_products(self, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """为所有抓到的商品补充筛选结论和打分。"""

        evaluated_products: list[dict[str, Any]] = []
        for product in products:
            evaluation = self.collector.evaluate(product)

            product_copy = dict(product)
            product_copy["score"] = evaluation.score
            product_copy["warnings"] = evaluation.warns
            product_copy["failReasons"] = evaluation.fails
            product_copy["estimatedShipping"] = evaluation.shipping
            product_copy["estimatedMaxCost"] = evaluation.max_cost
            product_copy["shippingTier"] = evaluation.tier
            product_copy["passed"] = len(evaluation.fails) == 0
            evaluated_products.append(product_copy)

        evaluated_products.sort(
            key=lambda item: (0 if item.get("passed") else 1, -int(item.get("score") or 0), str(item.get("sku") or ""))
        )
        return evaluated_products

    @staticmethod
    def filter_qualified_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """筛选符合规则的商品。"""

        qualified = [dict(product) for product in products if product.get("passed")]
        qualified.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("sku") or "")))
        return qualified

    @staticmethod
    def merge_enriched_products_into_evaluated(
        *,
        evaluated_products: list[dict[str, Any]],
        qualified_products: list[dict[str, Any]],
    ) -> None:
        """把详情页补抓结果回填到全量评估列表。"""

        qualified_by_sku = {
            str(product.get("sku") or ""): product
            for product in qualified_products
        }
        for index, product in enumerate(evaluated_products):
            sku = str(product.get("sku") or "")
            if sku in qualified_by_sku:
                evaluated_products[index] = qualified_by_sku[sku]

    def save_to_database(self, products: list[dict[str, Any]]) -> dict[str, Any]:
        """把候选商品写入数据库。"""

        payloads = [self.build_candidate_payload(product) for product in products]
        try:
            return self.repository.save_many(payloads)
        except Exception as exc:
            return {
                "status": "failed",
                "count": 0,
                "table": self.repository.table_name,
                "error": str(exc),
            }

    def save_batch_to_sqlite(self, manifest_path: Path) -> dict[str, Any]:
        """把本次 Ozon manifest 作为关键词批次写入 SQLite。"""

        try:
            from ozon_selection.services.ozon_batch_importer import OzonBatchImporter

            importer = OzonBatchImporter(settings=self.settings)
            return importer.import_manifest(manifest_path)
        except Exception as exc:
            return {
                "status": "failed",
                "manifest_path": str(manifest_path),
                "error": str(exc),
            }

    def save_products_to_sqlite(
        self,
        *,
        keyword: str,
        search_url: str,
        products: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """把本次采集通过的 Ozon 商品直接写入 SQLite。"""

        try:
            from ozon_selection.services.ozon_batch_importer import OzonBatchImporter

            importer = OzonBatchImporter(settings=self.settings)
            return importer.import_products(
                keyword=keyword,
                search_url=search_url,
                products=products,
                generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception as exc:
            return {
                "status": "failed",
                "keyword": keyword,
                "error": str(exc),
            }

    def write_manifest(
        self,
        keyword: str,
        search_url: str,
        products: list[dict[str, Any]],
        excel_path: Path,
    ) -> Path:
        """把 Ozon 合格商品写成 JSON 清单，供后续 1688 图搜图步骤读取。"""

        output_dir = self.settings.ozon_scrape_output_path
        output_dir.mkdir(parents=True, exist_ok=True)

        safe_keyword = re.sub(r"\s+", "_", keyword).strip("_") or "ozon"
        output_path = output_dir / f"ozon_candidates_{safe_keyword}_{time.strftime('%Y%m%d_%H%M%S')}.json"
        payload = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "keyword": keyword,
            "search_url": search_url,
            "excel_path": str(excel_path),
            "image_dir": str(self.settings.ozon_scrape_image_path),
            "products": products,
        }
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path

    def write_full_report(
        self,
        *,
        keyword: str,
        search_url: str,
        products: list[dict[str, Any]],
        excel_path: Path,
    ) -> Path:
        """写出包含所有抓取商品和淘汰原因的完整评估报告。"""

        output_dir = self.settings.ozon_scrape_output_path
        output_dir.mkdir(parents=True, exist_ok=True)

        safe_keyword = re.sub(r"\s+", "_", keyword).strip("_") or "ozon"
        output_path = output_dir / f"ozon_evaluated_{safe_keyword}_{time.strftime('%Y%m%d_%H%M%S')}.json"
        payload = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "keyword": keyword,
            "search_url": search_url,
            "excel_path": str(excel_path),
            "image_dir": str(self.settings.ozon_scrape_image_path),
            "total_collected": len(products),
            "qualified_count": sum(1 for product in products if product.get("passed")),
            "rejected_count": sum(1 for product in products if not product.get("passed")),
            "products": products,
        }
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path

    @staticmethod
    def build_candidate_payload(product: dict[str, Any]) -> dict[str, Any]:
        """把商品转换成 `product_candidates` 表结构。"""

        return {
            "source_platform": "ozon",
            "source_product_id": str(product.get("sku") or ""),
            "title": product.get("name"),
            "source_url": product.get("url"),
            "image_url": product.get("imageUrl"),
            "image_path": product.get("localImagePath"),
            "detail_title": product.get("detailTitle"),
            "detail_price": product.get("detailPrice"),
            "detail_image_url": product.get("detailImageUrl"),
            "attributes": product.get("attributes") or [],
            "price": product.get("price"),
            "category": product.get("category"),
            "brand": product.get("brand"),
            "monthly_sales": product.get("monthlySales"),
            "daily_sales": product.get("dailySales"),
            "growth_rate": product.get("growthRate"),
            "return_rate": product.get("returnRate"),
            "conversion_rate": product.get("conversionRate"),
            "ctr": product.get("ctr"),
            "cart_add_rate": product.get("cartAddRate"),
            "search_views": product.get("searchViews"),
            "ad_share": product.get("adShare"),
            "promotion_days": product.get("promotionDays"),
            "weight_grams": product.get("weight"),
            "shipping_mode": product.get("shippingMode"),
            "sellers": product.get("sellers"),
            "lowest_competitor": product.get("lowestCompetitor"),
            "listed_days": product.get("listedDays"),
            "avg_price": product.get("avgPrice"),
            "score": product.get("score"),
            "passed": True,
            "warnings": product.get("warnings") or [],
            "fail_reasons": product.get("failReasons") or [],
            "raw_payload": product,
        }

    @staticmethod
    def find_latest_manifest(output_dir: Path) -> Path:
        """查找最新的 Ozon 候选商品清单。"""

        candidates = sorted(output_dir.glob("ozon_candidates_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not candidates:
            raise FileNotFoundError(f"未找到 Ozon 候选商品清单，请先执行第 2 步。目录: {output_dir}")
        return candidates[0]
