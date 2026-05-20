"""SQLite 中 Ozon 关键词池状态页。"""

from __future__ import annotations

import json
import re
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
from ozon_selection.api.clients.openai_client import OpenAIClient
from ozon_selection.repositories.ozon_keyword_pool_repository import OzonKeywordPoolRepository


LEVEL_LABELS = {
    "all": "全部",
    "parent": "上一级类目关键词",
    "grandparent": "上两级类目关键词",
}
SELECTION_STATE_KEY = "keyword_pool_selection_map"
TRANSLATION_CACHE_FILE = "keyword_category_translation_cache.json"


def get_selection_map() -> dict[str, bool]:
    """读取关键词勾选状态。"""

    if SELECTION_STATE_KEY not in st.session_state:
        st.session_state[SELECTION_STATE_KEY] = {}
    return st.session_state[SELECTION_STATE_KEY]


def to_excel_bytes(rows: list[dict], *, sheet_name: str) -> bytes:
    """把字典列表转换为 XLSX 二进制内容。"""

    output = BytesIO()
    frame = pd.DataFrame(rows)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name=sheet_name[:31] or "Sheet1")
    return output.getvalue()


def build_export_rows(
    rows: list[dict],
    selection_map: dict[str, bool],
    translation_map: dict[str, str],
) -> list[dict]:
    """构建关键词池导出行。"""

    return [
        {
            "选择": bool(selection_map.get(str(item.get("keyword") or "").strip(), False)),
            "关键词": item.get("keyword"),
            "关键词层级": LEVEL_LABELS.get(str(item.get("keyword_level") or "").strip(), item.get("keyword_level") or "-"),
            "当前类目": get_translated_category(item.get("current_category"), translation_map),
            "上一级类目": get_translated_category(item.get("parent_category"), translation_map),
            "上两级类目": get_translated_category(item.get("grandparent_category"), translation_map),
            "来源商品标题": item.get("source_product_title"),
            "来源商品链接": item.get("source_product_url"),
            "来源商品SKU": item.get("source_product_sku"),
            "来源批次类型": item.get("source_batch_type"),
            "已使用": "是" if item.get("used") else "否",
            "使用次数": item.get("use_count"),
            "最近执行状态": item.get("last_used_status"),
            "最近错误": item.get("last_error"),
            "最近使用时间": item.get("used_at"),
            "创建时间": item.get("created_at"),
            "更新时间": item.get("updated_at"),
        }
        for item in rows
    ]


def sync_selection_state(records: list[dict]) -> dict[str, bool]:
    """把当前页勾选同步回 session_state。"""

    selection_map = get_selection_map()
    for row in records:
        keyword = str(row.get("关键词") or "").strip()
        if not keyword:
            continue
        selection_map[keyword] = bool(row.get("选择"))
    return selection_map


def build_page_numbers(*, current_page: int, total_pages: int, window: int = 2) -> list[int]:
    """构建传统分页条中展示的页码。"""

    start_page = max(1, current_page - window)
    end_page = min(total_pages, current_page + window)
    return list(range(start_page, end_page + 1))


def get_translation_cache_path() -> Path:
    """返回类目中文翻译缓存文件路径。"""

    settings = get_settings()
    return settings.processed_data_path / TRANSLATION_CACHE_FILE


def load_translation_cache() -> dict[str, str]:
    """读取本地翻译缓存。"""

    cache_path = get_translation_cache_path()
    if not cache_path.exists():
        return {}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        str(key).strip(): str(value).strip()
        for key, value in payload.items()
        if str(key).strip() and str(value).strip()
    }


def save_translation_cache(cache: dict[str, str]) -> None:
    """保存本地翻译缓存。"""

    cache_path = get_translation_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def looks_like_chinese(text: str) -> bool:
    """判断文本是否已包含中文。"""

    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def get_translated_category(value: object, translation_map: dict[str, str]) -> str:
    """读取中文类目文案。"""

    text = str(value or "").strip()
    if not text:
        return ""
    translated = str(translation_map.get(text) or "").strip()
    return translated or text


