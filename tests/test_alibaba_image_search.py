"""1688 浏览器图搜图纯逻辑测试。"""

from config.settings import Settings
from ozon_selection.collectors.alibaba.image_search import Alibaba1688ImageSearchBrowser


def test_parse_weight_grams_supports_kg_and_g() -> None:
    """确保重量文本能统一换算成克。"""

    assert Alibaba1688ImageSearchBrowser.parse_weight_grams("0.75kg") == 750.0
    assert Alibaba1688ImageSearchBrowser.parse_weight_grams("500克") == 500.0


def test_is_punish_page_detects_risk_redirect() -> None:
    """确保能识别 1688 风控跳转页。"""

    class DummyPage:
        url = "https://s.1688.com//youyuan/index.htm/_____tmd_____/punish?x5secdata=demo"

    assert Alibaba1688ImageSearchBrowser.is_punish_page(DummyPage()) is True


def test_pick_unit_price_text_prefers_price_per_piece() -> None:
    """确保能优先识别单价文本。"""

    value = Alibaba1688ImageSearchBrowser.pick_unit_price_text(
        ["批发价 12.5元/件", "其他文案"],
        "正文无单价",
    )
    assert value == "12.5元/件"


def test_pick_weight_text_reads_labeled_weight() -> None:
    """确保能从正文中提取重量字段。"""

    value = Alibaba1688ImageSearchBrowser.pick_weight_text("商品信息 重量: 1.2kg 颜色: 黑色")
    assert value == "1.2kg"


def test_extract_detail_snapshot_includes_attributes() -> None:
    """确保 1688 详情页快照会保留属性列表。"""

    class DummyPage:
        url = "https://detail.1688.com/offer/1.html"

        def evaluate(self, _script):
            return {
                "title": "demo",
                "body_text": "重量: 1.2kg",
                "price_candidates": ["12.5元/件"],
                "attributes": [{"key": "颜色", "value": "红色"}],
                "final_url": self.url,
            }

    result = Alibaba1688ImageSearchBrowser().extract_detail_snapshot(DummyPage())

    assert result["attributes"] == [{"key": "颜色", "value": "红色"}]


def test_clean_attributes_filters_placeholder_and_duplicate_values() -> None:
    """确保属性清洗会去掉占位值和语义重复项。"""

    result = Alibaba1688ImageSearchBrowser.clean_attributes(
        [
            {"key": "品牌", "value": "---"},
            {"key": "颜色", "value": "红色、蓝色"},
            {"key": "颜色", "value": "红色,蓝色"},
            {"key": "材质", "value": "硅胶"},
        ]
    )

    assert result == [
        {"key": "颜色", "value": "红色、蓝色"},
        {"key": "材质", "value": "硅胶"},
    ]


def test_clean_attributes_filters_generic_header_rows() -> None:
    """确保属性清洗会去掉表头型噪声字段。"""

    result = Alibaba1688ImageSearchBrowser.clean_attributes(
        [
            {"key": "颜色", "value": "规格"},
            {"key": "规格", "value": "5.5cm-10条袋装"},
        ]
    )

    assert result == [{"key": "规格", "value": "5.5cm-10条袋装"}]


def test_wait_for_detail_rate_limit_limits_to_15_requests_per_minute() -> None:
    """确保详情页访问会按 60 秒 15 次做节流。"""

    class FakeClock:
        def __init__(self) -> None:
            self.now = 0.0
            self.sleeps: list[float] = []

        def monotonic(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            self.now += seconds

    browser = Alibaba1688ImageSearchBrowser(
        Settings(
            ALIBABA1688_DETAIL_RATE_LIMIT_COUNT=15,
            ALIBABA1688_DETAIL_RATE_LIMIT_WINDOW_SECONDS=60,
        )
    )
    clock = FakeClock()
    browser._time_func = clock.monotonic
    browser._sleep_func = clock.sleep

    for _ in range(15):
        browser.wait_for_detail_rate_limit()

    assert clock.sleeps == []

    browser.wait_for_detail_rate_limit()

    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] == 60.0
