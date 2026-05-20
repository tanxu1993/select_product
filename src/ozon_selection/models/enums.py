"""枚举定义。"""

from enum import Enum


class ReviewStatus(str, Enum):
    """审核状态枚举。"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
