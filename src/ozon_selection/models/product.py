"""商品领域模型。"""

from pydantic import BaseModel, Field


class Product(BaseModel):
    """候选商品领域对象。"""

    product_id: str = Field(description="商品唯一 ID")
    title: str = Field(description="商品标题")
    ozon_price_rub: float = Field(default=0, description="Ozon 售价")
