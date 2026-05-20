"""Ozon 商品采集器纯逻辑测试。"""

from __future__ import annotations

from pathlib import Path

from config.settings import Settings
from ozon_selection.collectors.ozon.product_collector import ProductCollector
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


def build_collector(tmp_path: Path) -> ProductCollector:
    """创建用于测试的采集器实例。"""

    settings = Settings(
        OZON_BASE_URL="https://www.ozon.ru",
        OZON_SCRAPE_KEYWORD="Виброхвост",
        OZON_SCRAPE_OUTPUT_DIR=str(tmp_path / "exports"),
        OZON_SCRAPE_IMAGE_DIR=str(tmp_path / "images"),
    )
    return ProductCollector(settings)


def test_parse_num_handles_common_values(tmp_path: Path) -> None:
    """确保插件文本数字解析与 JS 逻辑一致。"""

    collector = build_collector(tmp_path)
    assert collector.parse_num("123.5%") == 123.5
    assert collector.parse_num("无数据") is None
    assert collector.parse_num("无跟卖") is None
    assert collector.parse_num("Kelirol等1个卖家") == 1


def test_profit_calc_matches_expected_tier(tmp_path: Path) -> None:
    """确保运费与最大成本估算正常。"""

    collector = build_collector(tmp_path)
    estimate = collector.profit_calc(1200, 400)
    assert estimate.tier == "Extra Small(≤500g)"
    assert estimate.shipping == 217.6
    assert estimate.max_cost == 622


def test_evaluate_marks_valid_candidate_as_pass(tmp_path: Path) -> None:
    """确保符合规则的商品不会触发红线。"""

    collector = build_collector(tmp_path)
    product = {
        "price": 1200,
        "shippingMode": "FBS",
        "sellers": 8,
        "returnRate": 5,
        "growthRate": 25,
        "monthlySales": 350,
        "listedDays": 20,
        "weight": 300,
        "conversionRate": 82,
        "ctr": 4.5,
        "promotionDays": 10,
        "searchViews": 150000,
        "rating": 4.8,
    }

    result = collector.evaluate(product)
    assert result.fails == []
    assert result.score > 0


def test_evaluate_marks_invalid_candidate_as_fail(tmp_path: Path) -> None:
    """确保触发红线的商品会被拦截。"""

    collector = build_collector(tmp_path)
    product = {
        "price": 200,
        "shippingMode": "FBS",
        "sellers": 60,
        "returnRate": 30,
        "growthRate": -5,
        "monthlySales": 20,
        "listedDays": 45,
        "weight": 3000,
    }

    result = collector.evaluate(product)
    assert len(result.fails) >= 4
    assert any("价格200₽" in item for item in result.fails)


def test_evaluate_marks_low_monthly_sales_as_fail(tmp_path: Path) -> None:
    """月销量低于 5 应直接淘汰。"""

    collector = build_collector(tmp_path)
    product = {
        "price": 1200,
        "shippingMode": "FBS",
        "sellers": 8,
        "returnRate": 5,
        "growthRate": 25,
        "monthlySales": 4,
        "listedDays": 20,
        "weight": 300,
    }

    result = collector.evaluate(product)

    assert any("月销4件 不在5-500之间" in item for item in result.fails)


def test_evaluate_marks_price_below_min_as_fail(tmp_path: Path) -> None:
    """价格低于 500 应直接淘汰。"""

    collector = build_collector(tmp_path)
    product = {
        "price": 499,
        "shippingMode": "FBS",
        "sellers": 8,
        "returnRate": 5,
        "growthRate": 25,
        "monthlySales": 350,
        "listedDays": 20,
        "weight": 300,
    }

    result = collector.evaluate(product)

    assert any("价格499₽ 需500-20000₽" in item for item in result.fails)


def test_evaluate_marks_price_above_max_as_fail(tmp_path: Path) -> None:
    """价格高于 20000 应直接淘汰。"""

    collector = build_collector(tmp_path)
    product = {
        "price": 20001,
        "shippingMode": "FBS",
        "sellers": 8,
        "returnRate": 5,
        "growthRate": 25,
        "monthlySales": 350,
        "listedDays": 20,
        "weight": 300,
    }

    result = collector.evaluate(product)

    assert any("价格20001₽ 需500-20000₽" in item for item in result.fails)