def collect_category_texts(rows: list[dict]) -> list[str]:
    """收集当前页面涉及的类目文本。"""

    values: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for field_name in ("current_category", "parent_category", "grandparent_category"):
            text = str(row.get(field_name) or "").strip()
            if not text or text in seen or looks_like_chinese(text):
                continue
            seen.add(text)
            values.append(text)
    return values


def chunk_items(items: list[str], size: int) -> list[list[str]]:
    """把列表切成固定大小的块。"""

    return [items[index:index + size] for index in range(0, len(items), size)]


def extract_json_object(text: str) -> dict[str, str]:
    """从模型输出中提取 JSON 对象。"""

    normalized = str(text or "").strip()
    if not normalized:
        return {}
    try:
        payload = json.loads(normalized)
        if isinstance(payload, dict):
            return {str(key): str(value) for key, value in payload.items()}
    except Exception:
        pass

    matched = re.search(r"\{.*\}", normalized, re.S)
    if not matched:
        return {}
    try:
        payload = json.loads(matched.group(0))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items()}


def translate_category_batch(texts: list[str]) -> dict[str, str]:
    """调用 OpenAI 批量把类目翻译成中文。"""

    settings = get_settings()
    if not texts:
        return {}
    if not settings.openai_api_key.strip():
        return {text: text for text in texts}

    client = OpenAIClient(settings=settings)
    system_prompt = (
        "你是跨境电商类目翻译助手。"
        "把输入的俄文或英文商品类目短语翻译成简洁准确的中文。"
        "只返回 JSON 对象，不要输出解释，不要输出 Markdown。"
    )
    user_prompt = (
        "请把下面这些类目短语翻译成中文，返回格式必须是 JSON 对象，"
        "键为原文，值为中文：\n"
        f"{json.dumps(texts, ensure_ascii=False)}"
    )
    try:
        response = client.stream_chat_completion(
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=1200,
        )
        payload = extract_json_object(response.output_text)
    except Exception:
        payload = {}

    result: dict[str, str] = {}
    for text in texts:
        translated = str(payload.get(text) or "").strip()
        result[text] = translated or text
    return result


def build_translation_map(rows: list[dict]) -> dict[str, str]:
    """构建类目中文映射，并写入本地缓存。"""

    cache = load_translation_cache()
    texts = collect_category_texts(rows)
    missing_texts = [text for text in texts if not str(cache.get(text) or "").strip()]
    if missing_texts:
        with st.spinner("正在把类目俄文翻译成中文..."):
            for batch in chunk_items(missing_texts, size=40):
                cache.update(translate_category_batch(batch))
        save_translation_cache(cache)
    return cache


