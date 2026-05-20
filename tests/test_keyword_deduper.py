"""关键词去重测试。"""

from ozon_selection.services.keyword_deduper import KeywordDeduper


def test_parse_keywords_supports_multiple_separators() -> None:
    """应支持逗号、分号和换行。"""

    result = KeywordDeduper.parse_keywords("A,B；C\nD")

    assert result == ["A", "B", "C", "D"]


def test_dedupe_exact_keeps_only_strict_unique_keywords() -> None:
    """完全重复去重只处理相同字符串。"""

    result = KeywordDeduper.dedupe_exact(["Бумага", "Бумага", "Бумага для печати"])

    assert [item.keyword for item in result] == ["Бумага", "Бумага для печати"]
    assert result[0].removed_keywords == ["Бумага"]


def test_dedupe_semantic_prefers_more_specific_keyword_for_parent_child_pair() -> None:
    """语义去重应优先保留更具体的词。"""

    result = KeywordDeduper.dedupe_semantic(["Бумага", "Бумага для печати"])

    assert [item.keyword for item in result] == ["Бумага для печати"]
    assert result[0].removed_keywords == ["Бумага"]


def test_dedupe_semantic_can_merge_overlapping_toilet_litter_keywords() -> None:
    """父子类目强重叠时应合并。"""

    result = KeywordDeduper.dedupe_semantic(["Туалеты и наполнители", "Наполнители для туалета"])

    assert [item.keyword for item in result] == ["Наполнители для туалета"]
    assert result[0].removed_keywords == ["Туалеты и наполнители"]


def test_dedupe_semantic_can_merge_generic_product_category_phrase() -> None:
    """带泛类目词的短语应让位给更具体的短语。"""

    result = KeywordDeduper.dedupe_semantic(["Туалетная бумага", "Ватно-бумажная продукция"])

    assert [item.keyword for item in result] == ["Туалетная бумага"]
    assert result[0].removed_keywords == ["Ватно-бумажная продукция"]
