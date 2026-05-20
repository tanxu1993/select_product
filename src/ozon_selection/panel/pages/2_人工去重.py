"""Ozon 候选商品人工去重页面。"""

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


def render_product_card(product: dict, checkbox_key: str) -> None:
    """渲染商品卡片。"""

    with st.container(border=True):
        image_path = str(product.get("image_path") or "")
        if image_path and Path(image_path).exists():
            st.image(image_path, use_container_width=True)
        st.checkbox("保留该图片", key=checkbox_key, value=False)


def main() -> None:
    """渲染人工去重页面。"""

    settings = get_settings()
    repository = OzonBatchRepository(settings=settings)

    st.title("Ozon 人工去重")
    st.caption("按关键词批次做人审去重，点击提交后只保留勾选主图。")
    st.markdown(
        """
        <style>
        .block-container {
            max-width: 100%;
            padding-top: 1.2rem;
            padding-left: 1rem;
            padding-right: 1rem;
        }
        div[data-testid="stHorizontalBlock"] {
            gap: 0.35rem;
        }
        div[data-testid="stCheckbox"] label {
            font-size: 0.85rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    if not repository.is_configured:
        st.warning("未配置 `SQLITE_PATH`，请先配置 SQLite 数据文件路径后再使用管理系统。")
        return

    st.markdown("### 控制区")
    control_col1, control_col2 = st.columns([1.4, 1.4])
    with control_col1:
        columns_per_row = st.slider("每行图片数", min_value=4, max_value=8, value=5, step=1)
    with control_col2:
        st.write("")
        st.write("")
        delete_disabled = not bool(st.session_state.get("candidate_review_keyword_filter", "").strip())
        if st.button("按关键词过滤删除", use_container_width=True, type="secondary", disabled=delete_disabled):
            result = repository.delete_batches_by_keyword(st.session_state.get("candidate_review_keyword_filter", ""))
            if result["status"] == "deleted":
                st.success(
                    f"已删除关键词过滤 `{result['keyword']}` 命中的 {result['deleted_batches']} 个批次，"
                    f"共删除 {result['deleted_products']} 个商品。"
                )
                st.rerun()
            elif result.get("reason") == "no_matching_batches":
                st.warning("当前关键词过滤没有命中任何批次。")
            else:
                st.warning("请先输入关键词过滤，再执行删除。")

    filter_col1, filter_col2 = st.columns([3, 1])
    with filter_col1:
        keyword_filter = st.text_input("按关键词过滤", key="candidate_review_keyword_filter")
    with filter_col2:
        st.write("")
        show_completed = st.checkbox("显示已完成批次", value=True)

    batches = repository.list_batches(keyword=keyword_filter, include_completed=show_completed)
    reviewable_batches = [
        item
        for item in batches
        if item.get("status") != "completed" and int(item.get("total_products") or 0) > 0
    ]
    completed_batches = [item for item in batches if item.get("status") == "completed"]

    if not batches:
        st.info("SQLite 中还没有可审核的批次。请先执行 `python scripts/collect_ozon_candidates.py`。")
        return

    if completed_batches:
        st.info(f"当前筛选结果中已有 {len(completed_batches)} 个已完成批次，它们不会进入当前自动审核队列。")

    if not reviewable_batches:
        st.success("当前筛选范围内没有待审核批次，说明这些关键词批次都已处理完成。")
        return

    selected_batch = reviewable_batches[0]
    batch_id = int(selected_batch["id"])
    products = repository.get_batch_products(batch_id)
    if not products:
        st.warning("当前自动选中的批次没有可审核商品，已跳过空批次。建议在数据管理页删除该空批次。")
        return

    st.markdown(
        f"**当前队列位置**: 1 / {len(reviewable_batches)}  \n"
        f"**批次ID**: {batch_id}  \n"
        f"**关键词**: {selected_batch.get('keyword') or '-'}  \n"
        f"**状态**: {selected_batch.get('status') or '-'}  \n"
        f"**商品数**: {len(products)}"
    )

    if len(reviewable_batches) > 1:
        st.caption(f"提交当前批次后，页面会自动切换到下一个待审核批次。剩余待审核批次：{len(reviewable_batches)}")

    if selected_batch.get("status") == "completed":
        st.success(
            f"当前关键词 `{selected_batch.get('keyword')}` 的人工去重已完成。"
            f" 已保留 {selected_batch.get('dedupe_kept_count') or len(products)} 个商品。"
        )
    else:
        st.warning("当前批次尚未完成人工去重。勾选需要保留的主图后提交；如果这个批次一个都不保留，也可以直接提交。")

    st.markdown("### 商品主图")
    form = st.form(key=f"manual_dedupe_batch_{batch_id}")
    columns = []
    for index, product in enumerate(products):
        if index % columns_per_row == 0:
            columns = form.columns(columns_per_row)
        with columns[index % columns_per_row]:
            render_product_card(product, checkbox_key=f"keep_{batch_id}_{product['id']}")

    submitted = form.form_submit_button(
        "提交人工去重结果",
        type="primary",
        disabled=selected_batch.get("status") == "completed",
    )

    if submitted:
        keep_ids = [
            int(product["id"])
            for product in products
            if st.session_state.get(f"keep_{batch_id}_{product['id']}")
        ]
        result = repository.apply_manual_dedupe(batch_id=batch_id, keep_product_ids=keep_ids)
        st.success(
            f"人工去重已完成。保留 {result['kept_count']} 个商品，删除 {result['deleted_count']} 个商品。"
        )
        st.rerun()


main()
