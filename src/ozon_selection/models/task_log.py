"""任务日志领域模型。"""

from pydantic import BaseModel, Field


class TaskLog(BaseModel):
    """任务执行日志对象。"""

    task_name: str = Field(description="任务名称")
    status: str = Field(description="执行状态")
    message: str = Field(default="", description="执行说明")