def main() -> None:
    """渲染关键词池页面。"""

    settings = get_settings()
    repository = OzonKeywordPoolRepository(settings=settings)

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

    st.title("Ozon 关键词池")
    st.caption("展示 Shopbang 热销抽取后写入 SQLite 的结构化关键词，以及每个关键词的使用状态。")

    if not repository.is_configured:
        st.warning("未配置 `SQLITE_PATH`，请先配置 SQLite 数据文件路径。")
        return

    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns([2.4, 1.4, 1.6, 1.2])
    with filter_col1:
        keyword_filter = st.text_input("按关键词过滤")
    with filter_col2:
        used_status_label = st.selectbox("按使用状态过滤", options=["全部", "未使用", "已使用"], index=0)
    with filter_col3:
        level_label = st.selectbox(
            "按层级过滤",
            options=[LEVEL_LABELS["all"], LEVEL_LABELS["parent"], LEVEL_LABELS["grandparent"]],
            index=0,
        )
    with filter_col4:
        page_size = st.selectbox("每页条数", options=[20, 50, 100, 200], index=1)

    used_status = {"全部": "all", "未使用": "unused", "已使用": "used"}[used_status_label]
    level_filter = {label: code for code, label in LEVEL_LABELS.items()}[level_label]

    rows = repository.list_keywords(keyword=keyword_filter, used_status=used_status)
    if level_filter != "all":
        rows = [item for item in rows if item.get("keyword_level") == level_filter]
    translation_map = build_translation_map(rows)

    total_count = len(rows)
    used_count = sum(1 for item in rows if item.get("used"))
    unused_count = total_count - used_count

    metric_col1, metric_col2, metric_col3 = st.columns(3)
    metric_col1.metric("关键词数", total_count)
    metric_col2.metric("已使用", used_count)
    metric_col3.metric("未使用", unused_count)

    if not rows:
        st.info("当前筛选条件下没有关键词数据。")
        return

    total_pages = max((total_count - 1) // int(page_size) + 1, 1)
    current_page = int(st.session_state.get("keyword_pool_current_page", 1))
    if current_page > total_pages:
        current_page = total_pages
    if current_page < 1:
        current_page = 1
    st.session_state["keyword_pool_current_page"] = current_page

    start_index = (current_page - 1) * int(page_size)
    end_index = start_index + int(page_size)
    page_rows_raw = rows[start_index:end_index]

    selection_map = get_selection_map()
    page_rows = build_export_rows(page_rows_raw, selection_map, translation_map)
    editor_key = f"keyword_pool_editor_{used_status}_{level_filter}_{current_page}_{page_size}"
    edited_frame = st.data_editor(
        pd.DataFrame(page_rows),
        key=editor_key,
        hide_index=True,
        use_container_width=True,
        height=560,
        disabled=[column for column in pd.DataFrame(page_rows).columns if column != "选择"],
        column_config={
            "选择": st.column_config.CheckboxColumn("选择"),
            "来源商品链接": st.column_config.LinkColumn("来源商品链接"),
        },
    )
    selection_map = sync_selection_state(edited_frame.to_dict(orient="records"))

    selected_keywords = [keyword for keyword, selected in selection_map.items() if selected]
    current_page_keywords = [str(item.get("keyword") or "").strip() for item in page_rows_raw if str(item.get("keyword") or "").strip()]

    action_col1, action_col2, action_col3, action_col4 = st.columns(4)
    with action_col1:
        if st.button("全选当前页", use_container_width=True):
            for keyword in current_page_keywords:
                selection_map[keyword] = True
            st.rerun()
    with action_col2:
        if st.button("取消当前页全选", use_container_width=True):
            for keyword in current_page_keywords:
                selection_map[keyword] = False
            st.rerun()
    with action_col3:
        if st.button("删除选中关键词", type="secondary", use_container_width=True, disabled=not selected_keywords):
            result = repository.delete_keywords(selected_keywords)
            for keyword in selected_keywords:
                selection_map.pop(keyword, None)
            st.success(f"已删除 {result.get('deleted_count', 0)} 个关键词。")
            st.rerun()
    with action_col4:
        if st.button("删除当前筛选全部", type="secondary", use_container_width=True, disabled=not rows):
            result = repository.delete_keywords([str(item.get("keyword") or "").strip() for item in rows])
            for item in rows:
                selection_map.pop(str(item.get("keyword") or "").strip(), None)
            st.success(f"已删除当前筛选结果中的 {result.get('deleted_count', 0)} 个关键词。")
            st.rerun()

    export_rows = build_export_rows(rows, selection_map, translation_map)
    st.download_button(
        "导出关键词池 XLSX",
        data=to_excel_bytes(export_rows, sheet_name="keyword_pool"),
        file_name="ozon_keyword_pool.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    st.markdown("---")
    page_bar_info_col, page_bar_nav_col = st.columns([2.2, 5.8])
    with page_bar_info_col:
        st.caption(f"当前筛选共 {total_count} 个关键词，第 {current_page} / {total_pages} 页")
    with page_bar_nav_col:
        nav_cols = st.columns(9)
        if nav_cols[0].button("上一页", disabled=current_page <= 1, use_container_width=True):
            st.session_state["keyword_pool_current_page"] = current_page - 1
            st.rerun()

        page_numbers = build_page_numbers(current_page=current_page, total_pages=total_pages)
        for index, page_number in enumerate(page_numbers, start=1):
            label = f"[{page_number}]" if page_number == current_page else str(page_number)
            if index < len(nav_cols) - 1 and nav_cols[index].button(label, use_container_width=True):
                st.session_state["keyword_pool_current_page"] = page_number
                st.rerun()

        if nav_cols[-1].button("下一页", disabled=current_page >= total_pages, use_container_width=True):
            st.session_state["keyword_pool_current_page"] = current_page + 1
            st.rerun()


main()
