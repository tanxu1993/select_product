"""启动 APScheduler 的脚本入口。"""

from __future__ import annotations

import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ozon_selection.scheduler.scheduler import build_scheduler


def main() -> None:
    """启动调度器并保持进程常驻。"""

    scheduler = build_scheduler()
    scheduler.start()
    print("Scheduler started. Press Ctrl+C to exit.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.shutdown()
        print("Scheduler stopped.")


if __name__ == "__main__":
    main()
