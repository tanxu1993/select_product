"""APScheduler 构建逻辑。"""

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from config.settings import get_settings
from ozon_selection.scheduler.jobs import JOB_REGISTRY


def build_scheduler() -> BlockingScheduler:
    """根据环境变量构建调度器。"""

    settings = get_settings()
    scheduler = BlockingScheduler(timezone=settings.scheduler_timezone)

    scheduler.add_job(
        JOB_REGISTRY["collect_ozon_products"],
        trigger=CronTrigger.from_crontab(settings.collect_ozon_cron, timezone=settings.scheduler_timezone),
        id="collect_ozon_products",
        replace_existing=True,
    )
    scheduler.add_job(
        JOB_REGISTRY["sync_1688_products"],
        trigger=CronTrigger.from_crontab(settings.sync_1688_cron, timezone=settings.scheduler_timezone),
        id="sync_1688_products",
        replace_existing=True,
    )
    scheduler.add_job(
        JOB_REGISTRY["run_ai_analysis"],
        trigger=CronTrigger.from_crontab(settings.run_ai_analysis_cron, timezone=settings.scheduler_timezone),
        id="run_ai_analysis",
        replace_existing=True,
    )
    scheduler.add_job(
        JOB_REGISTRY["push_review_queue"],
        trigger=CronTrigger.from_crontab(settings.push_review_queue_cron, timezone=settings.scheduler_timezone),
        id="push_review_queue",
        replace_existing=True,
    )

    return scheduler
