"""SQLite 批次仪表盘。"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings
from ozon_selection.repositories.ozon_batch_repository import OzonBatchRepository


def main() -> None:
    """渲染 SQLite 批次总览。"""

    settings = get_settings()
    repository = OzonBatchRepository(settings=settings)

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

    st.title("Ozon 批次仪表盘")
    st.caption("查看 SQLite 中的关键词批次、待图片去重任务和已完成任务。采集脚本会直接写入这里。")

    if not repository.is_configured:
        st.warning("未配置 `SQLITE_PATH`，请先配置 SQLite 数据文件路径后再使用管理系统。")
        return

    batches = repository.list_batches()
    pending = [item for item in batches if item.get("status") != "completed"]
    completed = [item for item in batches if item.get("status") == "completed"]

    col1, col2, col3 = st.columns(3)
    col1.metric("批次数", len(batches))
    col2.metric("待图片去重", len(pending))
    col3.metric("已完成", len(completed))

    if not batches:
        st.info("SQLite 中还没有批次。请先执行 `python scripts/collect_ozon_candidates.py`。")
        return

    st.markdown("### 批次列表")
    st.dataframe(
        [
            {
                "批次ID": item.get("id"),
                "关键词": item.get("keyword"),
                "状态": item.get("status"),
                "商品数": item.get("total_products"),
                "保留数": item.get("dedupe_kept_count"),
                "生成时间": item.get("generated_at"),
                "完成时间": item.get("dedupe_completed_at"),
                "批次来源": item.get("source_manifest_path"),
            }
            for item in batches
        ],
        use_container_width=True,
        hide_index=True,
    )


main()
