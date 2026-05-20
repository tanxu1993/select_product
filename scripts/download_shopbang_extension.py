"""下载并解包上品帮插件。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ozon_selection.collectors.ozon.shopbang_auth import ShopbangExtensionManager


def main() -> None:
    """下载并解包上品帮插件。"""

    manager = ShopbangExtensionManager()
    print("正在下载并解包上品帮插件...")
    unpack_path = manager.download_and_unpack()
    print(f"插件已准备完成: {unpack_path}")


if __name__ == "__main__":
    main()
