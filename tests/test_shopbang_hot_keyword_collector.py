"""上品帮热销关键词提取逻辑测试。"""

from ozon_selection.collectors.ozon.shopbang_hot_keyword_collector import ShopbangHotKeywordCollector


def test_normalize_keyword_removes_generic_text() -> None:
    """通用面包屑文本应被过滤掉。"""

    assert ShopbangHotKeywordCollector.normalize_keyword("首页") == ""
    assert ShopbangHotKeywordCollector.normalize_keyword("  商品详情  ") == ""
    assert ShopbangHotKeywordCollector.normalize_keyword("园艺工具(123)") == "园艺工具"


def test_is_excluded_category_text_detects_blocked_categories() -> None:
    """应能识别被排除的大类。"""

    assert ShopbangHotKeywordCollector.is_excluded_category_text("电子产品配件") is True
    assert ShopbangHotKeywordCollector.is_excluded_category_text("Electronics") is True
    assert ShopbangHotKeywordCollector.is_excluded_category_text("Pharmacy") is True
    assert ShopbangHotKeywordCollector.is_excluded_category_text("园艺喷壶") is False


def test_pick_ancestor_items_returns_parent_and_grandparent() -> None:
    """应从面包屑中取出上一级和上两级。"""

    breadcrumbs = [
        {"text": "首页", "href": ""},
        {"text": "园艺工具", "href": "/garden"},
        {"text": "移栽工具", "href": "/transplant"},
        {"text": "移栽三件套", "href": "/detail"},
    ]

    result = ShopbangHotKeywordCollector.pick_ancestor_items(breadcrumbs)

    assert result == [
        {"text": "移栽工具", "href": "/transplant"},
        {"text": "园艺工具", "href": "/garden"},
    ]


def test_dedupe_keywords_keeps_order_and_filters_excluded() -> None:
    """关键词去重时应保留顺序并过滤掉排除类目。"""

    result = ShopbangHotKeywordCollector.dedupe_keywords(
        ["园艺工具", "园艺工具", "电子产品", "Electronics", "移栽工具"]
    )

    assert result == ["园艺工具", "移栽工具"]


def test_extract_ozon_search_keyword_from_url() -> None:
    """应从 Ozon 搜索 URL 提取 text 参数。"""

    url = "https://ozon.kz/search/?deny_category_prediction=true&from_global=true&text=%D0%9F%D1%80%D0%BE%D1%82%D0%B5%D0%B8%D0%BD&product_id=379913038"

    assert ShopbangHotKeywordCollector.extract_ozon_search_keyword(url) == "Протеин"


def test_collect_api_entries_prefers_backend_link_data() -> None:
    """应优先从热销接口数据中提取标题与跳转 URL。"""

    collector = ShopbangHotKeywordCollector()
    result = collector.collect_api_entries(
        [
            {
                "_id": "1",
                "name": "ПЕРВЫЙ РУССКИЙ ПРОТЕИН Protein",
                "link": "https://www.ozon.ru/product/379913038",
                "sku": "379913038",
                "category1": "Pharmacy",
                "category3": "蛋白质",
            },
            {
                "_id": "2",
                "name": "SvetoCopy Printer Paper A4, 500 pcs",
                "link": "https://www.ozon.ru/product/149222760",
                "sku": "149222760",
                "category1": "Stationery",
                "category3": "打印纸",
            },
        ]
    )

    assert result == [
        {
            "index": 1,
            "title": "SvetoCopy Printer Paper A4, 500 pcs",
            "action_text": "SvetoCopy Printer Paper A4, 500 pcs",
            "href": "https://www.ozon.ru/product/149222760",
            "category_text": "Stationery > 打印纸",
            "row_text": "SvetoCopy Printer Paper A4, 500 pcs",
            "sku": "149222760",
            "row_key": "2",
        }
    ]


def test_select_primary_category_levels_returns_parent_and_grandparent() -> None:
    """应从当前品类向上回推一级和二级类目。"""

    result = ShopbangHotKeywordCollector.select_primary_category_levels(
        ["文具", "纸", "打印纸", "SvetoCopy"]
    )

    assert result == ["打印纸", "纸"]


def test_build_keyword_record_returns_structured_category_context() -> None:
    """应生成包含当前类目、上一级和上两级的结构化记录。"""

    result = ShopbangHotKeywordCollector.build_keyword_record(
        breadcrumb_texts=["文具", "纸", "打印纸", "SvetoCopy"],
        source_product_title="SvetoCopy Printer Paper A4, 500 pcs",
        source_product_url="https://www.ozon.ru/product/149222760",
        source_product_sku="149222760",
    )

    assert result == {
        "current_category": "SvetoCopy",
        "parent_category": "打印纸",
        "grandparent_category": "纸",
        "source_product_title": "SvetoCopy Printer Paper A4, 500 pcs",
        "source_product_url": "https://www.ozon.ru/product/149222760",
        "source_product_sku": "149222760",
        "source_batch_type": "shopbang_hot",
        "keywords": ["打印纸", "纸"],
    }


def test_filter_entries_by_urls_skips_processed_and_seen_links() -> None:
    """应先过滤掉已处理和本轮重复的详情 URL。"""

    collector = ShopbangHotKeywordCollector()
    processed_urls = {"https://www.ozon.ru/product/149222760"}
    seen_urls = {"https://www.ozon.ru/product/1710550744"}

    result = collector.filter_entries_by_urls(
        [
            {"href": "https://www.ozon.ru/product/149222760/?__rr=1", "title": "A"},
            {"href": "https://www.ozon.ru/product/1710550744/", "title": "B"},
            {"href": "https://www.ozon.ru/product/29508517", "title": "C"},
            {"href": "", "title": "D"},
        ],
        processed_urls=processed_urls,
        seen_urls=seen_urls,
    )

    assert result == [
        {"href": "https://www.ozon.ru/product/29508517", "title": "C"},
        {"href": "", "title": "D"},
    ]


