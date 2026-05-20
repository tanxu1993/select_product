"""关键词去重服务。"""

from __future__ import annotations

from dataclasses import dataclass
import re


_SPLIT_PATTERN = re.compile(r"[\n,;，；]+")
_TOKEN_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+", re.UNICODE)
_STOPWORDS = {
    "и",
    "для",
    "на",
    "по",
    "с",
    "со",
    "в",
    "во",
    "к",
    "ко",
    "из",
    "от",
    "до",
    "под",
    "над",
    "при",
    "the",
    "and",
    "for",
    "with",
}
_GENERIC_TOKENS = {
    "продукция",
    "средства",
    "комплектующие",
    "товары",
    "аксессуары",
    "принадлежности",
    "изделия",
    "оборудование",
    "категория",
}


@dataclass(slots=True)
class DedupedKeyword:
    """单个保留关键词及其去重明细。"""

    keyword: str
    removed_keywords: list[str]
    reason: str


class KeywordDeduper:
    """支持完全重复和启发式语义重复两套规则。"""

    @classmethod
    def parse_keywords(cls, raw_value: str) -> list[str]:
        """把逗号、分号、换行分隔文本解析成关键词列表。"""

        return [item.strip() for item in _SPLIT_PATTERN.split(raw_value or "") if item.strip()]

    @classmethod
    def dedupe_exact(cls, keywords: list[str]) -> list[DedupedKeyword]:
        """仅按完全重复去重。"""

        kept: list[DedupedKeyword] = []
        seen: dict[str, DedupedKeyword] = {}
        for keyword in keywords:
            normalized = cls.normalize_text(keyword)
            if not normalized:
                continue
            if normalized in seen:
                seen[normalized].removed_keywords.append(keyword)
                continue
            record = DedupedKeyword(keyword=keyword, removed_keywords=[], reason="exact_unique")
            kept.append(record)
            seen[normalized] = record
        return kept

    @classmethod
    def dedupe_semantic(cls, keywords: list[str]) -> list[DedupedKeyword]:
        """按完全重复 + 启发式父子类目关系去重。"""

        exact_keywords = cls.dedupe_exact(keywords)
        kept: list[DedupedKeyword] = []

        for item in exact_keywords:
            merged = False
            for index, existing in enumerate(list(kept)):
                relation = cls.compare_keywords(existing.keyword, item.keyword)
                if relation == "keep_existing":
                    existing.removed_keywords.append(item.keyword)
                    existing.reason = "semantic_parent_child"
                    merged = True
                    break
                if relation == "replace_existing":
                    item.removed_keywords.extend([existing.keyword, *existing.removed_keywords])
                    item.reason = "semantic_parent_child"
                    kept[index] = item
                    merged = True
                    break
            if not merged:
                kept.append(item)
        return kept

    @classmethod
    def compare_keywords(cls, left: str, right: str) -> str:
        """比较两个关键词是否存在父子/强重叠关系。"""

        left_features = cls.build_features(left)
        right_features = cls.build_features(right)

        if not left_features["core_stems"] or not right_features["core_stems"]:
            return "independent"

        left_core = set(left_features["core_stems"])
        right_core = set(right_features["core_stems"])
        shared_stems = left_core & right_core
        if not shared_stems:
            return "independent"

        subset_relation = left_core <= right_core or right_core <= left_core
        generic_relation = bool(shared_stems) and (
            left_features["has_generic_token"] or right_features["has_generic_token"]
        )
        if not subset_relation and not generic_relation:
            return "independent"

        left_score = cls.compute_specificity_score(left_features)
        right_score = cls.compute_specificity_score(right_features)
        if right_score > left_score:
            return "replace_existing"
        return "keep_existing"

    @classmethod
    def build_features(cls, keyword: str) -> dict[str, object]:
        """提取用于语义去重的特征。"""

        normalized = cls.normalize_text(keyword)
        tokens = [token.lower() for token in _TOKEN_PATTERN.findall(normalized)]
        core_tokens = [token for token in tokens if token not in _STOPWORDS]
        core_stems = [cls.stem_token(token) for token in core_tokens]
        unique_core_stems: list[str] = []
        for stem in core_stems:
            if stem and stem not in unique_core_stems:
                unique_core_stems.append(stem)

        return {
            "normalized": normalized,
            "tokens": tokens,
            "core_tokens": core_tokens,
            "core_stems": unique_core_stems,
            "has_generic_token": any(token in _GENERIC_TOKENS for token in core_tokens),
            "has_conjunction": "и" in tokens,
            "has_relation_word": any(token in {"для", "с", "со", "под"} for token in tokens),
        }

    @classmethod
    def compute_specificity_score(cls, features: dict[str, object]) -> int:
        """给关键词打一个“更具体”的启发式分数。"""

        core_tokens = list(features["core_tokens"])
        score = len(core_tokens) * 10
        if bool(features["has_relation_word"]):
            score += 3
        if bool(features["has_conjunction"]):
            score -= 2
        if bool(features["has_generic_token"]):
            score -= 12
        return score

    @staticmethod
    def stem_token(token: str) -> str:
        """对俄文/英文 token 做轻量词干截断。"""

        normalized = token.lower().replace("ё", "е")
        if len(normalized) <= 4:
            return normalized
        return normalized[:4]

    @staticmethod
    def normalize_text(text: str) -> str:
        """清洗关键词文本。"""

        return re.sub(r"\s+", " ", str(text or "")).strip()
