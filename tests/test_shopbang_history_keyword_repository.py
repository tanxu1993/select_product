"""Shopbang 历史页关键词仓储测试。"""

from __future__ import annotations

from config.settings import Settings
from ozon_selection.repositories.shopbang_history_keyword_repository import ShopbangHistoryKeywordRepository


def test_upsert_keywords_dedupes_by_keyword(tmp_path) -> None:
    """同一关键词重复写入时应按唯一键去重。"""

    settings = Settings(SQLITE_PATH=str(tmp_path / "shopbang_history_keywords.db"))
    repository = ShopbangHistoryKeywordRepository(settings=settings)

    result = repository.upsert_keywords(
        [
            {
                "keyword": "园艺工具",
                "avg_price": 1200,
                "source_page": 1,
                "source_endpoint": "https://plus.shopbang.cn/api/history",
                "price_min": 500,
                "price_max": 20000,
                "filters": {"min_avg_price": 500, "max_avg_price": 20000},
                "raw_payload": {"keyword": "园艺工具", "avgPrice": 1200},
            },
            {
                "keyword": "园艺工具",
                "avg_price": 1300,
                "source_page": 2,
                "source_endpoint": "https://plus.shopbang.cn/api/history",
                "price_min": 500,
                "price_max": 20000,
                "filters": {"min_avg_price": 500, "max_avg_price": 20000},
                "raw_payload": {"keyword": "园艺工具", "avgPrice": 1300},
            },
            {
                "keyword": "收纳盒",
                "avg_price": 560,
                "source_page": 2,
                "source_endpoint": "https://plus.shopbang.cn/api/history",
                "price_min": 500,
                "price_max": 20000,
                "filters": {"min_avg_price": 500, "max_avg_price": 20000},
                "raw_payload": {"keyword": "收纳盒", "avgPrice": 560},
            },
        ]
    )

    assert result["status"] == "saved"
    assert result["saved_count"] == 2

    rows = repository.list_keywords()
    assert [item["keyword"] for item in rows] == ["收纳盒", "园艺工具"]
    garden_row = next(item for item in rows if item["keyword"] == "园艺工具")
    assert garden_row["source_count"] == 2


def test_upsert_keywords_filters_out_of_range_avg_price(tmp_path) -> None:
    """平均价格不在 500-20000 之间的关键词不应入库。"""

    settings = Settings(SQLITE_PATH=str(tmp_path / "shopbang_history_keywords.db"))
    repository = ShopbangHistoryKeywordRepository(settings=settings)

    result = repository.upsert_keywords(
        [
            {"keyword": "有效关键词", "avg_price": 500, "raw_payload": {"avgPrice": 500}},
            {"keyword": "过低关键词", "avg_price": 499.99, "raw_payload": {"avgPrice": 499.99}},
            {"keyword": "过高关键词", "avg_price": 20000.01, "raw_payload": {"avgPrice": 20000.01}},
            {"keyword": "空价格关键词", "avg_price": None, "raw_payload": {"avgPrice": None}},
            {"keyword": "无效价格关键词", "avg_price": "abc", "raw_payload": {"avgPrice": "abc"}},
        ]
    )

    assert result["status"] == "saved"
    assert result["saved_count"] == 1
    assert result["filtered_out_count"] == 4

    rows = repository.list_keywords()
    assert [item["keyword"] for item in rows] == ["有效关键词"]
    assert rows[0]["avg_price"] == 500


def test_delete_out_of_range_keywords_removes_historical_rows(tmp_path) -> None:
    """应能清理历史库中超出价格范围的关键词。"""

    settings = Settings(SQLITE_PATH=str(tmp_path / "shopbang_history_keywords.db"))
    repository = ShopbangHistoryKeywordRepository(settings=settings)

    repository.upsert_keywords(
        [
            {"keyword": "保留关键词", "avg_price": 1500, "raw_payload": {"avgPrice": 1500}},
        ]
    )
    repository.ensure_schema()
    with repository.client.connect() as connection:
        connection.execute(
            """
            insert into shopbang_history_keywords (
                keyword,
                avg_price,
                source_count,
                filters_json,
                raw_payload
            ) values (?, ?, ?, ?, ?)
            """,
            ("历史脏数据", 300, 1, "{}", "{}"),
        )
        connection.commit()

    result = repository.delete_out_of_range_keywords()

    assert result["status"] == "deleted"
    assert result["deleted_count"] == 1
    assert [item["keyword"] for item in repository.list_keywords()] == ["保留关键词"]


