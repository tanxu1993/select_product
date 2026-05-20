"""审核流服务占位实现。"""


class ReviewService:
    """负责把 AI 分析结果推入人工审核队列。"""

    def queue(self, payload: dict) -> dict:
        """返回入队结果。"""

        return {"status": "todo", "payload": payload}
