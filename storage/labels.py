"""标签工具 — 多扁平标签的创建、匹配、归一化。

标签格式：["col", "INT"]（多扁平），兼容旧格式 "col/INT"（自动归一化）。
"""
from typing import List


def normalize_labels(labels: List[str]) -> List[str]:
    """将旧格式 "col/INT" 归一化为 ["col", "INT"]。去重保序。"""
    if not labels:
        return []
    result = []
    seen = set()
    for label in labels:
        parts = label.split("/")
        for part in parts:
            if part and part not in seen:
                result.append(part)
                seen.add(part)
    return result


def label_matches(entity_labels: List[str], query: str) -> bool:
    """扁平标签匹配。query 是否在 entity_labels 中。"""
    return query in entity_labels


def labels_match_all(entity_labels: List[str], queries: List[str]) -> bool:
    """entity_labels 是否包含 queries 中的全部。"""
    return set(queries).issubset(set(entity_labels))
