"""Ozon 类目采集器占位实现。"""

from ozon_selection.collectors.base import BaseCollector


class CategoryCollector(BaseCollector):
    """负责类目页面采集。"""

    def collect(self) -> list[dict]:
        """返回类目采集结果。"""

        return []
