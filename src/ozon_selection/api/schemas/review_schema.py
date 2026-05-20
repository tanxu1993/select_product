"""审核记录结构定义。"""

from pydantic import BaseModel, Field


class ReviewSchema(BaseModel):
    """标准化审核结果结构。"""

    product_id: str = Field(description="候选商品 ID")
    reviewer: str = Field(description="审核人")
    decision: str = Field(description="审核结论")
