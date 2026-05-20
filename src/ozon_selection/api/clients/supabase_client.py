"""Supabase 客户端初始化封装。"""

from config.settings import get_settings


def get_supabase_connection_info() -> dict:
    """返回创建 Supabase 客户端所需的核心参数。"""

    settings = get_settings()
    return {
        "url": settings.supabase_url,
        "service_role_key": settings.supabase_service_role_key,
        "schema": settings.supabase_schema,
    }
