"""AI 选品服务占位实现。"""


class AISelectorService:
    """负责组合商品数据并调用 AI 分析。"""

    def score_candidate(self, payload: dict) -> dict:
        """返回候选商品评分占位结果。"""

        return {"status": "todo", "payload": payload}
