"""Ozon 采集任务入口。"""

from ozon_selection.services.ozon_candidate_pipeline import OzonCandidatePipeline


def run_collect_ozon_products() -> None:
    """执行 Ozon 商品采集任务。"""

    pipeline = OzonCandidatePipeline()
    batch_result = pipeline.run_for_keywords()
    print("collect_ozon_products: completed")
    print(f"keywords: {', '.join(batch_result['keywords'])}")
    print(f"success_count: {batch_result['success_count']}")
    print(f"failure_count: {batch_result['failure_count']}")
    print(f"skipped_count: {batch_result['skipped_count']}")
    print(f"sqlite_db_path: {batch_result['sqlite_db_path']}")
    print(f"checkpoint_path: {batch_result['checkpoint_path']}")
    if batch_result["skipped_keywords"]:
        print(f"skipped_keywords: {', '.join(batch_result['skipped_keywords'])}")

    for result in batch_result["results"]:
        print(f"keyword: {result['keyword']}")
        print(f"search_url: {result['search_url']}")
        print(f"total_collected: {result['total_collected']}")
        print(f"qualified_count: {result['qualified_count']}")
        print(f"image_dir: {result['image_dir']}")
        print(f"sqlite_status: {result['sqlite_result']['status']}")
        if result["sqlite_result"].get("batch_id") is not None:
            print(f"sqlite_batch_id: {result['sqlite_result']['batch_id']}")
        if result["sqlite_result"].get("source_ref"):
            print(f"sqlite_source_ref: {result['sqlite_result']['source_ref']}")

    for failure in batch_result["failures"]:
        print(f"failed_keyword: {failure['keyword']}")
        print(f"failed_error: {failure['error']}")
