"""1688 数据补全任务入口。"""

from ozon_selection.services.alibaba_image_search_pipeline import AlibabaImageSearchPipeline


def run_sync_1688_products() -> None:
    """执行 1688 数据补全任务。"""

    result = AlibabaImageSearchPipeline().run()
    print("sync_1688_products: completed")
    print(f"source_type: {result['source_type']}")
    print(f"source_reference: {result['source_reference']}")
    print(f"processed_products: {result['processed_products']}")
    print(f"matched_items: {result['matched_items']}")
    print(f"sqlite_status: {result['sqlite_result']['status']}")
    if result["sqlite_result"].get("count") is not None:
        print(f"sqlite_saved_count: {result['sqlite_result']['count']}")