def test_evaluate_marks_high_monthly_sales_as_fail(tmp_path: Path) -> None:
    """月销量高于 500 应直接淘汰。"""

    collector = build_collector(tmp_path)
    product = {
        "price": 1200,
        "shippingMode": "FBS",
        "sellers": 8,
        "returnRate": 5,
        "growthRate": 25,
        "monthlySales": 501,
        "listedDays": 20,
        "weight": 300,
    }

    result = collector.evaluate(product)

    assert any("月销501件 不在5-500之间" in item for item in result.fails)


def test_evaluate_marks_low_sellers_as_fail(tmp_path: Path) -> None:
    """跟卖人数低于 1 应直接淘汰。"""

    collector = build_collector(tmp_path)
    product = {
        "price": 1200,
        "shippingMode": "FBS",
        "sellers": 0,
        "returnRate": 5,
        "growthRate": 25,
        "monthlySales": 350,
        "listedDays": 20,
        "weight": 300,
    }

    result = collector.evaluate(product)

    assert any("跟卖者0个 不在1-50之间" in item for item in result.fails)


def test_evaluate_marks_missing_sellers_as_fail(tmp_path: Path) -> None:
    """跟卖人数缺失时也应直接淘汰。"""

    collector = build_collector(tmp_path)
    product = {
        "price": 1200,
        "shippingMode": "FBS",
        "sellers": None,
        "returnRate": 5,
        "growthRate": 25,
        "monthlySales": 350,
        "listedDays": 20,
        "weight": 300,
    }

    result = collector.evaluate(product)

    assert any("跟卖者数据缺失" in item for item in result.fails)


def test_evaluate_marks_too_old_listing_as_fail(tmp_path: Path) -> None:
    """上架天数大于 365 天应直接淘汰。"""

    collector = build_collector(tmp_path)
    product = {
        "price": 1200,
        "shippingMode": "FBS",
        "sellers": 8,
        "returnRate": 5,
        "growthRate": 25,
        "monthlySales": 350,
        "listedDays": 366,
        "weight": 300,
    }

    result = collector.evaluate(product)

    assert any("上架366天(需在1-365天之间)" in item for item in result.fails)


def test_evaluate_marks_too_new_listing_as_fail(tmp_path: Path) -> None:
    """上架天数小于 1 天应直接淘汰。"""

    collector = build_collector(tmp_path)
    product = {
        "price": 1200,
        "shippingMode": "FBS",
        "sellers": 8,
        "returnRate": 5,
        "growthRate": 25,
        "monthlySales": 350,
        "listedDays": 0,
        "weight": 300,
    }

    result = collector.evaluate(product)

    assert any("上架0天(需在1-365天之间)" in item for item in result.fails)


def test_evaluate_marks_russian_local_warehouse_as_fail(tmp_path: Path) -> None:
    """俄罗斯本地仓商品应直接淘汰。"""

    collector = build_collector(tmp_path)
    product = {
        "price": 1200,
        "shippingMode": "FBS",
        "sellers": 8,
        "returnRate": 5,
        "growthRate": 25,
        "monthlySales": 350,
        "listedDays": 20,
        "weight": 300,
        "isRussianLocalWarehouse": True,
    }

    result = collector.evaluate(product)

    assert any("俄罗斯本地仓" in item for item in result.fails)


def test_build_search_url_uses_settings(tmp_path: Path) -> None:
    """确保搜索 URL 会使用配置参数。"""

    collector = build_collector(tmp_path)
    url = collector.build_search_url("测试 关键词")
    assert "text=%E6%B5%8B%E8%AF%95%20%E5%85%B3%E9%94%AE%E8%AF%8D" in url
    assert "sorting=rating" in url


