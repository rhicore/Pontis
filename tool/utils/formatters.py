"""Formatters - 共用格式化逻辑

包含：
- resolve_info: 按标签段叠加 info 显示
- format_labels: 格式化标签为 Cypher 风格 :label1:label2
- format_entity_name: 将实体名与标签直接拼接
- meta 格式化
"""
from typing import Dict, Any, List, Optional

from tool.config import (
    MetaTypeConfig, resolve_meta_config, resolve_info as _resolve_info,
)
from tool.utils.knowledge_meta import derive_knowledge_brief


# ========== 标签格式化 ==========

def format_labels(labels: List[str]) -> str:
    """格式化标签为 Cypher 风格 ':label1:label2'。"""
    if not labels:
        return ""
    return "".join(f":{label}" for label in labels)


def format_entity_name(name: str, labels: List[str]) -> str:
    """将实体名与标签直接拼接，避免单独占一列。"""
    return f"{name}{format_labels(labels)}"


# ========== Info 解析 ==========

def get_info(labels: List[str], meta: dict) -> str:
    """按标签段叠加 info dict，返回显示字符串。"""
    return _resolve_info(labels, meta)


def _generic_brief_fallback(meta_dict: Dict[str, Any], labels: List[str]) -> Optional[str]:
    summary = get_info(labels, meta_dict)
    if summary and summary != "-":
        return summary

    label_set = set(labels or [])
    name = meta_dict.get("name")
    if "table" in label_set or "view" in label_set:
        row_count = meta_dict.get("row_count")
        column_count = meta_dict.get("column_count")
        primary_key = meta_dict.get("primary_key")
        parts = []
        if row_count is not None:
            parts.append(f"{row_count} rows")
        if column_count is not None:
            parts.append(f"{column_count} cols")
        if primary_key:
            parts.append(f"pk={primary_key}")
        if parts:
            prefix = f"{name} " if name else ""
            return prefix + ", ".join(parts)
    if "col" in label_set:
        type_labels = [label for label in labels if label in {"INT", "TEXT", "REAL", "BLOB", "DATETIME"}]
        parts = []
        if type_labels:
            parts.append("/".join(type_labels))
        if meta_dict.get("not_null") is True:
            parts.append("not null")
        if meta_dict.get("default_value") not in (None, ""):
            parts.append(f"default={meta_dict.get('default_value')}")
        if parts:
            prefix = f"{name} " if name else ""
            return prefix + ", ".join(parts)
    return None


def _relation_detail_fallback(meta_dict: Dict[str, Any], labels: List[str]) -> Optional[str]:
    label_set = set(labels or [])
    if not ({"fk", "rel", "overlap"} & label_set):
        return None

    brief = meta_dict.get("brief")
    stats = meta_dict.get("stats")
    lines: List[str] = []
    if brief and str(brief).strip() not in ("", "-", "..."):
        lines.append(str(brief).strip())

    if "fk" in label_set:
        if meta_dict.get("from_table") and meta_dict.get("from_column") and meta_dict.get("to_table") and meta_dict.get("to_column"):
            lines.append(
                f"关系: {meta_dict['from_table']}.{meta_dict['from_column']} -> "
                f"{meta_dict['to_table']}.{meta_dict['to_column']}"
            )
        if meta_dict.get("match_count") is not None or meta_dict.get("violation_count") is not None:
            match_count = meta_dict.get("match_count", "?")
            violation_count = meta_dict.get("violation_count", "?")
            lines.append(f"校验: match_count={match_count}, violation_count={violation_count}")

    if "rel" in label_set and stats and isinstance(stats, dict):
        parts = []
        for key in ("jaccard", "coverage_A_in_B", "coverage_B_in_A"):
            value = stats.get(key)
            if value is None:
                continue
            if isinstance(value, float):
                parts.append(f"{key}={value:.4f}")
            else:
                parts.append(f"{key}={value}")
        if parts:
            lines.append("统计: " + ", ".join(parts))

    if "overlap" in label_set and stats and isinstance(stats, dict):
        parts = []
        for key in ("card_overlap", "jaccard", "coverage_A_in_B", "coverage_B_in_A"):
            value = stats.get(key)
            if value is None:
                continue
            if isinstance(value, float):
                parts.append(f"{key}={value:.4f}")
            else:
                parts.append(f"{key}={value}")
        if parts:
            lines.append("重叠统计: " + ", ".join(parts))
        lines.append("该关系仅表示值域重叠，不应直接当作 JOIN 条件；需结合 fk/rel/disambig 再判断。")

    if not lines:
        return None
    return "\n".join(lines)


