"""数据补全服务占位实现。"""


class DataEnricherService:
    """负责合并 Ozon、1688 与其他扩展数据。"""

    def enrich(self, payload: dict) -> dict:
        """返回补全后的结果。"""

        return {"status": "todo", "payload": payload}
