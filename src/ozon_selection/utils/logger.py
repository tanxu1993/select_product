"""日志工具。"""

import logging


def setup_logging(level: str = "INFO") -> None:
    """初始化标准库日志。"""

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
