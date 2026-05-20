"""主程序入口占位文件。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings


def main() -> None:
    """打印系统启动信息。"""

    settings = get_settings()
    print(f"{settings.app_name} is ready.")


if __name__ == "__main__":
    main()
