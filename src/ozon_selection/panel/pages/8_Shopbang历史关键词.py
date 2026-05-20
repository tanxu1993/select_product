"""Shopbang 历史关键词页面。"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings
from ozon_selection.repositories.shopbang_history_keyword_repository import ShopbangHistoryKeywordRepository


CONDITION_LABELS = {
    "all": "全部",
    "gt_500": "商品平均价格 > 500",
    "lt_20000": "商品平均价格 < 20000",
}
USED_STATUS_LABELS = {
    "all": "全部",
    "unused": "未爬取",
    "used": "已爬取",
}


def to_excel_bytes(rows: list[dict[str, Any]]) -> bytes:
    """把展示行转换成 XLSX 二进制。"""

    output = BytesIO()
    frame = pd.DataFrame(rows)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="shopbang_history_keywords")
    return output.getvalue()


def build_page_numbers(*, current_page: int, total_pages: int, window: int = 2) -> list[int]:
    """构建传统分页条中展示的页码。"""

    start_page = max(1, current_page - window)
    end_page = min(total_pages, current_page + window)
    return list(range(start_page, end_page + 1))


def resolve_condition_label(item: dict[str, Any]) -> str:
    """根据价格上下界解析来源条件文案。"""

    price_min = item.get("price_min")
    price_max = item.get("price_max")
    if price_min not in (None, "") and price_max in (None, ""):
        return CONDITION_LABELS["gt_500"]
    if price_max not in (None, "") and price_min in (None, ""):
        return CONDITION_LABELS["lt_20000"]
    if price_min not in (None, "") and price_max not in (None, ""):
        return f"{price_min} - {price_max}"
    return "-"


def build_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """构建页面展示行。"""

    display_rows: list[dict[str, Any]] = []
    for item in rows:
        display_rows.append(
            {
                "记录ID": item.get("id"),
                "关键词": item.get("keyword"),
                "关键词中文": str((item.get("raw_payload") or {}).get("zhText") or "").strip(),
                "平均价格": item.get("avg_price"),
                "爬取状态": "已爬取" if item.get("used") else "未爬取",
                "爬取次数": item.get("use_count"),
                "最近执行状态": item.get("last_used_status") or "-",
                "最近错误": item.get("last_error") or "-",
                "最近爬取时间": item.get("used_at") or "-",
                "来源条件": resolve_condition_label(item),
                "首次抓取时间": item.get("first_seen_at"),
                "最近抓取时间": item.get("last_seen_at"),
                "创建时间": item.get("created_at"),
                "更新时间": item.get("updated_at"),
            }
        )
    return display_rows


def paginate_rows(rows: list[dict[str, Any]], *, page_size: int) -> tuple[list[dict[str, Any]], int, int, int]:
    """按当前页码分页。"""

    total_count = len(rows)
    total_pages = max((total_count - 1) // int(page_size) + 1, 1)
    current_page = int(st.session_state.get("shopbang_history_keyword_current_page", 1))
    if current_page < 1:
        current_page = 1
    if current_page > total_pages:
        current_page = total_pages
    st.session_state["shopbang_history_keyword_current_page"] = current_page

    start_index = (current_page - 1) * int(page_size)
    end_index = start_index + int(page_size)
    return rows[start_index:end_index], current_page, total_pages, total_count


def render_pagination(*, current_page: int, total_pages: int, total_count: int) -> None:
    """渲染底部分页条。"""

    st.markdown("---")
    info_col, nav_col = st.columns([2.2, 5.8])
    with info_col:
        st.caption(f"当前筛选共 {total_count} 条关键词，第 {current_page} / {total_pages} 页")
    with nav_col:
        nav_cols = st.columns(9)
        if nav_cols[0].button("上一页", disabled=current_page <= 1, use_container_width=True, key="shopbang_history_prev"):
            st.session_state["shopbang_history_keyword_current_page"] = current_page - 1
            st.rerun()

        page_numbers = build_page_numbers(current_page=current_page, total_pages=total_pages)
        for index, page_number in enumerate(page_numbers, start=1):
            label = f"[{page_number}]" if page_number == current_page else str(page_number)
            if index < len(nav_cols) - 1 and nav_cols[index].button(
                label,
                use_container_width=True,
                key=f"shopbang_history_page_{page_number}",
            ):
                st.session_state["shopbang_history_keyword_current_page"] = page_number
                st.rerun()

        if nav_cols[-1].button("下一页", disabled=current_page >= total_pages, use_container_width=True, key="shopbang_history_next"):
            st.session_state["shopbang_history_keyword_current_page"] = current_page + 1
            st.rerun()


def main() -> None:
    """渲染 Shopbang 历史关键词页。"""

    settings = get_settings()
    repository = ShopbangHistoryKeywordRepository(settings=settings)

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

    st.title("Shopbang 历史关键词")
    st.caption("围绕 `shopbang_history_keywords` 表查看从 History 页面抓到的关键词，并跟踪路径四中的关键词爬取状态。")

    if not repository.is_configured:
        st.warning("未配置 `SQLITE_PATH`，请先配置 SQLite 数据文件路径后再使用管理系统。")
        return

    raw_rows = repository.list_keywords()

    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns([2.2, 1.5, 1.5, 1.2])
    with filter_col1:
        keyword_filter = st.text_input("按关键词过滤")
    with filter_col2:
        condition_label = st.selectbox(
            "按来源条件过滤",
            options=[CONDITION_LABELS["all"], CONDITION_LABELS["gt_500"], CONDITION_LABELS["lt_20000"]],
            index=0,
        )
    with filter_col3:
        used_status_label = st.selectbox(
            "按爬取状态过滤",
            options=[USED_STATUS_LABELS["all"], USED_STATUS_LABELS["unused"], USED_STATUS_LABELS["used"]],
            index=0,
        )
    with filter_col4:
        page_size = st.selectbox("每页条数", options=[20, 50, 100, 200], index=1)

    condition_filter = {label: code for code, label in CONDITION_LABELS.items()}[condition_label]
    used_status_filter = {label: code for code, label in USED_STATUS_LABELS.items()}[used_status_label]

    filtered_rows = raw_rows
    if keyword_filter.strip():
        keyword = keyword_filter.strip().lower()
        filtered_rows = [item for item in filtered_rows if keyword in str(item.get("keyword") or "").lower()]
    if condition_filter != "all":
        filtered_rows = [item for item in filtered_rows if resolve_condition_label(item) == CONDITION_LABELS[condition_filter]]
    if used_status_filter == "used":
        filtered_rows = [item for item in filtered_rows if item.get("used")]
    elif used_status_filter == "unused":
        filtered_rows = [item for item in filtered_rows if not item.get("used")]

    gt_count = sum(1 for item in raw_rows if resolve_condition_label(item) == CONDITION_LABELS["gt_500"])
    lt_count = sum(1 for item in raw_rows if resolve_condition_label(item) == CONDITION_LABELS["lt_20000"])
    used_count = sum(1 for item in raw_rows if item.get("used"))
    unused_count = len(raw_rows) - used_count

    metric_col1, metric_col2, metric_col3, metric_col4, metric_col5 = st.columns(5)
    metric_col1.metric("关键词总数", len(raw_rows))
    metric_col2.metric("商品平均价格 > 500", gt_count)
    metric_col3.metric("商品平均价格 < 20000", lt_count)
    metric_col4.metric("已爬取", used_count)
    metric_col5.metric("未爬取", unused_count)

    if not filtered_rows:
        st.info("当前筛选条件下没有关键词数据。")
        return

    display_rows = build_rows(filtered_rows)
    page_rows, current_page, total_pages, total_count = paginate_rows(display_rows, page_size=int(page_size))

    st.dataframe(
        pd.DataFrame(page_rows),
        hide_index=True,
        use_container_width=True,
        height=560,
    )

    st.download_button(
        "导出当前筛选 XLSX",
        data=to_excel_bytes(display_rows),
        file_name="shopbang_history_keywords.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    render_pagination(current_page=current_page, total_pages=total_pages, total_count=total_count)


main()
