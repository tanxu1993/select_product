"""Shopbang 历史页关键词采集逻辑测试。"""

from __future__ import annotations

from ozon_selection.collectors.ozon.shopbang_history_keyword_collector import ShopbangHistoryKeywordCollector


def test_extract_keyword_items_from_response_prefers_keyword_like_list() -> None:
    """应递归命中包含关键词字段的列表。"""

    payload = {
        "code": 0,
        "data": {
            "summary": {"count": 2},
            "list": [
                {"keyword": "园艺工具", "avgPrice": 1200},
                {"keyword": "收纳盒", "avgPrice": 650},
            ],
        },
        "other": {
            "list": [
                {"name": "A"},
            ]
        },
    }

    result = ShopbangHistoryKeywordCollector.extract_keyword_items_from_response(payload)

    assert result == payload["data"]["list"]


def test_build_keyword_record_filters_excluded_keywords() -> None:
    """命中排除词的关键词不应入库。"""

    collector = ShopbangHistoryKeywordCollector()

    record = collector.build_keyword_record(
        {"keyword": "手机支架", "avgPrice": 66},
        source_page=1,
        source_endpoint="https://plus.shopbang.cn/api/history",
        min_avg_price=500,
        max_avg_price=20000,
        excluded_keywords=collector.build_excluded_keyword_fragments(),
    )

    assert record is None


def test_build_keyword_record_filters_apparel_keywords() -> None:
    """服饰鞋靴相关关键词应按默认规则过滤。"""

    collector = ShopbangHistoryKeywordCollector()
    excluded_keywords = collector.build_excluded_keyword_fragments()

    for keyword in (
        "女衬衫",
        "女孩的夏装",
        "男式内裤",
        "女式马裤",
        "女式沙滩束腰外衣",
        "夏季大码女装",
        "男式短上衣",
        "运动鞋",
        "高跟鞋",
        "女鞋",
        "女式经典长裤",
        "男式经典长裤",
        "夏季女裤",
        "女式运动裤",
        "男式人字拖",
        "夏季女鞋",
        "夏季男式运动裤",
        "女式家庭服",
        "夏季运动服",
        "легинсы женские",
        "女军团",
        "男式棉袜",
        "носки мужские набор хлопок",
        "сарафан женский",
        "女萨拉凡",
        "кросовки",
        "克罗索夫基",
        "сарафан",
        "萨拉凡",
        "бомбер женский",
        "女炸弹手",
        "носки мужские короткие",
        "男式短袜",
        "кроксы мужские",
        "男克罗克斯",
        "кроксы женские",
        "雌克罗克斯",
        "шлепки женские",
        "шлепки женские летние",
        "女子马球",
        "束身衣",
        "夏季芭蕾舞女郎",
        "女开衫",
        "米莉女式",
        "女式晚装",
        "女式紧身胸衣",
        "女式节日礼服",
        "男式马球",
        "女芭蕾舞演员",
        "女夏日",
    ):
        record = collector.build_keyword_record(
            {"keyword": keyword, "avgPrice": 800},
            source_page=1,
            source_endpoint="https://plus.shopbang.cn/api/history",
            min_avg_price=500,
            max_avg_price=20000,
            excluded_keywords=excluded_keywords,
        )
        assert record is None, keyword

def test_build_keyword_record_keeps_normal_keyword() -> None:
    """普通关键词应正常生成结构化记录。"""

    collector = ShopbangHistoryKeywordCollector()

    record = collector.build_keyword_record(
        {"keyword": "园艺工具", "avgPrice": "1,280"},
        source_page=3,
        source_endpoint="https://plus.shopbang.cn/api/history",
        min_avg_price=500,
        max_avg_price=20000,
        excluded_keywords=collector.build_excluded_keyword_fragments(),
    )

    assert record is not None
    assert record["keyword"] == "园艺工具"
    assert record["avg_price"] == 1280
    assert record["source_page"] == 3


def test_build_keyword_record_filters_by_zh_text() -> None:
    """即使俄文关键词未命中，中文字段命中服饰词也应过滤。"""

    collector = ShopbangHistoryKeywordCollector()
    record = collector.build_keyword_record(
        {"keyword": "футболка женская оверсайз", "zhText": "女衬衫", "avgPrice": 790},
        source_page=1,
        source_endpoint="https://plus.shopbang.cn/api/history",
        min_avg_price=500,
        max_avg_price=20000,
        excluded_keywords=collector.build_excluded_keyword_fragments(),
    )

    assert record is None


def test_build_keyword_record_filters_more_apparel_by_zh_text() -> None:
    """中文字段中的更多服饰鞋类词也应被过滤。"""

    collector = ShopbangHistoryKeywordCollector()
    excluded_keywords = collector.build_excluded_keyword_fragments()

    for zh_text in (
        "女鞋",
        "女夏季",
        "女式经典长裤",
        "男式经典长裤",
        "女式夏季大甩卖",
        "夏季女裤",
        "女式运动裤",
        "男式人字拖",
        "夏季女鞋",
        "夏季男式运动裤",
        "女式家庭服",
        "夏季运动服",
        "легинсы женские",
        "女军团",
        "男式棉袜",
        "носки мужские набор хлопок",
        "сарафан женский",
        "女萨拉凡",
        "кросовки",
        "克罗索夫基",
        "сарафан",
        "萨拉凡",
        "бомбер женский",
        "女炸弹手",
        "носки мужские короткие",
        "男式短袜",
        "кроксы мужские",
        "男克罗克斯",
        "кроксы женские",
        "雌克罗克斯",
        "女子马球",
        "束身衣",
        "夏季芭蕾舞女郎",
        "女开衫",
        "米莉女式",
        "女式晚装",
        "女式紧身胸衣",
        "女式节日礼服",
        "男式马球",
        "女芭蕾舞演员",
        "女夏日",
    ):
        record = collector.build_keyword_record(
            {"keyword": "random keyword", "zhText": zh_text, "avgPrice": 790},
            source_page=1,
            source_endpoint="https://plus.shopbang.cn/api/history",
            min_avg_price=500,
            max_avg_price=20000,
            excluded_keywords=excluded_keywords,
        )
        assert record is None, zh_text


def test_read_page_helpers_support_shopbang_payload() -> None:
    """应能从 Shopbang 请求体中读出页码和每页条数。"""

    payload = {
        "pageNo": 3,
        "pageSize": 10,
        "condition": [{"searchType": "avgCaRub", "relative": 2, "searchInp": "500"}],
    }

    assert ShopbangHistoryKeywordCollector.read_page_no(payload) == 3
    assert ShopbangHistoryKeywordCollector.read_page_size(payload) == 10
