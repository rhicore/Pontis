"""
Formatters - 共用格式化逻辑

包含：
- glob/search 的类型配置获取和文件类型推断
- meta 的属性格式化
"""
from typing import Dict, Any, List, Optional

from tool_use.config import MetaTypeConfig, META_TYPE_CONFIG


# ========== 配置获取 ==========

def get_type_config(file_type: str):
    """获取文件类型的 info 配置"""
    from tool_use.config import INFO_TYPE_CONFIG
    file_type = file_type.lower()
    if not file_type.startswith("."):
        file_type = "." + file_type
    return INFO_TYPE_CONFIG.get(file_type, INFO_TYPE_CONFIG[".file"])


def get_meta_type_config(file_ext: str) -> MetaTypeConfig:
    """获取 meta 类型的显示配置（按后缀名匹配）"""
    ext = file_ext.lower()
    if ext and not ext.startswith("."):
        ext = "." + ext
    return META_TYPE_CONFIG.get(ext, META_TYPE_CONFIG["default"])


def get_file_type_from_name(name: str, node_type: str = "") -> str:
    """从文件名推断类型"""
    if "." in name:
        parts = name.split(".")

        # 处理复合后缀如 id.INT.col
        if len(parts) >= 3 and parts[-2].upper() in ["INT", "TEXT", "VARCHAR", "BOOL", "FLOAT", "DOUBLE", "DATE", "DATETIME"]:
            return ".col"

        # 处理序列化文件内部类型如 "question_id.INT"
        if len(parts) == 2:
            ext = parts[-1].upper()
            if ext in ["INT", "STR", "STRING", "TEXT", "BOOL", "FLOAT", "DOUBLE", "DICT", "LIST", "ARRAY"]:
                return "." + ext.lower()

        return "." + parts[-1].lower()

    # 从 node_type 推断
    node_type_lower = node_type.lower()
    if "database" in node_type_lower or "db" in node_type_lower:
        return ".db"
    elif "table" in node_type_lower:
        return ".table"
    elif "view" in node_type_lower:
        return ".view"
    elif "column" in node_type_lower:
        return ".col"
    elif "serialized" in node_type_lower:
        if ".json" in name.lower():
            return ".json"
        elif ".yaml" in name.lower() or ".yml" in name.lower():
            return ".yaml"

    return "file"


# ========== Meta 格式化 ==========


def _format_detail(key: str, text: str, max_lines: Optional[int] = None) -> str:
    """格式化 detail/brief：多行缩进，单行紧凑，可选截断。"""
    raw_lines = text.split("\n")
    total_lines = len(raw_lines)

    # 截断
    if max_lines and total_lines > max_lines:
        show_lines = raw_lines[:max_lines]
        truncated = True
    else:
        show_lines = raw_lines
        truncated = False

    # 单行：紧凑显示
    if total_lines == 1 and not truncated:
        return f"{key}: {text}"

    # 多行：首行带 key，后续行缩进对齐
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
        # If default_keys matched nothing, show all
        if not keys:
            keys = sorted(meta_dict.keys())

    lines = []
    for key in keys:
        value = meta_dict[key]
        if key in config.folded_keys and not show_all:
            lines.append(f"{key}: {_fold_summary(key, value)}")
        elif key in ("detail", "brief"):
            # 全量显示时截断，单独 property 时不截断
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
