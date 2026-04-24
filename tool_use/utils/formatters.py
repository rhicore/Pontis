"""
Formatters - 共用格式化逻辑

包含：
- glob/search 的 info 格式化
- meta 的属性格式化
"""
import re
from typing import Dict, Any, List, Optional

from tool_use.config import (
    InfoTypeConfig, MetaTypeConfig,
    INFO_TYPE_CONFIG, META_TYPE_CONFIG,
)


# ========== 配置获取 ==========

def get_type_config(file_type: str) -> InfoTypeConfig:
    """获取文件类型的 info 配置"""
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


# ========== 通用工具 ==========

def _format_file_size(size: int) -> str:
    """格式化文件大小"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# ========== Info 格式化 (glob/search) ==========

def format_info_from_template(template: str, data: Dict[str, Any], max_str_len: int = 30) -> str:
    """使用字符串模板格式化 Info 字段"""
    try:
        formatted_data = {}
        for key, value in data.items():
            if value is None:
                formatted_data[key] = "-"
            elif key == "file_size" and isinstance(value, int):
                formatted_data[key] = _format_file_size(value)
            elif isinstance(value, (int, float)):
                formatted_data[key] = value
            elif isinstance(value, str):
                formatted_data[key] = value if len(value) <= max_str_len else f"({len(value)} chars)"
            else:
                formatted_data[key] = str(value)

        # 填充缺失字段为 "-"
        field_names = re.findall(r'\{(\w+)', template)
        for field in field_names:
            if field not in formatted_data:
                formatted_data[field] = "-"

        result = template.format(**formatted_data)
        return result.strip() or "-"
    except (KeyError, ValueError):
        return "-"


def format_info_from_meta(meta: Dict[str, Any], config: InfoTypeConfig) -> str:
    """根据配置从元数据格式化 Info 字段"""
    return format_info_from_template(config.info_template, meta, config.max_str_len)


# ========== Meta 格式化 ==========


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
        # sample / topk 等列表
        items = []
        for item in value[:10]:
            if isinstance(item, dict):
                items.append(str(item))
            else:
                items.append(str(item))
        text = ", ".join(items)
        if len(value) > 10:
            text += f" ... ({len(value)} total)"
        return text
    s = str(value)
    if max_len and len(s) > max_len:
        return s[:max_len] + "..."
    return s
