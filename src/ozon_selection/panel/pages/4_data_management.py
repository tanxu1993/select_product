"""SQLite 数据管理中心。"""

from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path
from typing import Any
from typing import Callable

import pandas as pd
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings
from ozon_selection.repositories.ozon_batch_repository import OzonBatchRepository
from ozon_selection.repositories.ozon_reviewed_seller_repository import OzonReviewedSellerRepository
from ozon_selection.repositories.shopbang_hot_category_progress_repository import (
    ShopbangHotCategoryProgressRepository,
)


REVIEW_STATUS_LABELS = {
    "all": "全部",
    "completed": "已图片去重",
    "pending": "未图片去重",
}
SELLER_PRODUCT_STATUS_LABELS = {
    "all": "全部",
    "processed": "已处理",
    "processed_with_note": "已处理(带备注)",
}
PROGRESS_STATUS_LABELS = {
    "all": "全部",
    "completed": "已完成",
    "completed_no_entries": "已完成无数据",
    "failed": "失败",
    "skipped": "已跳过",
}


def to_excel_bytes(rows: list[dict[str, Any]], *, sheet_name: str) -> bytes:
    """把字典列表转换为 XLSX 二进制内容。"""

    output = BytesIO()
    frame = pd.DataFrame(rows)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name=sheet_name[:31] or "Sheet1")
    return output.getvalue()


def normalize_selection_key(value: object) -> str:
    """规范化勾选状态字典中的 key。"""

    return str(value or "").strip()


def get_selection_map(state_key: str) -> dict[str, bool]:
    """读取页面勾选状态。"""

    if state_key not in st.session_state:
        st.session_state[state_key] = {}
    return st.session_state[state_key]


def sync_selection_state(records: list[dict[str, Any]], *, state_key: str, identity_column: str) -> dict[str, bool]:
    """把当前页勾选状态同步到 session_state。"""

    selection_map = get_selection_map(state_key)
    for row in records:
        identity = normalize_selection_key(row.get(identity_column))
        if not identity:
            continue
        selection_map[identity] = bool(row.get("选择"))
    return selection_map


def build_page_numbers(*, current_page: int, total_pages: int, window: int = 2) -> list[int]:
    """构建传统分页条中展示的页码。"""

    start_page = max(1, current_page - window)
    end_page = min(total_pages, current_page + window)
    return list(range(start_page, end_page + 1))


