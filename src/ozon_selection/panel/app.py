"""Streamlit 审核面板入口。"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings


def main() -> None:
    """渲染审核面板首页。"""

    settings = get_settings()

    st.set_page_config(
        page_title="Ozon AI 选品审核面板",
        layout="wide",
    )

    st.markdown(
        """
        <style>
        .block-container {
            max-width: 100%;
            padding-top: 1.2rem;
            padding-left: 1rem;
            padding-right: 1rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("Ozon AI 选品审核面板")
    st.caption("用于管理 SQLite 中的 Ozon 批次，并对关键词抓取到的商品主图做人工去重。")

    col1, col2, col3 = st.columns(3)
    col1.metric("运行环境", settings.app_env)
    col2.metric("默认模型", settings.openai_product_parse_model)
    col3.metric("调度时区", settings.scheduler_timezone)

    st.info(
        "请从左侧页面导航进入 `Ozon 批次仪表盘`、`Ozon 人工去重`、`SQLite 数据管理中心`、`Ozon 店铺列表`、`1688以图搜图`"
        "、`Ozon 关键词池` 或 `Shopbang 历史关键词`。`python scripts/collect_ozon_candidates_from_shopbang_hot.py` 会把结构化关键词写入 SQLite，"
        " `python scripts/collect_ozon_candidates.py` 默认会从 SQLite 关键词池随机抽取未使用关键词执行，并回写使用状态。"
    )


if __name__ == "__main__":
    main()