def _knowledge_detail_fallback(meta_dict: Dict[str, Any], labels: List[str]) -> Optional[str]:
    label_set = set(labels or [])
    if "knowledge" not in label_set:
        return None
    detail = meta_dict.get("detail")
    if detail is not None and str(detail).strip() and str(detail).strip() != "...":
        return str(detail)

    lines: List[str] = []
    if "brief" in meta_dict and str(meta_dict.get("brief")).strip() not in ("", "-", "..."):
        lines.append(f"brief: {meta_dict['brief']}")

    ordered_fields = [
        ("transfer_hint", "transfer_hint"),
        ("why_this_case_matters", "why_this_case_matters"),
        ("mistake_summary", "mistake_summary"),
        ("decision_summary", "decision_summary"),
        ("wrong_assumption", "wrong_assumption"),
        ("fix_hint", "fix_hint"),
        ("verification_note", "verification_note"),
        ("schema_background", "schema_background"),
        ("question", "question"),
        ("evidence", "evidence"),
        ("predicted_sql", "predicted_sql"),
        ("golden_sql", "golden_sql"),
        ("rejected_alternatives", "rejected_alternatives"),
    ]
    for key, title in ordered_fields:
        value = meta_dict.get(key)
        if value is None or str(value).strip() in ("", "-", "..."):
            continue
        lines.append(f"{title}: {value}")

    if not lines:
        return None
    return "\n\n".join(lines)


def get_display_property_value(
    meta_dict: Dict[str, Any],
    labels: List[str],
    key: str,
) -> Any:
    value = meta_dict.get(key)
    if key in ("detail", "brief") and value is not None and str(value).strip() in ("...", "-"):
        value = None
    if value is None and key == "brief":
        value = derive_knowledge_brief(meta_dict) if "knowledge" in set(labels or []) else None
        if value is None:
            value = _generic_brief_fallback(meta_dict, labels)
    if value is None and key == "detail":
        value = _relation_detail_fallback(meta_dict, labels)
        if value is None:
            value = _knowledge_detail_fallback(meta_dict, labels)
        if value is None:
            value = _generic_brief_fallback(meta_dict, labels)
    return value


# ========== Meta 格式化 ==========

def _format_detail(key: str, text: str, max_lines: Optional[int] = None) -> str:
    """格式化 detail/brief：多行缩进，单行紧凑，可选截断。"""
    raw_lines = text.split("\n")
    total_lines = len(raw_lines)

    if max_lines and total_lines > max_lines:
        show_lines = raw_lines[:max_lines]
        truncated = True
    else:
        show_lines = raw_lines
        truncated = False

    if total_lines == 1 and not truncated:
        return f"{key}: {text}"

    indent = " " * (len(key) + 2)
    result = []
    for i, line in enumerate(show_lines):
        if i == 0:
            result.append(f"{key}: {line}")
        else:
            result.append(f"{indent}{line}")

    if truncated:
        result.append(f"{indent}... ({total_lines - max_lines} more lines, use property=[\"{key}\"] to view full)")

    return "\n".join(result)


def format_meta_output(
    meta_dict: Dict[str, Any],
    config: MetaTypeConfig,
    show_all: bool = False,
    specific_key: Optional[str] = None,
) -> str:
    """格式化 meta 输出"""
    if specific_key:
        value = get_display_property_value(meta_dict, meta_dict.get("labels", []), specific_key)
        if value is None:
            return f"{specific_key}: N/A"
        if specific_key in ("detail", "brief"):
            return _format_detail(specific_key, str(value))
        return f"{specific_key}: {_format_meta_value(value, None)}"

    if show_all:
        keys = sorted(k for k in meta_dict.keys() if not k.startswith("_"))
    else:
        keys = [k for k in config.default_keys if k in meta_dict]
        if not keys:
            keys = sorted(meta_dict.keys())

    lines = []
    for key in keys:
        value = meta_dict[key]
        if key in config.folded_keys and not show_all:
            lines.append(f"{key}: {_fold_summary(key, value)}")
        elif key in ("detail", "brief"):
            max_lines = config.max_detail_lines if hasattr(config, 'max_detail_lines') and config.max_detail_lines else None
            lines.append(_format_detail(key, str(value), max_lines=max_lines))
        elif key in config.untruncated_keys:
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {_format_meta_value(value, config.max_value_len)}")

    return "\n".join(lines)


def _fold_summary(key: str, value: Any) -> str:
    """将 sample/topk 折叠为一行摘要"""
    if isinstance(value, list):
        return f"({len(value)} items, use property=\"{key}\" to expand)"
    return str(value)


def _format_meta_value(value: Any, max_len: Optional[int] = None) -> str:
    """格式化 meta 值，可选截断"""
    if isinstance(value, list):
        items = [str(item) for item in value[:10]]
        text = ", ".join(items)
        if len(value) > 10:
            text += f" ... ({len(value)} total)"
        return text
    s = str(value)
    if max_len and len(s) > max_len:
        return s[:max_len] + "..."
    return s
