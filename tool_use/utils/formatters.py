"""
Formatters - 共用格式化逻辑

包含：
- LS/glob/search 的表格格式化
- Meta 的属性格式化
"""
import re
from typing import Dict, Any, List, Optional

from tool_use.utils.config import (
    TypeConfig, MetaTypeConfig,
    LS_TYPE_CONFIG, META_TYPE_CONFIG, SERIALIZED_TYPE_CONFIG
)


# ========== 配置获取 ==========

def get_type_config(file_type: str) -> TypeConfig:
    """获取文件类型的配置"""
    file_type = file_type.lower()
    if not file_type.startswith("."):
        file_type = "." + file_type
    return LS_TYPE_CONFIG.get(file_type, LS_TYPE_CONFIG["file"])


def get_meta_type_config(node_type: str) -> MetaTypeConfig:
    """获取 meta 类型的配置"""
    node_type_clean = node_type.replace("Serialized (", "").replace(")", "").strip()

    if node_type in META_TYPE_CONFIG:
        return META_TYPE_CONFIG[node_type]
    if node_type_clean in META_TYPE_CONFIG:
        return META_TYPE_CONFIG[node_type_clean]

    for key in META_TYPE_CONFIG:
        if key.lower() in node_type.lower():
            return META_TYPE_CONFIG[key]

    return META_TYPE_CONFIG["default"]


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

def can_ls_node(has_children: bool, config: TypeConfig) -> bool:
    """检查节点是否可以执行 ls 命令"""
    if config.ls_only_if_has_children:
        return has_children
    return True


