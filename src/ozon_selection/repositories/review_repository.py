"""审核记录仓储占位实现。"""


class ReviewRepository:
    """负责审核记录表的读写。"""

    def save(self, payload: dict) -> dict:
        """保存审核结果。"""

        return {"status": "todo", "payload": payload}
