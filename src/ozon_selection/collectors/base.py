"""采集器基类。"""


class BaseCollector:
    """统一定义采集器接口，避免具体采集器随意扩展。"""

    def collect(self) -> list[dict]:
        """执行采集任务。"""

        raise NotImplementedError("Subclasses must implement collect().")
