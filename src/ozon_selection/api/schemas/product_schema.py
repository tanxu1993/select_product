"""候选商品结构定义。"""

from pydantic import BaseModel, Field


class ProductSchema(BaseModel):
    """标准化商品结构。"""

    product_id: str = Field(description="商品唯一标识")
    title: str = Field(description="商品标题")
    source: str = Field(default="ozon", description="数据来源")
