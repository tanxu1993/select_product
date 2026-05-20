"""1688以图搜图页。"""

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
from ozon_selection.repositories.alibaba_image_search_repository import AlibabaImageSearchRepository
from ozon_selection.repositories.ozon_batch_repository import OzonBatchRepository
from ozon_selection.services.alibaba_image_search_pipeline import AlibabaImageSearchPipeline


STATUS_LABELS = {
    "all": "全部",
    "pending": "未完成",
    "completed": "已完成",
}
SELECTION_STATE_KEY = "alibaba_result_selection_map"


def get_selection_map() -> dict[str, bool]:
    """读取结果勾选状态。"""

    if SELECTION_STATE_KEY not in st.session_state:
        st.session_state[SELECTION_STATE_KEY] = {}
    return st.session_state[SELECTION_STATE_KEY]


def build_export_rows(rows: list[dict], selection_map: dict[str, bool]) -> list[dict]:
    """构建 1688以图搜图展示/导出行。"""

    return [
        {
            "选择": bool(selection_map.get(str(item.get("id") or "").strip(), False)),
            "记录ID": item.get("id"),
            "完成状态": "已完成" if item.get("is_completed") else "未完成",
            "完成时间": item.get("completed_at"),
            "Ozon批次ID": item.get("ozon_batch_id"),
            "关键词": item.get("ozon_keyword"),
            "Ozon SKU": item.get("source_product_id"),
            "Ozon商品": item.get("source_title"),
            "Ozon链接": item.get("source_product_url"),
            "Ozon主图路径": item.get("source_image_path"),
            "1688标题": item.get("supplier_title"),
            "1688链接": item.get("supplier_product_url"),
            "1688价格": item.get("supplier_price"),
            "1688价格文案": item.get("supplier_price_text"),
            "1688单价": item.get("supplier_unit_price"),
            "1688单价文案": item.get("supplier_unit_price_text"),
            "1688重量文案": item.get("supplier_weight_text"),
            "1688重量(g)": item.get("supplier_weight_grams"),
            "1688商品属性": AlibabaImageSearchPipeline.format_attributes(item.get("supplier_attributes")),
            "1688卖家": item.get("supplier_seller"),
            "GPT主图是否同款": AlibabaImageSearchPipeline.format_bool_value(item.get("ai_image_same_product")),
            "GPT主图同款分": item.get("ai_image_match_score"),
            "GPT主图置信度": item.get("ai_image_confidence"),
            "GPT主图说明": item.get("ai_image_summary"),
            "写入时间": item.get("created_at"),
        }
        for item in rows
    ]


def to_excel_bytes(rows: list[dict]) -> bytes:
    """把导出行转成 XLSX 二进制。"""

    output = BytesIO()
    frame = pd.DataFrame(rows)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="1688图搜图")
    return output.getvalue()


def sync_selection_state(records: list[dict]) -> dict[str, bool]:
    """把当前页勾选同步回 session_state。"""

    selection_map = get_selection_map()
    for row in records:
        result_id = str(row.get("记录ID") or "").strip()
        if not result_id:
            continue
        selection_map[result_id] = bool(row.get("选择"))
    return selection_map


def build_page_numbers(*, current_page: int, total_pages: int, window: int = 2) -> list[int]:
    """构建传统分页条中展示的页码。"""

    start_page = max(1, current_page - window)
    end_page = min(total_pages, current_page + window)
    return list(range(start_page, end_page + 1))


def build_pending_source_rows(rows: list[dict]) -> list[dict]:
    """构建待执行 Ozon 商品展示行。"""

    return [
        {
            "记录ID": item.get("id"),
            "批次ID": item.get("batch_id"),
            "批次关键词": item.get("batch_keyword"),
            "Ozon SKU": item.get("source_product_id"),
            "Ozon商品": item.get("title"),
            "Ozon链接": item.get("source_url"),
            "Ozon主图路径": item.get("image_path"),
            "价格": item.get("price"),
            "评分": item.get("score"),
            "筛选通过": "是" if item.get("passed") else "否",
            "已完成1688处理": "是" if item.get("alibaba_processed") else "否",
            "图片去重完成时间": item.get("batch_dedupe_completed_at"),
        }
        for item in rows
    ]