def test_delete_keywords_by_fragments_removes_matching_rows(tmp_path) -> None:
    """应能按关键词片段清理历史数据。"""

    settings = Settings(SQLITE_PATH=str(tmp_path / "shopbang_history_keywords.db"))
    repository = ShopbangHistoryKeywordRepository(settings=settings)
    repository.upsert_keywords(
        [
            {"keyword": "园艺工具", "avg_price": 1200, "raw_payload": {"avgPrice": 1200}},
            {"keyword": "女士单肩包", "avg_price": 1400, "raw_payload": {"avgPrice": 1400}},
            {"keyword": "男士双肩包", "avg_price": 1600, "raw_payload": {"avgPrice": 1600}},
        ]
    )

    result = repository.delete_keywords_by_fragments(["包", "背包"])

    assert result["status"] == "deleted"
    assert result["deleted_count"] == 2
    assert [item["keyword"] for item in repository.list_keywords()] == ["园艺工具"]


def test_delete_keywords_by_fragments_can_match_raw_payload(tmp_path) -> None:
    """应能按 raw_payload 中的中文字段清理历史数据。"""

    settings = Settings(SQLITE_PATH=str(tmp_path / "shopbang_history_keywords.db"))
    repository = ShopbangHistoryKeywordRepository(settings=settings)
    repository.upsert_keywords(
        [
            {"keyword": "футболка женская оверсайз", "avg_price": 790, "raw_payload": {"zhText": "女衬衫"}},
            {"keyword": "садовый набор", "avg_price": 1200, "raw_payload": {"zhText": "园艺工具"}},
        ]
    )

    result = repository.delete_keywords_by_fragments(["衬衫"], include_raw_payload=True)

    assert result["status"] == "deleted"
    assert result["deleted_count"] == 1
    assert [item["keyword"] for item in repository.list_keywords()] == ["садовый набор"]


def test_ensure_schema_adds_runtime_status_columns_for_existing_table(tmp_path) -> None:
    """旧表结构应自动补齐路径四需要的状态字段。"""

    settings = Settings(SQLITE_PATH=str(tmp_path / "shopbang_history_keywords.db"))
    repository = ShopbangHistoryKeywordRepository(settings=settings)

    with repository.client.connect() as connection:
        connection.execute(
            """
            create table if not exists shopbang_history_keywords (
                id integer primary key autoincrement,
                keyword text not null unique,
                avg_price numeric,
                source_page integer,
                source_endpoint text,
                price_min numeric,
                price_max numeric,
                source_count integer not null default 1,
                filters_json text not null default '{}',
                raw_payload text not null default '{}',
                first_seen_at text not null default current_timestamp,
                last_seen_at text not null default current_timestamp,
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp
            )
            """
        )
        connection.commit()

    repository.ensure_schema()

    with repository.client.connect() as connection:
        rows = connection.execute("pragma table_info(shopbang_history_keywords)").fetchall()
    column_names = {str(row["name"]) for row in rows}
    for column_name in ("used", "used_at", "last_used_status", "last_error", "use_count"):
        assert column_name in column_names


def test_pick_random_unused_keywords_and_mark_keyword_used(tmp_path) -> None:
    """路径四应能读取未爬关键词并标记为已爬。"""

    settings = Settings(SQLITE_PATH=str(tmp_path / "shopbang_history_keywords.db"))
    repository = ShopbangHistoryKeywordRepository(settings=settings)
    repository.upsert_keywords(
        [
            {"keyword": "园艺工具", "avg_price": 1200, "raw_payload": {"avgPrice": 1200}},
            {"keyword": "收纳盒", "avg_price": 900, "raw_payload": {"avgPrice": 900}},
        ]
    )

    picked_keywords = repository.pick_random_unused_keywords(limit=10)

    assert sorted(picked_keywords) == ["园艺工具", "收纳盒"]

    mark_result = repository.mark_keyword_used(keyword="园艺工具", status="success", error="")

    assert mark_result["status"] == "updated"

    all_rows = repository.list_keywords()
    used_row = next(item for item in all_rows if item["keyword"] == "园艺工具")
    unused_row = next(item for item in all_rows if item["keyword"] == "收纳盒")
    assert used_row["used"] is True
    assert used_row["last_used_status"] == "success"
    assert used_row["use_count"] == 1
    assert unused_row["used"] is False

    used_rows = repository.list_keywords(used_status="used")
    unused_rows = repository.list_keywords(used_status="unused")
    assert [item["keyword"] for item in used_rows] == ["园艺工具"]
    assert [item["keyword"] for item in unused_rows] == ["收纳盒"]