def paginate_rows(rows: list[dict[str, Any]], *, state_key: str, page_size: int) -> tuple[list[dict[str, Any]], int, int, int]:
    """对列表按当前 session_state 中的页码分页。"""

    total_count = len(rows)
    total_pages = max((total_count - 1) // int(page_size) + 1, 1)
    current_page = int(st.session_state.get(state_key, 1))
    if current_page < 1:
        current_page = 1
    if current_page > total_pages:
        current_page = total_pages
    st.session_state[state_key] = current_page

    start_index = (current_page - 1) * int(page_size)
    end_index = start_index + int(page_size)
    return rows[start_index:end_index], current_page, total_pages, total_count


def render_pagination(*, page_state_key: str, current_page: int, total_pages: int, total_count: int, item_label: str) -> None:
    """渲染底部分页条。"""

    st.markdown("---")
    info_col, nav_col = st.columns([2.2, 5.8])
    with info_col:
        st.caption(f"当前筛选共 {total_count} 条{item_label}，第 {current_page} / {total_pages} 页")
    with nav_col:
        nav_cols = st.columns(9)
        if nav_cols[0].button("上一页", disabled=current_page <= 1, use_container_width=True, key=f"{page_state_key}_prev"):
            st.session_state[page_state_key] = current_page - 1
            st.rerun()

        page_numbers = build_page_numbers(current_page=current_page, total_pages=total_pages)
        for index, page_number in enumerate(page_numbers, start=1):
            label = f"[{page_number}]" if page_number == current_page else str(page_number)
            if index < len(nav_cols) - 1 and nav_cols[index].button(
                label,
                use_container_width=True,
                key=f"{page_state_key}_page_{page_number}",
            ):
                st.session_state[page_state_key] = page_number
                st.rerun()

        if nav_cols[-1].button("下一页", disabled=current_page >= total_pages, use_container_width=True, key=f"{page_state_key}_next"):
            st.session_state[page_state_key] = current_page + 1
            st.rerun()


def build_dataset_overview_rows() -> list[dict[str, str]]:
    """返回 SQLite 表用途说明。"""

    return [
        {
            "数据表": "ozon_keyword_batches",
            "中文含义": "关键词批次",
            "作用": "保存每轮 Ozon 关键词抓取任务，以及图片去重是否完成。",
            "建议查看页面": "Dashboard / 数据管理 / 图片去重",
        },
        {
            "数据表": "ozon_batch_products",
            "中文含义": "批次商品",
            "作用": "保存关键词抓到的商品明细、筛选结果、图片去重结果、1688 处理标记。",
            "建议查看页面": "数据管理 / 图片去重 / 1688以图搜图",
        },
        {
            "数据表": "alibaba_image_search_results",
            "中文含义": "1688以图搜图结果",
            "作用": "保存 1 个 Ozon 商品对应的多条 1688以图搜图结果及 AI 主图判断。",
            "建议查看页面": "1688以图搜图",
        },
        {
            "数据表": "ozon_keyword_pool",
            "中文含义": "Ozon 关键词池",
            "作用": "保存从 Shopbang 热卖商品回溯出来的上一级/上两级关键词及使用状态。",
            "建议查看页面": "Ozon 关键词池",
        },
        {
            "数据表": "shopbang_hot_category_progress",
            "中文含义": "Shopbang 类目续跑进度",
            "作用": "保存热卖类目翻页抓取进度，脚本中断后可续跑。",
            "建议查看页面": "数据管理",
        },
        {
            "数据表": "shopbang_history_keywords",
            "中文含义": "Shopbang 历史关键词",
            "作用": "保存 History 页面按商品平均价格筛出的关键词，两轮条件结果合并后按关键词去重。",
            "建议查看页面": "Shopbang 历史关键词",
        },
        {
            "数据表": "ozon_reviewed_seller_products",
            "中文含义": "店铺来源商品",
            "作用": "保存“根据商品找热门店铺”时已经处理过的商品，避免重复扫同一 SKU。",
            "建议查看页面": "数据管理",
        },
        {
            "数据表": "ozon_reviewed_seller_shops",
            "中文含义": "热门店铺列表",
            "作用": "保存有评论的跟卖店铺，以及“按店铺抓商品”的抓取状态和统计。",
            "建议查看页面": "Ozon 店铺列表",
        },
    ]


def build_batch_rows(rows: list[dict[str, Any]], selection_map: dict[str, bool]) -> list[dict[str, Any]]:
    """构建批次展示行。"""

    return [
        {
            "选择": bool(selection_map.get(normalize_selection_key(item.get("id")), False)),
            "批次ID": item.get("id"),
            "关键词": item.get("keyword"),
            "图片去重状态": "已图片去重" if item.get("status") == "completed" else "未图片去重",
            "商品数": item.get("total_products"),
            "保留数": item.get("dedupe_kept_count"),
            "搜索链接": item.get("search_url"),
            "采集时间": item.get("generated_at"),
            "图片去重完成时间": item.get("dedupe_completed_at"),
            "批次来源": item.get("source_manifest_path"),
        }
        for item in rows
    ]


def build_product_rows(rows: list[dict[str, Any]], selection_map: dict[str, bool]) -> list[dict[str, Any]]:
    """构建批次商品展示行。"""

    return [
        {
            "选择": bool(selection_map.get(normalize_selection_key(item.get("id")), False)),
            "记录ID": item.get("id"),
            "批次ID": item.get("batch_id"),
            "关键词": item.get("batch_keyword"),
            "图片去重状态": "已图片去重" if item.get("batch_status") == "completed" else "未图片去重",
            "SKU": item.get("source_product_id"),
            "标题": item.get("title"),
            "商品链接": item.get("source_url"),
            "主图路径": item.get("image_path"),
            "价格": item.get("price"),
            "月销量": item.get("monthly_sales"),
            "跟卖人数": item.get("sellers"),
            "评分": item.get("score"),
            "筛选通过": "是" if item.get("passed") else "否",
            "淘汰原因": " | ".join(item.get("fail_reasons") or []),
            "已完成1688处理": "是" if item.get("alibaba_processed") else "否",
            "采集时间": item.get("batch_generated_at"),
            "图片去重完成时间": item.get("batch_dedupe_completed_at"),
        }
        for item in rows
    ]


def build_seller_product_rows(rows: list[dict[str, Any]], selection_map: dict[str, bool]) -> list[dict[str, Any]]:
    """构建店铺来源商品展示行。"""

    return [
        {
            "选择": bool(selection_map.get(normalize_selection_key(item.get("id")), False)),
            "记录ID": item.get("id"),
            "商品SKU": item.get("source_product_id"),
            "标题": item.get("title"),
            "商品链接": item.get("source_url"),
            "起始页": item.get("start_url"),
            "列表页": item.get("listing_url"),
            "报价按钮文案": item.get("offer_button_text"),
            "跟卖人数": item.get("seller_count"),
            "处理状态": item.get("status"),
            "备注": item.get("note"),
            "处理时间": item.get("processed_at"),
            "更新时间": item.get("updated_at"),
        }
        for item in rows
    ]


def build_progress_rows(rows: list[dict[str, Any]], selection_map: dict[str, bool]) -> list[dict[str, Any]]:
    """构建类目进度展示行。"""

    return [
        {
            "选择": bool(selection_map.get(normalize_selection_key(item.get("category_name")), False)),
            "类目名称": item.get("category_name"),
            "请求体": json.dumps(item.get("request_body") or {}, ensure_ascii=False, sort_keys=True),
            "最近状态": item.get("last_status"),
            "已完成页码": item.get("last_completed_page"),
            "最近页大小": item.get("last_page_size"),
            "最近错误": item.get("last_error"),
            "最近运行时间": item.get("last_run_at"),
            "创建时间": item.get("created_at"),
            "更新时间": item.get("updated_at"),
        }
        for item in rows
    ]


def render_select_buttons(
    *,
    current_page_keys: list[str],
    selection_map: dict[str, bool],
    delete_callback: Callable[[], None],
    delete_all_callback: Callable[[], None],
    disable_delete_selected: bool,
    disable_delete_all: bool,
    key_prefix: str,
) -> None:
    """渲染勾选和删除操作按钮。"""

    action_col1, action_col2, action_col3, action_col4 = st.columns(4)
    with action_col1:
        if st.button("全选当前页", use_container_width=True, key=f"{key_prefix}_select_page"):
            for item_key in current_page_keys:
                selection_map[item_key] = True
            st.rerun()
    with action_col2:
        if st.button("取消当前页全选", use_container_width=True, key=f"{key_prefix}_clear_page"):
            for item_key in current_page_keys:
                selection_map[item_key] = False
            st.rerun()
    with action_col3:
        if st.button(
            "删除选中",
            type="secondary",
            use_container_width=True,
            disabled=disable_delete_selected,
            key=f"{key_prefix}_delete_selected",
        ):
            delete_callback()
    with action_col4:
        if st.button(
            "删除当前筛选全部",
            type="secondary",
            use_container_width=True,
            disabled=disable_delete_all,
            key=f"{key_prefix}_delete_filtered",
        ):
            delete_all_callback()


def main() -> None:
    """渲染 SQLite 数据管理中心。"""

    settings = get_settings()
    batch_repository = OzonBatchRepository(settings=settings)
    seller_repository = OzonReviewedSellerRepository(settings=settings)
    progress_repository = ShopbangHotCategoryProgressRepository(settings=settings)

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

    st.title("SQLite 数据管理中心")
    st.caption(
        "围绕当前 SQLite 数据表集中查看、导出和删除数据。店铺列表、关键词池、1688以图搜图保留独立页面；"
        "这里主要管理批次、批次商品、店铺来源商品和 Shopbang 类目续跑进度。"
    )

    if not batch_repository.is_configured:
        st.warning("未配置 `SQLITE_PATH`，请先配置 SQLite 数据文件路径后再使用管理系统。")
        return

    st.markdown("### 当前 SQLite 表说明")
    st.dataframe(
        build_dataset_overview_rows(),
        use_container_width=True,
        hide_index=True,
    )

    tab1, tab2, tab3, tab4 = st.tabs(["批次数据", "批次商品", "店铺来源商品", "类目续跑进度"])

    with tab1:
        filter_col1, filter_col2, filter_col3 = st.columns([2.2, 1.4, 1.2])
        with filter_col1:
            keyword_filter = st.text_input("按关键词过滤", key="dm_batch_keyword_filter")
        with filter_col2:
            review_status_label = st.selectbox(
                "按图片去重状态过滤",
                options=[REVIEW_STATUS_LABELS["all"], REVIEW_STATUS_LABELS["completed"], REVIEW_STATUS_LABELS["pending"]],
                index=0,
                key="dm_batch_review_status",
            )
        with filter_col3:
            page_size = st.selectbox("每页条数", options=[20, 50, 100, 200], index=1, key="dm_batch_page_size")

        review_status = {label: code for code, label in REVIEW_STATUS_LABELS.items()}[review_status_label]
        batch_rows_raw = batch_repository.list_batches(keyword=keyword_filter, include_completed=True)
        if review_status == "completed":
            batch_rows_raw = [item for item in batch_rows_raw if item.get("status") == "completed"]
        elif review_status == "pending":
            batch_rows_raw = [item for item in batch_rows_raw if item.get("status") != "completed"]

        metric_col1, metric_col2, metric_col3 = st.columns(3)
        metric_col1.metric("当前批次数", len(batch_rows_raw))
        metric_col2.metric("已图片去重", sum(1 for item in batch_rows_raw if item.get("status") == "completed"))
        metric_col3.metric("未图片去重", sum(1 for item in batch_rows_raw if item.get("status") != "completed"))

        if not batch_rows_raw:
            st.info("当前筛选条件下没有批次数据。")
        else:
            page_rows_raw, current_page, total_pages, total_count = paginate_rows(
                batch_rows_raw,
                state_key="dm_batch_current_page",
                page_size=int(page_size),
            )
            selection_map = get_selection_map("dm_batch_selection")
            page_rows = build_batch_rows(page_rows_raw, selection_map)
            frame = pd.DataFrame(page_rows)
            edited_frame = st.data_editor(
                frame,
                key=f"dm_batch_editor_{review_status}_{current_page}_{page_size}",
                hide_index=True,
                use_container_width=True,
                height=520,
                disabled=[column for column in frame.columns if column != "选择"],
                column_config={
                    "选择": st.column_config.CheckboxColumn("选择"),
                    "搜索链接": st.column_config.LinkColumn("搜索链接"),
                },
            )
            selection_map = sync_selection_state(
                edited_frame.to_dict(orient="records"),
                state_key="dm_batch_selection",
                identity_column="批次ID",
            )
            selected_batch_ids = [int(key) for key, selected in selection_map.items() if selected and key.isdigit()]
            current_page_keys = [normalize_selection_key(item.get("id")) for item in page_rows_raw]

            def delete_selected_batches() -> None:
                result = batch_repository.delete_batches(selected_batch_ids)
                for batch_id in selected_batch_ids:
                    selection_map.pop(normalize_selection_key(batch_id), None)
                st.success(
                    f"已删除 {result.get('deleted_batches', 0)} 个批次，同时删除 {result.get('deleted_products', 0)} 个商品。"
                )
                st.rerun()

            def delete_filtered_batches() -> None:
                batch_ids = [int(item.get("id")) for item in batch_rows_raw if int(item.get("id") or 0) > 0]
                result = batch_repository.delete_batches(batch_ids)
                for batch_id in batch_ids:
                    selection_map.pop(normalize_selection_key(batch_id), None)
                st.success(
                    f"已删除当前筛选中的 {result.get('deleted_batches', 0)} 个批次，同时删除 {result.get('deleted_products', 0)} 个商品。"
                )
                st.rerun()

            render_select_buttons(
                current_page_keys=current_page_keys,
                selection_map=selection_map,
                delete_callback=delete_selected_batches,
                delete_all_callback=delete_filtered_batches,
                disable_delete_selected=not selected_batch_ids,
                disable_delete_all=not batch_rows_raw,
                key_prefix="dm_batch",
            )
            st.download_button(
                "导出当前筛选批次 XLSX",
                data=to_excel_bytes(build_batch_rows(batch_rows_raw, selection_map), sheet_name="batches"),
                file_name="ozon_keyword_batches.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="dm_batch_export",
            )
            render_pagination(
                page_state_key="dm_batch_current_page",
                current_page=current_page,
                total_pages=total_pages,
                total_count=total_count,
                item_label="批次",
            )

    with tab2:
        filter_col1, filter_col2, filter_col3, filter_col4 = st.columns([2.2, 1.4, 1.4, 1.2])
        with filter_col1:
            keyword_filter = st.text_input("按关键词过滤", key="dm_product_keyword_filter")
        with filter_col2:
            review_status_label = st.selectbox(
                "按图片去重状态过滤",
                options=[REVIEW_STATUS_LABELS["all"], REVIEW_STATUS_LABELS["completed"], REVIEW_STATUS_LABELS["pending"]],
                index=0,
                key="dm_product_review_status",
            )
        with filter_col3:
            passed_label = st.selectbox("按筛选结果过滤", options=["全部", "筛选通过", "已淘汰"], index=0, key="dm_product_passed")
        with filter_col4:
            page_size = st.selectbox("每页条数", options=[20, 50, 100, 200], index=1, key="dm_product_page_size")

        review_status = {label: code for code, label in REVIEW_STATUS_LABELS.items()}[review_status_label]
        product_rows_raw = batch_repository.list_products_for_management(keyword=keyword_filter, review_status=review_status)
        if passed_label == "筛选通过":
            product_rows_raw = [item for item in product_rows_raw if item.get("passed")]
        elif passed_label == "已淘汰":
            product_rows_raw = [item for item in product_rows_raw if not item.get("passed")]

        metric_col1, metric_col2, metric_col3 = st.columns(3)
        metric_col1.metric("当前商品数", len(product_rows_raw))
        metric_col2.metric("筛选通过", sum(1 for item in product_rows_raw if item.get("passed")))
        metric_col3.metric("已完成1688处理", sum(1 for item in product_rows_raw if item.get("alibaba_processed")))

        if not product_rows_raw:
            st.info("当前筛选条件下没有批次商品数据。")
        else:
            page_rows_raw, current_page, total_pages, total_count = paginate_rows(
                product_rows_raw,
                state_key="dm_product_current_page",
                page_size=int(page_size),
            )
            selection_map = get_selection_map("dm_product_selection")
            page_rows = build_product_rows(page_rows_raw, selection_map)
            frame = pd.DataFrame(page_rows)
            edited_frame = st.data_editor(
                frame,
                key=f"dm_product_editor_{review_status}_{passed_label}_{current_page}_{page_size}",
                hide_index=True,
                use_container_width=True,
                height=560,
                disabled=[column for column in frame.columns if column != "选择"],
                column_config={
                    "选择": st.column_config.CheckboxColumn("选择"),
                    "商品链接": st.column_config.LinkColumn("商品链接"),
                },
            )
            selection_map = sync_selection_state(
                edited_frame.to_dict(orient="records"),
                state_key="dm_product_selection",
                identity_column="记录ID",
            )
            selected_product_ids = [int(key) for key, selected in selection_map.items() if selected and key.isdigit()]
            current_page_keys = [normalize_selection_key(item.get("id")) for item in page_rows_raw]

            def delete_selected_products() -> None:
                result = batch_repository.delete_products(selected_product_ids)
                for product_id in selected_product_ids:
                    selection_map.pop(normalize_selection_key(product_id), None)
                st.success(f"已删除 {result.get('deleted_count', 0)} 个商品。")
                st.rerun()

            def delete_filtered_products() -> None:
                product_ids = [int(item.get("id")) for item in product_rows_raw if int(item.get("id") or 0) > 0]
                result = batch_repository.delete_products(product_ids)
                for product_id in product_ids:
                    selection_map.pop(normalize_selection_key(product_id), None)
                st.success(f"已删除当前筛选中的 {result.get('deleted_count', 0)} 个商品。")
                st.rerun()

            render_select_buttons(
                current_page_keys=current_page_keys,
                selection_map=selection_map,
                delete_callback=delete_selected_products,
                delete_all_callback=delete_filtered_products,
                disable_delete_selected=not selected_product_ids,
                disable_delete_all=not product_rows_raw,
                key_prefix="dm_product",
            )
            st.download_button(
                "导出当前筛选商品 XLSX",
                data=to_excel_bytes(build_product_rows(product_rows_raw, selection_map), sheet_name="products"),
                file_name="ozon_batch_products.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="dm_product_export",
            )
            render_pagination(
                page_state_key="dm_product_current_page",
                current_page=current_page,
                total_pages=total_pages,
                total_count=total_count,
                item_label="商品",
            )

    with tab3:
        filter_col1, filter_col2, filter_col3 = st.columns([2.4, 1.4, 1.2])
        with filter_col1:
            query_filter = st.text_input("按 SKU / 标题 / URL 过滤", key="dm_seller_product_query")
        with filter_col2:
            status_label = st.selectbox(
                "按处理状态过滤",
                options=[
                    SELLER_PRODUCT_STATUS_LABELS["all"],
                    SELLER_PRODUCT_STATUS_LABELS["processed"],
                    SELLER_PRODUCT_STATUS_LABELS["processed_with_note"],
                ],
                index=0,
                key="dm_seller_product_status",
            )
        with filter_col3:
            page_size = st.selectbox("每页条数", options=[20, 50, 100, 200], index=1, key="dm_seller_product_page_size")

        seller_status = {label: code for code, label in SELLER_PRODUCT_STATUS_LABELS.items()}[status_label]
        seller_product_rows_raw = seller_repository.list_products(query=query_filter, status=seller_status)

        metric_col1, metric_col2, metric_col3 = st.columns(3)
        metric_col1.metric("当前商品数", len(seller_product_rows_raw))
        metric_col2.metric(
            "带备注处理",
            sum(1 for item in seller_product_rows_raw if item.get("status") == "processed_with_note"),
        )
        metric_col3.metric("累计跟卖人数", sum(int(item.get("seller_count") or 0) for item in seller_product_rows_raw))

        if not seller_product_rows_raw:
            st.info("当前筛选条件下没有店铺来源商品数据。")
        else:
            page_rows_raw, current_page, total_pages, total_count = paginate_rows(
                seller_product_rows_raw,
                state_key="dm_seller_product_current_page",
                page_size=int(page_size),
            )
            selection_map = get_selection_map("dm_seller_product_selection")
            page_rows = build_seller_product_rows(page_rows_raw, selection_map)
            frame = pd.DataFrame(page_rows)
            edited_frame = st.data_editor(
                frame,
                key=f"dm_seller_product_editor_{seller_status}_{current_page}_{page_size}",
                hide_index=True,
                use_container_width=True,
                height=560,
                disabled=[column for column in frame.columns if column != "选择"],
                column_config={
                    "选择": st.column_config.CheckboxColumn("选择"),
                    "商品链接": st.column_config.LinkColumn("商品链接"),
                    "起始页": st.column_config.LinkColumn("起始页"),
                    "列表页": st.column_config.LinkColumn("列表页"),
                },
            )
            selection_map = sync_selection_state(
                edited_frame.to_dict(orient="records"),
                state_key="dm_seller_product_selection",
                identity_column="记录ID",
            )
            selected_product_ids = [int(key) for key, selected in selection_map.items() if selected and key.isdigit()]
            current_page_keys = [normalize_selection_key(item.get("id")) for item in page_rows_raw]

            def delete_selected_seller_products() -> None:
                result = seller_repository.delete_products(selected_product_ids)
                for product_id in selected_product_ids:
                    selection_map.pop(normalize_selection_key(product_id), None)
                st.success(f"已删除 {result.get('deleted_count', 0)} 条店铺来源商品。")
                st.rerun()

            def delete_filtered_seller_products() -> None:
                product_ids = [int(item.get("id")) for item in seller_product_rows_raw if int(item.get("id") or 0) > 0]
                result = seller_repository.delete_products(product_ids)
                for product_id in product_ids:
                    selection_map.pop(normalize_selection_key(product_id), None)
                st.success(f"已删除当前筛选中的 {result.get('deleted_count', 0)} 条店铺来源商品。")
                st.rerun()

            render_select_buttons(
                current_page_keys=current_page_keys,
                selection_map=selection_map,
                delete_callback=delete_selected_seller_products,
                delete_all_callback=delete_filtered_seller_products,
                disable_delete_selected=not selected_product_ids,
                disable_delete_all=not seller_product_rows_raw,
                key_prefix="dm_seller_product",
            )
            st.download_button(
                "导出当前筛选店铺来源商品 XLSX",
                data=to_excel_bytes(build_seller_product_rows(seller_product_rows_raw, selection_map), sheet_name="seller_products"),
                file_name="ozon_reviewed_seller_products.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="dm_seller_product_export",
            )
            render_pagination(
                page_state_key="dm_seller_product_current_page",
                current_page=current_page,
                total_pages=total_pages,
                total_count=total_count,
                item_label="店铺来源商品",
            )

    with tab4:
        filter_col1, filter_col2, filter_col3 = st.columns([2.4, 1.4, 1.2])
        with filter_col1:
            query_filter = st.text_input("按类目名过滤", key="dm_progress_query")
        with filter_col2:
            status_label = st.selectbox(
                "按最近状态过滤",
                options=[
                    PROGRESS_STATUS_LABELS["all"],
                    PROGRESS_STATUS_LABELS["completed"],
                    PROGRESS_STATUS_LABELS["completed_no_entries"],
                    PROGRESS_STATUS_LABELS["failed"],
                    PROGRESS_STATUS_LABELS["skipped"],
                ],
                index=0,
                key="dm_progress_status",
            )
        with filter_col3:
            page_size = st.selectbox("每页条数", options=[20, 50, 100, 200], index=1, key="dm_progress_page_size")

        progress_status = {label: code for code, label in PROGRESS_STATUS_LABELS.items()}[status_label]
        progress_rows_raw = progress_repository.list_progress(query=query_filter, status=progress_status)

        metric_col1, metric_col2, metric_col3 = st.columns(3)
        metric_col1.metric("当前类目数", len(progress_rows_raw))
        metric_col2.metric("已完成", sum(1 for item in progress_rows_raw if item.get("last_status") == "completed"))
        metric_col3.metric("失败", sum(1 for item in progress_rows_raw if item.get("last_status") == "failed"))

        if not progress_rows_raw:
            st.info("当前筛选条件下没有 Shopbang 类目续跑进度。")
        else:
            page_rows_raw, current_page, total_pages, total_count = paginate_rows(
                progress_rows_raw,
                state_key="dm_progress_current_page",
                page_size=int(page_size),
            )
            selection_map = get_selection_map("dm_progress_selection")
            page_rows = build_progress_rows(page_rows_raw, selection_map)
            frame = pd.DataFrame(page_rows)
            edited_frame = st.data_editor(
                frame,
                key=f"dm_progress_editor_{progress_status}_{current_page}_{page_size}",
                hide_index=True,
                use_container_width=True,
                height=520,
                disabled=[column for column in frame.columns if column != "选择"],
                column_config={
                    "选择": st.column_config.CheckboxColumn("选择"),
                },
            )
            selection_map = sync_selection_state(
                edited_frame.to_dict(orient="records"),
                state_key="dm_progress_selection",
                identity_column="类目名称",
            )
            selected_categories = [key for key, selected in selection_map.items() if selected]
            current_page_keys = [normalize_selection_key(item.get("category_name")) for item in page_rows_raw]

            def delete_selected_progress() -> None:
                result = progress_repository.delete_progress(selected_categories)
                for category_name in selected_categories:
                    selection_map.pop(normalize_selection_key(category_name), None)
                st.success(f"已删除 {result.get('deleted_count', 0)} 条类目续跑进度。")
                st.rerun()

            def delete_filtered_progress() -> None:
                category_names = [str(item.get("category_name") or "").strip() for item in progress_rows_raw if str(item.get("category_name") or "").strip()]
                result = progress_repository.delete_progress(category_names)
                for category_name in category_names:
                    selection_map.pop(normalize_selection_key(category_name), None)
                st.success(f"已删除当前筛选中的 {result.get('deleted_count', 0)} 条类目续跑进度。")
                st.rerun()

            render_select_buttons(
                current_page_keys=current_page_keys,
                selection_map=selection_map,
                delete_callback=delete_selected_progress,
                delete_all_callback=delete_filtered_progress,
                disable_delete_selected=not selected_categories,
                disable_delete_all=not progress_rows_raw,
                key_prefix="dm_progress",
            )
            st.download_button(
                "导出当前筛选类目进度 XLSX",
                data=to_excel_bytes(build_progress_rows(progress_rows_raw, selection_map), sheet_name="category_progress"),
                file_name="shopbang_hot_category_progress.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="dm_progress_export",
            )
            render_pagination(
                page_state_key="dm_progress_current_page",
                current_page=current_page,
                total_pages=total_pages,
                total_count=total_count,
                item_label="类目进度",
            )


main()
