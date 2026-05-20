"""Ozon 关键词采集器占位实现。"""

from ozon_selection.collectors.base import BaseCollector


class KeywordCollector(BaseCollector):
    """负责关键词搜索页采集。"""

    def collect(self) -> list[dict]:
        """返回关键词采集结果。"""

        return []
