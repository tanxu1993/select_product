"""Ozon 店铺列表页。"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings
from ozon_selection.repositories.ozon_reviewed_seller_repository import OzonReviewedSellerRepository


STATUS_LABELS = {
    "all": "全部",
    "pending": "待抓取",
    "in_progress": "抓取中",
    "completed": "已完成",
    "failed": "失败",
}
SHOP_TYPE_LABELS = {
    "all": "全部",
    "unclassified": "未分类",
    "杂货铺": "杂货铺",
    "垂直店": "垂直店",
}
SELECTION_STATE_KEY = "shop_list_selection_map"


def get_selection_map() -> dict[str, bool]:
    """读取店铺勾选状态。"""

    if SELECTION_STATE_KEY not in st.session_state:
        st.session_state[SELECTION_STATE_KEY] = {}
    return st.session_state[SELECTION_STATE_KEY]


def build_shop_rows(shops: list[dict], selection_map: dict[str, bool]) -> list[dict]:
    """构建页面展示行。"""

    return [
        {
            "选择": bool(selection_map.get(str(item.get("seller_url") or "").strip(), False)),
            "店铺名": item.get("seller_name"),
            "店铺URL": item.get("seller_url"),
            "评论数": item.get("review_count"),
            "店铺类型": str(item.get("shop_type") or "").strip() or "未分类",
            "类型备注": item.get("shop_type_reason"),
            "抓取状态": STATUS_LABELS.get(str(item.get("crawl_status") or "").strip(), item.get("crawl_status") or "-"),
            "抓取商品数": item.get("crawl_product_count"),
            "合格商品数": item.get("crawl_qualified_count"),
            "淘汰商品数": item.get("crawl_rejected_count"),
            "类型抽样数": item.get("shop_type_sample_size"),
            "一级类目数": item.get("shop_type_primary_category_count"),
            "品牌数": item.get("shop_type_brand_count"),
            "类型判定时间": item.get("shop_type_checked_at"),
            "首次发现时间": item.get("first_seen_at"),
            "最近发现时间": item.get("last_seen_at"),
            "抓取开始时间": item.get("crawl_started_at"),
            "抓取完成时间": item.get("crawl_completed_at"),
            "抓取失败时间": item.get("crawl_failed_at"),
            "抓取错误": item.get("crawl_error"),
        }
        for item in shops
    ]


def to_excel_bytes(rows: list[dict]) -> bytes:
    """把展示行转成 XLSX 二进制。"""

    output = BytesIO()
    frame = pd.DataFrame(rows)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="shops")
    return output.getvalue()


def sync_selection_state(records: list[dict]) -> dict[str, bool]:
    """把当前页勾选同步回 session_state。"""

    selection_map = get_selection_map()
    for row in records:
        seller_url = str(row.get("店铺URL") or "").strip()
        if not seller_url:
            continue
        selection_map[seller_url] = bool(row.get("选择"))
    return selection_map


def build_page_numbers(*, current_page: int, total_pages: int, window: int = 2) -> list[int]:
    """构建传统分页条中展示的页码。"""

    start_page = max(1, current_page - window)
    end_page = min(total_pages, current_page + window)
    return list(range(start_page, end_page + 1))


def main() -> None:
    """渲染店铺列表页。"""

    settings = get_settings()
    repository = OzonReviewedSellerRepository(settings=settings)

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

    st.title("Ozon 店铺列表")
    st.caption("围绕 `ozon_reviewed_seller_shops` 表查看店铺、评论数和按店铺抓商品的抓取状态。")

    if not repository.is_configured:
        st.warning("未配置 `SQLITE_PATH`，请先配置 SQLite 数据文件路径后再使用管理系统。")
        return

    top_col1, top_col2, top_col3, top_col4 = st.columns([1.4, 1.2, 1.2, 1.2])
    with top_col1:
        status_label = st.selectbox(
            "按抓取状态过滤",
            options=[STATUS_LABELS["all"], STATUS_LABELS["pending"], STATUS_LABELS["in_progress"], STATUS_LABELS["completed"], STATUS_LABELS["failed"]],
            index=0,
        )
    with top_col2:
        shop_type_label = st.selectbox(
            "按店铺类型过滤",
            options=[
                SHOP_TYPE_LABELS["all"],
                SHOP_TYPE_LABELS["unclassified"],
                SHOP_TYPE_LABELS["杂货铺"],
                SHOP_TYPE_LABELS["垂直店"],
            ],
            index=0,
        )
    with top_col3:
        page_size = st.selectbox("每页条数", options=[20, 50, 100, 200], index=1)
    with top_col4:
        st.write("")
        st.write("")
        if st.button("初始化 Schema", use_container_width=True):
            repository.ensure_schema()
            st.success("SQLite schema 已初始化。")

    status_filter = {label: code for code, label in STATUS_LABELS.items()}[status_label]
    shop_type_filter = {label: code for code, label in SHOP_TYPE_LABELS.items()}[shop_type_label]
    resolved_shop_type_filter = "" if shop_type_filter == "unclassified" else shop_type_filter

    all_shops = repository.list_shops(crawl_status="all", shop_type="all")
    shops = repository.list_shops(
        crawl_status=status_filter,
        shop_type=resolved_shop_type_filter if resolved_shop_type_filter != "all" else "all",
    )
    if shop_type_filter == "unclassified":
        shops = [item for item in shops if not str(item.get("shop_type") or "").strip()]

    pending_count = sum(1 for item in all_shops if str(item.get("crawl_status") or "") == "pending")
    in_progress_count = sum(1 for item in all_shops if str(item.get("crawl_status") or "") == "in_progress")
    completed_count = sum(1 for item in all_shops if str(item.get("crawl_status") or "") == "completed")
    failed_count = sum(1 for item in all_shops if str(item.get("crawl_status") or "") == "failed")
    misc_count = sum(1 for item in all_shops if str(item.get("shop_type") or "").strip() == "杂货铺")
    vertical_count = sum(1 for item in all_shops if str(item.get("shop_type") or "").strip() == "垂直店")

    metric_col1, metric_col2, metric_col3, metric_col4, metric_col5, metric_col6, metric_col7 = st.columns(7)
    metric_col1.metric("店铺总数", len(all_shops))
    metric_col2.metric("待抓取", pending_count)
    metric_col3.metric("抓取中", in_progress_count)
    metric_col4.metric("已完成", completed_count)
    metric_col5.metric("失败", failed_count)
    metric_col6.metric("杂货铺", misc_count)
    metric_col7.metric("垂直店", vertical_count)

    st.markdown("### 店铺列表")
    if not shops:
        st.info("当前筛选条件下没有店铺数据。")
        return

    total_count = len(shops)
    total_pages = max((total_count - 1) // int(page_size) + 1, 1)
    current_page = int(st.session_state.get("shop_list_current_page", 1))
    if current_page > total_pages:
        current_page = total_pages
    if current_page < 1:
        current_page = 1
    st.session_state["shop_list_current_page"] = current_page

    start_index = (int(current_page) - 1) * int(page_size)
    end_index = start_index + int(page_size)
    page_shops = shops[start_index:end_index]

    selection_map = get_selection_map()
    page_rows = build_shop_rows(page_shops, selection_map)
    editor_key = f"shop_list_editor_{status_filter}_{current_page}_{page_size}"
    edited_frame = st.data_editor(
        pd.DataFrame(page_rows),
        key=editor_key,
        hide_index=True,
        use_container_width=True,
        height=520,
        disabled=[column for column in pd.DataFrame(page_rows).columns if column != "选择"],
        column_config={
            "选择": st.column_config.CheckboxColumn("选择"),
            "店铺URL": st.column_config.LinkColumn("店铺URL"),
        },
    )
    selection_map = sync_selection_state(edited_frame.to_dict(orient="records"))

    selected_urls = [seller_url for seller_url, selected in selection_map.items() if selected]
    current_page_urls = [str(item.get("seller_url") or "").strip() for item in page_shops if str(item.get("seller_url") or "").strip()]

    action_col1, action_col2, action_col3, action_col4 = st.columns(4)
    with action_col1:
        if st.button("全选当前页", use_container_width=True):
            for seller_url in current_page_urls:
                selection_map[seller_url] = True
            st.rerun()
    with action_col2:
        if st.button("取消当前页全选", use_container_width=True):
            for seller_url in current_page_urls:
                selection_map[seller_url] = False
            st.rerun()
    with action_col3:
        if st.button("删除选中店铺", type="secondary", use_container_width=True, disabled=not selected_urls):
            result = repository.delete_shops(selected_urls)
            for seller_url in selected_urls:
                selection_map.pop(seller_url, None)
            st.success(f"已删除 {result.get('deleted_count', 0)} 家店铺。")
            st.rerun()
    with action_col4:
        if st.button("删除当前筛选全部", type="secondary", use_container_width=True, disabled=not shops):
            result = repository.delete_shops([str(item.get("seller_url") or "").strip() for item in shops])
            for item in shops:
                selection_map.pop(str(item.get("seller_url") or "").strip(), None)
            st.success(f"已删除当前筛选结果中的 {result.get('deleted_count', 0)} 家店铺。")
            st.rerun()

    st.download_button(
        "导出当前筛选结果 XLSX",
        data=to_excel_bytes(build_shop_rows(shops, selection_map)),
        file_name="ozon_reviewed_seller_shops.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    st.markdown("---")
    page_bar_info_col, page_bar_nav_col = st.columns([2.2, 5.8])
    with page_bar_info_col:
        st.caption(f"当前筛选共 {total_count} 家店铺，第 {current_page} / {total_pages} 页")
    with page_bar_nav_col:
        nav_cols = st.columns(9)
        if nav_cols[0].button("上一页", disabled=current_page <= 1, use_container_width=True):
            st.session_state["shop_list_current_page"] = current_page - 1
            st.rerun()

        page_numbers = build_page_numbers(current_page=current_page, total_pages=total_pages)
        for index, page_number in enumerate(page_numbers, start=1):
            label = f"[{page_number}]" if page_number == current_page else str(page_number)
            if index < len(nav_cols) - 1 and nav_cols[index].button(label, use_container_width=True):
                st.session_state["shop_list_current_page"] = page_number
                st.rerun()

        if nav_cols[-1].button("下一页", disabled=current_page >= total_pages, use_container_width=True):
            st.session_state["shop_list_current_page"] = current_page + 1
            st.rerun()


main()