def test_export_to_excel_creates_file(tmp_path: Path) -> None:
    """确保导出 Excel 文件成功生成。"""

    collector = build_collector(tmp_path)
    rows = [
        {
            "#": 1,
            "结果": "✅ 通过",
            "红线原因": "",
            "注意事项": "",
            "黄金评分": 10,
            "SKU": "123456",
            "商品链接": "https://www.ozon.ru/product/demo-123456/",
            "主图路径": str(tmp_path / "images" / "123456" / "1.jpg"),
            "商品名称": "demo",
            "属性数": 1,
            "商品属性": "颜色: 红色",
            "类目": "cat",
            "品牌": "brand",
            "当前售价(₽)": 1000,
            "平均价格(₽)": 900,
            "物流档位": "Extra Small(≤500g)",
            "包装重量(g)": 300,
            "预估运费(₽)": 13.5,
            "最大成本(₽)": 536,
            "发货模式": "FBS",
            "上架天数": 365,
            "月销量(件)": 300,
            "日销量(件)": 10,
            "月增速(%)": 25,
            "退货取消率(%)": 5,
            "成交率(%)": 80,
            "点击率(%)": 4,
            "加购率(%)": 9,
            "搜索浏览量": 100000,
            "广告份额(%)": 10,
            "促销天数": 12,
            "跟卖者数": 8,
            "跟卖最低价": "950",
            "评分": 4.9,
            "评价数": 200,
            "有插件数据": "是",
        }
    ]

    output_file = collector.export_to_excel(rows, "测试 关键词")
    assert output_file.exists()
    assert output_file.suffix == ".xlsx"


def test_format_attributes_returns_joined_text(tmp_path: Path) -> None:
    """确保商品属性会被格式化为可读文本。"""

    collector = build_collector(tmp_path)
    value = collector.format_attributes(
        [
            {"key": "颜色", "value": "红色"},
            {"key": "材质", "value": "硅胶"},
        ]
    )

    assert value == "颜色: 红色 | 材质: 硅胶"


def test_analyze_detail_logistics_lines_extracts_delivery_return_and_warehouse(tmp_path: Path) -> None:
    """确保能从详情文本中抽出配送、退货和仓库信息。"""

    collector = build_collector(tmp_path)

    result = collector.analyze_detail_logistics_lines(
        [
            "Доставка из России, завтра",
            "Возврат товара надлежащего качества в течение 30 дней",
            "Склад: Россия",
        ]
    )

    assert result["deliveryInfo"] == "Доставка из России, завтра"
    assert result["returnInfo"] == "Возврат товара надлежащего качества в течение 30 дней"
    assert result["warehouseInfo"] == "Склад: Россия"
    assert result["isRussianLocalWarehouse"] is True


def test_build_result_rows_includes_logistics_fields(tmp_path: Path) -> None:
    """导出行应包含配送、退货和本地仓信息。"""

    collector = build_collector(tmp_path)

    rows = collector.build_result_rows(
        [
            {
                "sku": "123",
                "name": "demo",
                "url": "https://www.ozon.ru/product/demo-123/",
                "price": 1200,
                "shippingMode": "FBS",
                "returnRate": 5,
                "deliveryInfo": "Доставка из Китая",
                "returnInfo": "Возврат 30 дней",
                "warehouseInfo": "Склад: Китай",
                "isRussianLocalWarehouse": False,
                "passed": True,
                "failReasons": [],
                "warnings": [],
                "score": 6,
            }
        ]
    )

    assert rows[0]["配送信息"] == "Доставка из Китая"
    assert rows[0]["退货信息"] == "Возврат 30 дней"
    assert rows[0]["仓库信息"] == "Склад: Китай"
    assert rows[0]["俄罗斯本地仓"] == "否"


def test_plugin_card_placeholder_detection(tmp_path: Path) -> None:
    """确保能识别插件未登录占位内容。"""

    collector = build_collector(tmp_path)
    placeholder_text = """
    类目：登录
    品牌：登录
    月销量：登录
    发货模式：登录
    跟卖者：登录
    """
    assert collector.plugin_card_is_login_placeholder(placeholder_text) is True
    assert collector.plugin_card_has_real_data(placeholder_text) is False


def test_plugin_card_real_data_detection(tmp_path: Path) -> None:
    """确保能识别插件真实数据内容。"""

    collector = build_collector(tmp_path)
    real_text = """
    类目：软饵
    品牌：Lucky John
    月销量：320件
    月销售动态：+23%
    发货模式：FBS
    跟卖者：8
    """
    assert collector.plugin_card_is_login_placeholder(real_text) is False
    assert collector.plugin_card_has_real_data(real_text) is True