def main() -> None:
    """渲染 1688以图搜图页。"""

    settings = get_settings()
    repository = AlibabaImageSearchRepository(settings=settings)
    batch_repository = OzonBatchRepository(settings=settings)

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

    st.title("1688以图搜图")
    st.caption("展示 SQLite 中的 1688 以图搜图结果，并对照显示待执行的 Ozon 商品。这里的完成状态统一表示：该条结果已经完成图搜任务并已导出/入库。")

    if not repository.is_configured:
        st.warning("未配置 `SQLITE_PATH`，请先配置 SQLite 数据文件路径。")
        return

    reviewed_products = batch_repository.list_completed_products(include_alibaba_processed=True)
    pending_source_products = [item for item in reviewed_products if not item.get("alibaba_processed")]
    processed_source_products = [item for item in reviewed_products if item.get("alibaba_processed")]

    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    metric_col1.metric("图搜结果数", repository.count_results(completion_status="all"))
    metric_col2.metric("已完成图搜结果", repository.count_results(completion_status="completed"))
    metric_col3.metric("已处理Ozon商品", len(processed_source_products))
    metric_col4.metric("待执行Ozon商品", len(pending_source_products))

    tab1, tab2 = st.tabs(["图搜结果", "待执行商品"])

    with tab1:
        filter_col1, filter_col2, filter_col3 = st.columns([2.6, 1.4, 1.2])
        with filter_col1:
            keyword_filter = st.text_input("按关键词过滤", key="alibaba_result_keyword_filter")
        with filter_col2:
            status_label = st.selectbox(
                "按任务完成状态过滤",
                options=[STATUS_LABELS["all"], STATUS_LABELS["pending"], STATUS_LABELS["completed"]],
                index=2,
                key="alibaba_result_status_filter",
            )
        with filter_col3:
            page_size = st.selectbox("每页条数", options=[20, 50, 100, 200], index=1, key="alibaba_result_page_size")

        completion_status = {label: code for code, label in STATUS_LABELS.items()}[status_label]

        total_count = repository.count_results(keyword=keyword_filter, completion_status=completion_status)
        completed_count = repository.count_results(keyword=keyword_filter, completion_status="completed")
        pending_count = repository.count_results(keyword=keyword_filter, completion_status="pending")

        result_metric_col1, result_metric_col2, result_metric_col3 = st.columns(3)
        result_metric_col1.metric("结果总数", total_count)
        result_metric_col2.metric("已完成", completed_count)
        result_metric_col3.metric("未完成", pending_count)

        if total_count <= 0:
            st.info("当前筛选条件下没有 1688 以图搜图结果。请先执行 `python scripts/search_1688_by_saved_images.py`。")
        else:
            total_pages = max((total_count - 1) // int(page_size) + 1, 1)
            current_page = int(st.session_state.get("alibaba_result_current_page", 1))
            if current_page > total_pages:
                current_page = total_pages
            if current_page < 1:
                current_page = 1
            st.session_state["alibaba_result_current_page"] = current_page

            offset = (current_page - 1) * int(page_size)
            rows = repository.list_results(
                keyword=keyword_filter,
                completion_status=completion_status,
                limit=int(page_size),
                offset=offset,
            )
            all_filtered_rows = repository.list_results(
                keyword=keyword_filter,
                completion_status=completion_status,
            )

            selection_map = get_selection_map()
            page_rows = build_export_rows(rows, selection_map)
            editor_key = f"alibaba_result_editor_{completion_status}_{current_page}_{page_size}"
            frame = pd.DataFrame(page_rows)
            edited_frame = st.data_editor(
                frame,
                key=editor_key,
                hide_index=True,
                use_container_width=True,
                height=560,
                disabled=[column for column in frame.columns if column != "选择"],
                column_config={
                    "选择": st.column_config.CheckboxColumn("选择"),
                    "Ozon链接": st.column_config.LinkColumn("Ozon链接"),
                    "1688链接": st.column_config.LinkColumn("1688链接"),
                },
            )
            selection_map = sync_selection_state(edited_frame.to_dict(orient="records"))

            selected_result_ids = [int(result_id) for result_id, selected in selection_map.items() if selected and result_id.isdigit()]
            current_page_ids = [str(item.get("id") or "").strip() for item in rows if str(item.get("id") or "").strip()]
            selected_rows = [row for row in all_filtered_rows if int(row.get("id") or 0) in set(selected_result_ids)]

            action_col1, action_col2, action_col3, action_col4 = st.columns(4)
            with action_col1:
                if st.button("全选当前页", use_container_width=True, key="alibaba_result_select_page"):
                    for result_id in current_page_ids:
                        selection_map[result_id] = True
                    st.rerun()
            with action_col2:
                if st.button("取消当前页全选", use_container_width=True, key="alibaba_result_clear_page"):
                    for result_id in current_page_ids:
                        selection_map[result_id] = False
                    st.rerun()
            with action_col3:
                if st.button("标记选中为已完成", type="primary", use_container_width=True, disabled=not selected_result_ids, key="alibaba_result_mark_completed"):
                    result = repository.mark_results_completed(selected_result_ids)
                    st.success(f"已标记 {result.get('updated_count', 0)} 条图搜图结果为已完成。")
                    st.rerun()
            with action_col4:
                if st.button("删除选中结果", type="secondary", use_container_width=True, disabled=not selected_result_ids, key="alibaba_result_delete_selected"):
                    result = repository.delete_results(selected_result_ids)
                    for result_id in selected_result_ids:
                        selection_map.pop(str(result_id), None)
                    st.success(f"已删除 {result.get('deleted_count', 0)} 条图搜图结果。")
                    st.rerun()

            export_col1, export_col2, export_col3 = st.columns(3)
            with export_col1:
                st.download_button(
                    "导出当前页 XLSX",
                    data=to_excel_bytes(build_export_rows(rows, selection_map)),
                    file_name=f"alibaba1688_image_search_page_{current_page}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="alibaba_result_export_page",
                )
            with export_col2:
                st.download_button(
                    "导出当前筛选 XLSX",
                    data=to_excel_bytes(build_export_rows(all_filtered_rows, selection_map)),
                    file_name="alibaba1688_image_search_filtered.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="alibaba_result_export_filtered",
                )
            with export_col3:
                st.download_button(
                    "导出选中结果 XLSX",
                    data=to_excel_bytes(build_export_rows(selected_rows, selection_map)),
                    file_name="alibaba1688_image_search_selected.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    disabled=not selected_rows,
                    key="alibaba_result_export_selected",
                )

            st.markdown("---")
            page_bar_info_col, page_bar_nav_col = st.columns([2.2, 5.8])
            with page_bar_info_col:
                st.caption(f"当前筛选共 {total_count} 条结果，第 {current_page} / {total_pages} 页")
            with page_bar_nav_col:
                nav_cols = st.columns(9)
                if nav_cols[0].button("上一页", disabled=current_page <= 1, use_container_width=True, key="alibaba_result_prev"):
                    st.session_state["alibaba_result_current_page"] = current_page - 1
                    st.rerun()

                page_numbers = build_page_numbers(current_page=current_page, total_pages=total_pages)
                for index, page_number in enumerate(page_numbers, start=1):
                    label = f"[{page_number}]" if page_number == current_page else str(page_number)
                    if index < len(nav_cols) - 1 and nav_cols[index].button(label, use_container_width=True, key=f"alibaba_result_page_{page_number}"):
                        st.session_state["alibaba_result_current_page"] = page_number
                        st.rerun()

                if nav_cols[-1].button("下一页", disabled=current_page >= total_pages, use_container_width=True, key="alibaba_result_next"):
                    st.session_state["alibaba_result_current_page"] = current_page + 1
                    st.rerun()

    with tab2:
        filter_col1, filter_col2 = st.columns([2.6, 1.2])
        with filter_col1:
            source_keyword_filter = st.text_input("按批次关键词过滤", key="alibaba_pending_keyword_filter")
        with filter_col2:
            source_page_size = st.selectbox("每页条数", options=[20, 50, 100, 200], index=1, key="alibaba_pending_page_size")

        filtered_pending_rows = pending_source_products
        if source_keyword_filter.strip():
            keyword = source_keyword_filter.strip().lower()
            filtered_pending_rows = [
                item
                for item in pending_source_products
                if keyword in str(item.get("batch_keyword") or "").lower()
            ]

        pending_metric_col1, pending_metric_col2 = st.columns(2)
        pending_metric_col1.metric("待执行商品数", len(filtered_pending_rows))
        pending_metric_col2.metric("全部已图片去重商品", len(reviewed_products))

        if not filtered_pending_rows:
            st.info("当前筛选条件下没有待执行的 Ozon 商品。")
        else:
            total_pages = max((len(filtered_pending_rows) - 1) // int(source_page_size) + 1, 1)
            current_page = int(st.session_state.get("alibaba_pending_current_page", 1))
            if current_page > total_pages:
                current_page = total_pages
            if current_page < 1:
                current_page = 1
            st.session_state["alibaba_pending_current_page"] = current_page

            start_index = (current_page - 1) * int(source_page_size)
            end_index = start_index + int(source_page_size)
            page_rows = filtered_pending_rows[start_index:end_index]

            st.dataframe(
                build_pending_source_rows(page_rows),
                use_container_width=True,
                hide_index=True,
                height=560,
                column_config={
                    "Ozon链接": st.column_config.LinkColumn("Ozon链接"),
                },
            )

            st.download_button(
                "导出当前筛选待执行商品 XLSX",
                data=to_excel_bytes(build_pending_source_rows(filtered_pending_rows)),
                file_name="pending_ozon_products_for_1688.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="alibaba_pending_export",
            )

            st.markdown("---")
            page_bar_info_col, page_bar_nav_col = st.columns([2.2, 5.8])
            with page_bar_info_col:
                st.caption(f"当前筛选共 {len(filtered_pending_rows)} 个待执行商品，第 {current_page} / {total_pages} 页")
            with page_bar_nav_col:
                nav_cols = st.columns(9)
                if nav_cols[0].button("上一页", disabled=current_page <= 1, use_container_width=True, key="alibaba_pending_prev"):
                    st.session_state["alibaba_pending_current_page"] = current_page - 1
                    st.rerun()

                page_numbers = build_page_numbers(current_page=current_page, total_pages=total_pages)
                for index, page_number in enumerate(page_numbers, start=1):
                    label = f"[{page_number}]" if page_number == current_page else str(page_number)
                    if index < len(nav_cols) - 1 and nav_cols[index].button(label, use_container_width=True, key=f"alibaba_pending_page_{page_number}"):
                        st.session_state["alibaba_pending_current_page"] = page_number
                        st.rerun()

                if nav_cols[-1].button("下一页", disabled=current_page >= total_pages, use_container_width=True, key="alibaba_pending_next"):
                    st.session_state["alibaba_pending_current_page"] = current_page + 1
                    st.rerun()


main()
