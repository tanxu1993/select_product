"""定时任务注册目标函数。"""

from ozon_selection.tasks.collect_ozon_products import run_collect_ozon_products
from ozon_selection.tasks.push_review_queue import run_push_review_queue
from ozon_selection.tasks.run_ai_analysis import run_ai_analysis
from ozon_selection.tasks.sync_1688_products import run_sync_1688_products


JOB_REGISTRY = {
    "collect_ozon_products": run_collect_ozon_products,
    "sync_1688_products": run_sync_1688_products,
    "run_ai_analysis": run_ai_analysis,
    "push_review_queue": run_push_review_queue,
}
