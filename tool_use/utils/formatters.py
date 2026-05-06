"""Formatters - 共用格式化逻辑

包含：
- resolve_info: 按标签段叠加 info 显示
- format_labels: 格式化标签为 :label/seg :label2 形式
- meta 格式化
"""
from typing import Dict, Any, List, Optional

from tool_use.config import (
    MetaTypeConfig, resolve_meta_config, resolve_info as _resolve_info,
)


# ========== 标签格式化 ==========

def format_labels(labels: List[str]) -> str:
    """格式化标签为 ':label1/seg :label2' 形式。"""
    if not labels:
        return ""
    return " ".join(f":{label}" for label in labels)


# ========== Info 解析 ==========

def get_info(labels: List[str], meta: dict) -> str:
    """按标签段叠加 info dict，返回显示字符串。"""
    return _resolve_info(labels, meta)


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
        value = meta_dict.get(specific_key)
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