def _format_file_size(size: int) -> str:
    """格式化文件大小"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _format_str_value(value: Any, max_len: int = 30) -> str:
    """格式化字符串值，超长时显示长度"""
    if value is None:
        return "null"
    s = str(value)
    if len(s) > max_len:
        return f"({len(s)} chars)"
    return s


# ========== LS 格式化 ==========

def format_info_from_template(template: str, data: Dict[str, Any], max_str_len: int = 30) -> str:
    """使用字符串模板格式化 Info 字段"""
    try:
        formatted_data = {}
        for key, value in data.items():
            if value is None:
                formatted_data[key] = "-"
            elif key == "file_size" and isinstance(value, int):
                formatted_data[key] = _format_file_size(value)
            elif isinstance(value, float):
                formatted_data[key] = value
            elif isinstance(value, int):
                formatted_data[key] = value
            elif isinstance(value, str):
                if len(value) > max_str_len:
                    formatted_data[key] = f"({len(value)} chars)"
                else:
                    formatted_data[key] = value
            else:
                formatted_data[key] = str(value)

        result = template.format(**formatted_data)
        return result if result.strip() else "-"
    except (KeyError, ValueError):
        try:
            available_fields = {}
            for k, v in data.items():
                if v is None:
                    available_fields[k] = "-"
                elif k == "file_size" and isinstance(v, int):
                    available_fields[k] = _format_file_size(v)
                elif isinstance(v, (int, float)):
                    available_fields[k] = v
                elif isinstance(v, str):
                    available_fields[k] = v if len(v) <= max_str_len else f"({len(v)} chars)"
                else:
                    available_fields[k] = str(v)

            field_names = re.findall(r'\{(\w+)', template)
            for field in field_names:
                if field not in available_fields:
                    available_fields[field] = "-"
            result = template.format(**available_fields)
            return result if result.strip() else "-"
        except:
            return "-"


def format_info_from_meta(meta: Dict[str, Any], config: TypeConfig) -> str:
    """根据配置从元数据格式化 Info 字段"""
    return format_info_from_template(config.info_template, meta, config.max_str_len)


def get_brief_from_meta(meta: Dict[str, Any], config: TypeConfig) -> str:
    """从元数据获取 brief 字段"""
    brief = meta.get(config.brief_field)
    return str(brief) if brief else ""


def format_serialized_info(node_type: str, value: Any, max_str_len: int = 30) -> str:
    """格式化序列化文件内部的 Info 字段"""
    config = SERIALIZED_TYPE_CONFIG.get(node_type)
    if not config:
        return str(value)[:max_str_len] if value is not None else "-"

    template = config["info_template"]
    data = {}

    if node_type in ("DICT",):
        data["count"] = len(value) if isinstance(value, dict) else 0
    elif node_type in ("LIST", "ARRAY"):
        data["count"] = len(value) if isinstance(value, list) else 0
    elif node_type == "STR":
        s = str(value) if value is not None else ""
        if len(s) > max_str_len:
            data["value"] = f"({len(s)} chars)"
        else:
            data["value"] = s
    elif node_type == "INT":
        data["value"] = value if value is not None else 0
    elif node_type == "FLOAT":
        data["value"] = value if value is not None else 0.0
    elif node_type == "BOOL":
        data["value"] = "true" if value else "false"
    elif node_type == "NULL":
        data["value"] = "null"
    else:
        data["value"] = str(value)[:max_str_len] if value is not None else "-"

    try:
        return template.format(**data)
    except:
        return str(value)[:max_str_len] if value is not None else "-"


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
        return f"{specific_key}: {_format_meta_value(value, config.max_value_len)}"

    if show_all:
        lines = []
        for key, value in sorted(meta_dict.items()):
            lines.append(f"{key}: {_format_meta_value(value, config.max_value_len)}")
        return "\n".join(lines)

    lines = []
    for key in config.default_keys:
        if key in meta_dict:
            value = meta_dict[key]
            lines.append(f"{key}: {_format_meta_value(value, config.max_value_len)}")

    return "\n".join(lines)


def _format_meta_value(value: Any, max_len: Optional[int] = None) -> str:
    """格式化 meta 值，可选截断"""
    s = str(value)
    if max_len and len(s) > max_len:
        return s[:max_len] + "..."
    return s


# ========== 表格渲染（ls/glob/search 共用）==========

def format_node_table(nodes: List[Any], show_brief: bool = True) -> str:
    """
    格式化节点列表为 4 列表格

    Args:
        nodes: 节点对象列表（需要有 name, has_children, node_type, meta 等属性）
        show_brief: 是否显示 Brief 列

    Returns:
        格式化的表格字符串
    """
    if not nodes:
        return "(empty)"

    # 按类型排序
    sorted_nodes = _sort_nodes(nodes)

    # 计算列宽
    has_sub_width = 3
    name_width = max(len(_get_node_display_name(n)) for n in sorted_nodes) if sorted_nodes else 4
    name_width = max(name_width, 4)  # 最小宽度

    # 生成行
    lines = []
    for node in sorted_nodes:
        has_sub = "[+]" if node.has_children else "[ ]"
        name = _get_node_display_name(node)
        name = name[:50]  # 限制最大长度
        name_padded = name.ljust(name_width)

        # Info 列
        info = _get_node_info(node)

        # Brief 列
        if show_brief:
            brief = _get_node_brief(node)
            lines.append(f"{has_sub} {name_padded}  {info}  {brief}")
        else:
            lines.append(f"{has_sub} {name_padded}  {info}")

    return "\n".join(lines) if lines else "(empty)"


def _sort_nodes(nodes: List[Any]) -> List[Any]:
    """按类型优先级排序节点"""
    def sort_key(node):
        name = _get_node_display_name(node)
        file_type = get_file_type_from_name(name, getattr(node, 'node_type', ''))
        config = get_type_config(file_type)

        # 提取数字用于自然排序
        base_name = name
        num = 0
        if '.' in name:
            parts = name.rsplit('.', 1)
            if parts[1].isdigit():
                base_name = parts[0]
                num = int(parts[1])

        return (config.priority, base_name, num)

    return sorted(nodes, key=sort_key)


def _get_node_display_name(node: Any) -> str:
    """获取节点的显示名称"""
    return getattr(node, 'display_name', getattr(node, 'name', str(node)))


def _get_node_info(node: Any) -> str:
    """获取节点的 Info 字符串"""
    # 优先使用预计算的 info
    info = getattr(node, 'info', None)
    if info:
        return info

    # 从 meta 计算
    meta = getattr(node, 'meta', {})
    if meta:
        name = _get_node_display_name(node)
        file_type = get_file_type_from_name(name, getattr(node, 'node_type', ''))
        config = get_type_config(file_type)
        return format_info_from_meta(meta, config)

    return "-"


def _get_node_brief(node: Any) -> str:
    """获取节点的 Brief 字符串"""
    meta = getattr(node, 'meta', {})
    if meta:
        name = _get_node_display_name(node)
        file_type = get_file_type_from_name(name, getattr(node, 'node_type', ''))
        config = get_type_config(file_type)
        return get_brief_from_meta(meta, config)
    return ""