def test_collect_descendant_category_ids_flattens_leaf_category_ids() -> None:
    """应从类目树中展开所有叶子类目 ID。"""

    node = {
        "name": "文具",
        "children": [
            {
                "name": "纸",
                "children": [
                    {"name": "打印纸", "category_id": 101, "children": []},
                    {"name": "卫生纸", "category_id": 102, "children": []},
                ],
            },
            {
                "name": "笔",
                "children": [
                    {"name": "钢笔", "type_id": 201, "children": []},
                ],
            },
        ],
    }

    result = ShopbangHotKeywordCollector.collect_descendant_category_ids(node)

    assert result == [101, 102, 201]


def test_collect_remai_categories_filters_excluded_top_categories(monkeypatch) -> None:
    """应过滤掉电子产品、食品、药店和服装。"""

    collector = ShopbangHotKeywordCollector()

    monkeypatch.setattr(
        collector,
        "load_remai_category_tree",
        lambda page: [
            {"name": "电子产品", "children": [{"category_id": 1, "children": []}]},
            {"name": "文具", "children": [{"category_id": 2, "children": []}]},
            {"name": "食品", "children": [{"category_id": 3, "children": []}]},
            {"name": "住宅和花园", "children": [{"category_id": 4, "children": []}]},
        ],
    )

    result = collector.collect_remai_categories(page=None)  # type: ignore[arg-type]

    assert result == [
        {"index": 2, "name": "文具", "category_ids": [2], "top_category_id": None},
        {"index": 4, "name": "住宅和花园", "category_ids": [4], "top_category_id": None},
    ]


def test_collect_paginated_api_items_fetches_by_page_limit(monkeypatch) -> None:
    """应按页数上限继续抓取后续页。"""

    collector = ShopbangHotKeywordCollector()
    response_state = {
        "items": [
            {"link": "https://www.ozon.ru/product/1", "name": "A", "category1": "文具", "category3": "纸"},
            {"link": "https://www.ozon.ru/product/2", "name": "B", "category1": "文具", "category3": "纸"},
        ],
        "request_body": {"pageSize": 30, "pageNo": 1, "categories": [1]},
    }

    monkeypatch.setattr(
        collector,
        "fetch_remai_items_by_page",
        lambda page, request_body, page_no: [
            {"link": "https://www.ozon.ru/product/3", "name": "C", "category1": "文具", "category3": "纸"},
            {"link": "https://www.ozon.ru/product/4", "name": "D", "category1": "文具", "category3": "纸"},
        ]
        if page_no == 2
        else [],
    )

    result = collector.collect_paginated_api_items_for_current_category(
        page=None,  # type: ignore[arg-type]
        response_state=response_state,
        max_pages=2,
        processed_urls={"https://www.ozon.ru/product/1"},
        seen_urls=set(),
        category_name="文具",
    )

    assert [item["link"] for item in result] == [
        "https://www.ozon.ru/product/1",
        "https://www.ozon.ru/product/2",
        "https://www.ozon.ru/product/3",
        "https://www.ozon.ru/product/4",
    ]


def test_collect_paginated_api_items_with_progress_resumes_from_saved_page(monkeypatch) -> None:
    """存在类目进度时，应从下一页继续拉取。"""

    collector = ShopbangHotKeywordCollector()
    response_state = {
        "items": [
            {"link": "https://www.ozon.ru/product/1", "name": "A", "category1": "文具", "category3": "纸"},
        ],
        "request_body": {"pageSize": 30, "pageNo": 1, "categories": [1]},
    }

    requested_pages: list[int] = []

    def fake_fetch(page, request_body, page_no):  # type: ignore[no-untyped-def]
        requested_pages.append(page_no)
        if page_no == 3:
            return [
                {"link": "https://www.ozon.ru/product/3", "name": "C", "category1": "文具", "category3": "纸"},
            ]
        if page_no == 4:
            return [
                {"link": "https://www.ozon.ru/product/4", "name": "D", "category1": "文具", "category3": "纸"},
            ]
        return []

    monkeypatch.setattr(collector, "fetch_remai_items_by_page", fake_fetch)

    result = collector.collect_paginated_api_items_with_progress(
        page=None,  # type: ignore[arg-type]
        response_state=response_state,
        max_pages=2,
        processed_urls=set(),
        seen_urls=set(),
        category_name="文具",
        start_page=3,
    )

    assert requested_pages == [3, 4]
    assert [item["link"] for item in result["items"]] == [
        "https://www.ozon.ru/product/3",
        "https://www.ozon.ru/product/4",
    ]
    assert result["last_completed_page"] == 4
    assert result["last_page_size"] == 1


def test_simplify_product_keyword_prefers_compact_search_term() -> None:
    """应把商品标题压缩为更适合搜索的短词。"""

    assert (
        ShopbangHotKeywordCollector.simplify_product_keyword(
            'Специализированный пищевой продукт для питания спортсменов "Сывороточный протеин", 1 кг'
        )
        == "Сывороточный протеин"
    )
    assert (
        ShopbangHotKeywordCollector.simplify_product_keyword(
            "Игровая консоль PlayStation 5 Slim Blu-Ray купить на OZON по низкой цене"
        )
        == "Игровая консоль"
    )
    assert (
        ShopbangHotKeywordCollector.simplify_product_keyword(
            "VOIS Шампунь для волос женский и бальзам кондиционер, бессульфатный, набор 2000мл"
        )
        == "Шампунь для волос"
    )
