"""任务日志仓储占位实现。"""


class TaskLogRepository:
    """负责任务执行日志表的读写。"""

    def save(self, payload: dict) -> dict:
        """保存任务日志。"""

        return {"status": "todo", "payload": payload}