def test_plugin_card_real_data_detection_with_long_detail_text(tmp_path: Path) -> None:
    """长详情插件文本中包含真实指标时也应判定为有效。"""

    collector = build_collector(tmp_path)
    real_text = """
    类目：住宅和花园 > 马桶刷
    品牌：LBSYSLB
    月销量：490 件
    月销售额：57.97万 ₽
    日销量：17.821 件
    日销售额：2.11万 ₽
    月销售动态：-58.9 %
    商品卡片浏览量：10610
    商品卡片加购率：8.98 %
    搜索和目录浏览量：103206
    搜索和目录加购率：0.2 %
    点击率：2.62 %
    付费推广天数：10天
    广告份额：8.6%
    成交率：89.4%
    退货取消率：10.60%
    平均价格：1183.1 ₽
    包装重量：690 g
    发货模式：FBO
    跟卖者：Kelirol等1个卖家
    跟卖最低价：1 525 ₽
    上架时间：2024-12-18 (510天)
    SKU：1789957660
    """

    assert collector.plugin_card_is_login_placeholder(real_text) is False
    assert collector.plugin_card_has_real_data(real_text) is True


def test_should_attempt_scroll_recovery_only_when_below_target(tmp_path: Path) -> None:
    """只有未达到目标且恢复轮次未用尽时才应尝试补偿滚动。"""

    collector = build_collector(tmp_path)

    assert collector.should_attempt_scroll_recovery(current_count=24, target_count=1000, recovery_attempts=0) is True
    assert collector.should_attempt_scroll_recovery(current_count=1000, target_count=1000, recovery_attempts=0) is False
    assert collector.should_attempt_scroll_recovery(current_count=24, target_count=1000, recovery_attempts=3) is False


def test_build_paged_url_appends_page_parameter(tmp_path: Path) -> None:
    """确保能生成后续分页链接。"""

    collector = build_collector(tmp_path)
    base_url = (
        "https://www.ozon.ru/category/prochie-aksessuary-dlya-rybalki-30706/"
        "?from_global=true&sorting=rating&text=test"
    )

    result = collector.build_paged_url(base_url, 3)

    assert result.endswith("from_global=true&sorting=rating&text=test&page=3")


def test_merge_page_products_deduplicates_by_sku(tmp_path: Path) -> None:
    """跨页合并时应按 SKU 去重。"""

    collector = build_collector(tmp_path)
    collected_products = [{"sku": "1"}]
    seen_skus = {"1"}

    added, skipped = collector.merge_page_products(
        collected_products=collected_products,
        page_products=[{"sku": "1"}, {"sku": "2"}, {"sku": "3"}],
        seen_skus=seen_skus,
    )

    assert added == 2
    assert skipped == 1
    assert [item["sku"] for item in collected_products] == ["1", "2", "3"]


def test_collect_products_across_pages_stops_gracefully_on_later_page_timeout(tmp_path: Path) -> None:
    """后续分页超时应保留已抓数据并停止，而不是整轮报错。"""

    collector = build_collector(tmp_path)
    collector.settings.ozon_scrape_target_products = 40

    class FakePage:
        def __init__(self) -> None:
            self.url = "https://www.ozon.ru/category/demo/?currency_price=800.000%3B8000.000"

        def goto(self, url: str, wait_until: str, timeout: int) -> None:
            self.url = url

        def wait_for_timeout(self, timeout: int) -> None:
            return None

    fake_page = FakePage()

    def fake_wait_search_results_ready(page) -> None:
        if "page=2" in page.url:
            raise PlaywrightTimeoutError("page 2 timeout")

    def fake_scroll_to_load(page, target_count: int) -> None:
        return None

    def fake_extract_all(page) -> list[dict]:
        if "page=" not in page.url:
            return [{"sku": "1"}, {"sku": "2"}]
        return []

    collector.wait_search_results_ready = fake_wait_search_results_ready
    collector.scroll_to_load = fake_scroll_to_load
    collector.extract_all = fake_extract_all

    results = collector.collect_products_across_pages(fake_page)

    assert [item["sku"] for item in results] == ["1", "2"]
