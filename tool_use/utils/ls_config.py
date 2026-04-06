"""
LS Configuration - 定义每种文件类型的显示规则

规则说明：
- priority: 类型排序优先级（数字越小越靠前）
- info_template: Info字段的字符串模板，使用 {field_name} 占位符
  例如: "{structure_type} with {array_length} items"
- brief_field: brief字段从哪个元属性读取
- ls_only_if_has_children: 只有 has_sub=[+] 时才允许 ls（默认 True）
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass


@dataclass
class TypeConfig:
    """单个类型的配置"""
    priority: int  # 排序优先级，数字越小越靠前
    info_template: str  # Info字段的字符串模板，如 "{count} pairs"
    brief_field: str = "short_summary"  # brief来源字段
    max_str_len: int = 30  # 字符串最大显示长度
    ls_only_if_has_children: bool = True  # 只有 has_sub=[+] 时才允许 ls
    info_max_len: Optional[int] = None  # Info字段最大长度，None表示无限制
    brief_max_len: Optional[int] = None  # Brief字段最大长度，None表示无限制
    name_max_len: Optional[int] = None  # Name字段最大长度，None表示无限制


# LS 全局配置 - 使用字符串模板而非字段列表
LS_CONFIG = {
    # ========== 容器类型（高优先级）==========
    ".db": TypeConfig(
        priority=1,
        info_template="{table_count} tables, {view_count} views",
        brief_field="short_summary"
    ),

    ".database": TypeConfig(
        priority=1,
        info_template="{table_count} tables, {view_count} views",
        brief_field="short_summary"
    ),

    ".table": TypeConfig(
        priority=2,
        info_template="{row_count} rows, {column_count} cols",
        brief_field="short_summary"
    ),

    ".view": TypeConfig(
        priority=3,
        info_template="{column_count} cols",
        brief_field="short_summary"
    ),

    # 目录
    "directory": TypeConfig(
        priority=4,
        info_template="{child_count} children",
        brief_field="short_summary"
    ),

    # ========== 序列化文件 ==========

    ".json": TypeConfig(
        priority=10,
        info_template="{structure_type} with {array_length} items, {line_count} lines",
        brief_field="short_summary",
        max_str_len=50
    ),

    ".yaml": TypeConfig(
        priority=10,
        info_template="{structure_type} with {line_count} lines",
        brief_field="short_summary",
        max_str_len=50
    ),

    ".yml": TypeConfig(
        priority=10,
        info_template="{structure_type} with {line_count} lines",
        brief_field="short_summary",
        max_str_len=50
    ),

    ".xml": TypeConfig(
        priority=10,
        info_template="{line_count} lines, {char_count} chars",
        brief_field="short_summary",
        max_str_len=50
    ),

    ".toml": TypeConfig(
        priority=10,
        info_template="{line_count} lines",
        brief_field="short_summary",
        max_str_len=50
    ),

    ".hcl": TypeConfig(
        priority=10,
        info_template="{line_count} lines",
        brief_field="short_summary",
        max_str_len=50
    ),

    # ========== 文本文件 ==========

    ".md": TypeConfig(
        priority=20,
        info_template="{line_count} lines, {char_count} chars",
        brief_field="short_summary",
        max_str_len=100
    ),

    ".markdown": TypeConfig(
        priority=20,
        info_template="{line_count} lines, {char_count} chars",
        brief_field="short_summary",
        max_str_len=100
    ),

    ".txt": TypeConfig(
        priority=21,
        info_template="{line_count} lines, {char_count} chars",
        brief_field="short_summary",
        max_str_len=100
    ),

    # ========== 列类型 ==========
    ".col": TypeConfig(
        priority=30,
        info_template="Distinct: {cardinality}, null: {null_percentage:.1f}%",
        brief_field="short_summary"
    ),

    # ========== 数据采样 ==========
    ".sample": TypeConfig(
        priority=40,
        info_template="{sample_count} samples",
        brief_field="short_summary"
    ),

    ".topk": TypeConfig(
        priority=40,
        info_template="{topk_count} top values",
        brief_field="short_summary"
    ),

    # ========== 关系/外键 ==========
    ".fk": TypeConfig(
        priority=50,
        info_template="{source_table} -> {target_table}",
        brief_field="short_summary",
        ls_only_if_has_children=False  # 外键本身不需要子节点才能ls
    ),

    ".rel": TypeConfig(
        priority=50,
        info_template="{relation_type}",
        brief_field="short_summary",
        ls_only_if_has_children=False
    ),

    ".flow": TypeConfig(
        priority=50,
        info_template="{flow_type}",
        brief_field="short_summary",
        ls_only_if_has_children=False
    ),

    # ========== 块/片段 ==========
    ".chunk": TypeConfig(
        priority=60,
        info_template="{char_count} chars, {token_count} tokens",
        brief_field="short_summary"
    ),

    # ========== 默认/其他 ==========
    "file": TypeConfig(
        priority=100,
        info_template="{file_size}",
        brief_field="short_summary",
        ls_only_if_has_children=False  # 普通文件不需要子节点
    ),

    # ========== 序列化文件内部类型 ==========
    ".int": TypeConfig(
        priority=200,
        info_template="{value}",
        brief_field="short_summary",
        ls_only_if_has_children=True  # 标量类型，没有子节点，不能 ls
    ),

    ".str": TypeConfig(
        priority=201,
        info_template="{value}",
        brief_field="short_summary",
        max_str_len=30,
        ls_only_if_has_children=True  # 标量类型，不能 ls
    ),

    ".string": TypeConfig(
        priority=201,
        info_template="{value}",
        brief_field="short_summary",
        max_str_len=30,
        ls_only_if_has_children=True
    ),

    ".bool": TypeConfig(
        priority=202,
        info_template="{value}",
        brief_field="short_summary",
        ls_only_if_has_children=True
    ),

    ".float": TypeConfig(
        priority=203,
        info_template="{value}",
        brief_field="short_summary",
        ls_only_if_has_children=True
    ),

    ".double": TypeConfig(
        priority=203,
        info_template="{value}",
        brief_field="short_summary",
        ls_only_if_has_children=True
    ),

    ".dict": TypeConfig(
        priority=10,
        info_template="{count} pairs",
        brief_field="short_summary",
        ls_only_if_has_children=True
    ),

    ".list": TypeConfig(
        priority=11,
        info_template="{count} items",
        brief_field="short_summary",
        ls_only_if_has_children=True
    ),

    ".array": TypeConfig(
        priority=11,
        info_template="{count} items",
        brief_field="short_summary",
        ls_only_if_has_children=True
    ),
}


# 序列化文件内部的类型配置（JSON/YAML虚拟目录）
# 使用字符串模板而非lambda
SERIALIZED_TYPE_CONFIG = {
    "DICT": {
        "info_template": "{count} pairs",  # {count} 会被替换为 len(value)
        "has_children": True
    },
    "LIST": {
        "info_template": "{count} items",
        "has_children": True
    },
    "ARRAY": {
        "info_template": "{count} items",
        "has_children": True
    },
    "STR": {
        "info_template": "{value}",  # {value} 会被格式化为字符串
        "has_children": False
    },
    "INT": {
        "info_template": "{value}",
        "has_children": False
    },
    "FLOAT": {
        "info_template": "{value:.4f}",
        "has_children": False
    },
    "BOOL": {
        "info_template": "{value}",
        "has_children": False
    },
    "NULL": {
        "info_template": "null",
        "has_children": False
    },
}


def format_info_from_template(template: str, data: Dict[str, Any], max_str_len: int = 30) -> str:
    """
    使用字符串模板格式化Info字段

    Args:
        template: 字符串模板，如 "{structure_type} with {array_length} items"
        data: 元数据字典
        max_str_len: 字符串最大显示长度

    Returns:
        格式化后的字符串
    """
    try:
        # 预处理数据：格式化特殊值
        formatted_data = {}
        for key, value in data.items():
            if value is None:
                formatted_data[key] = "-"
            elif key == "file_size" and isinstance(value, int):
                # 文件大小特殊格式化
                formatted_data[key] = _format_file_size(value)
            elif isinstance(value, float):
                formatted_data[key] = value
            elif isinstance(value, int):
                formatted_data[key] = value
            elif isinstance(value, str):
                # 字符串超长时显示长度
                if len(value) > max_str_len:
                    formatted_data[key] = f"({len(value)} chars)"
                else:
                    formatted_data[key] = value
            else:
                formatted_data[key] = str(value)

        # 使用模板格式化
        result = template.format(**formatted_data)
        return result if result.strip() else "-"
    except (KeyError, ValueError):
        # 模板中的字段缺失，尝试部分格式化
        try:
            # 只使用存在的字段，并进行预处理
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
            # 为缺失的字段提供默认值
            import re
            field_names = re.findall(r'\{(\w+)', template)
            for field in field_names:
                if field not in available_fields:
                    available_fields[field] = "-"
            result = template.format(**available_fields)
            return result if result.strip() else "-"
        except:
            return "-"


def format_serialized_info(node_type: str, value: Any, max_str_len: int = 30) -> str:
    """
    格式化序列化文件内部的Info字段

    Args:
        node_type: 节点类型 (DICT, LIST, STR, INT, etc.)
        value: 节点值
        max_str_len: 字符串最大长度

    Returns:
        格式化后的字符串
    """
    config = SERIALIZED_TYPE_CONFIG.get(node_type)
    if not config:
        return str(value)[:max_str_len] if value is not None else "-"

    template = config["info_template"]

    # 准备数据
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


def _format_str_value(value: Any, max_len: int = 30) -> str:
    """格式化字符串值，超长时显示长度"""
    if value is None:
        return "null"
    s = str(value)
    if len(s) > max_len:
        return f"({len(s)} chars)"
    return s


def get_type_config(file_type: str) -> TypeConfig:
    """获取类型的配置"""
    # 标准化类型名
    file_type = file_type.lower()
    if not file_type.startswith("."):
        file_type = "." + file_type

    return LS_CONFIG.get(file_type, LS_CONFIG["file"])


def format_info_from_meta(meta: Dict[str, Any], config: TypeConfig) -> str:
    """根据配置从元数据格式化Info字段 - 使用字符串模板"""
    return format_info_from_template(config.info_template, meta, config.max_str_len)


def _format_file_size(size: int) -> str:
    """格式化文件大小"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def get_brief_from_meta(meta: Dict[str, Any], config: TypeConfig) -> str:
    """从元数据获取brief字段"""
    brief = meta.get(config.brief_field)
    if brief:
        return str(brief)
    return ""


def can_ls_node(has_children: bool, config: TypeConfig) -> bool:
    """
    检查节点是否可以执行 ls 命令

    Args:
        has_children: 是否有子节点 (has_sub=[+])
        config: 类型配置

    Returns:
        是否允许 ls
    """
    if config.ls_only_if_has_children:
        return has_children
    return True


def get_file_type_from_name(name: str, node_type: str = "") -> str:
    """从文件名提取类型"""
    # 处理序列化文件内部的类型 (e.g., "question_id.INT" -> ".INT")
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

    # 从node_type推断
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
        # 从name推断
        if ".json" in name.lower():
            return ".json"
        elif ".yaml" in name.lower() or ".yml" in name.lower():
            return ".yaml"

    return "file"
