"""
Display Configuration - 纯静态配置

包含：
- LS_TYPE_CONFIG: ls/glob/search 的显示配置
- META_TYPE_CONFIG: meta 命令的显示配置
"""
from typing import Dict, List, Optional, Any
from dataclasses import dataclass


@dataclass
class TypeConfig:
    """ls/glob/search 的类型配置"""
    priority: int
    info_template: str
    brief_field: str = "short_summary"
    max_str_len: int = 30
    ls_only_if_has_children: bool = True
    info_max_len: Optional[int] = None
    brief_max_len: Optional[int] = None
    name_max_len: Optional[int] = None


@dataclass
class MetaTypeConfig:
    """meta 命令的类型配置"""
    default_keys: List[str]
    show_value: bool = False
    max_value_len: Optional[int] = 100


# ========== LS 类型配置 ==========
LS_TYPE_CONFIG = {
    ".db": TypeConfig(
        priority=1,
        info_template="{table_count} tables, {view_count} views",
    ),
    ".database": TypeConfig(
        priority=1,
        info_template="{table_count} tables, {view_count} views",
    ),
    ".table": TypeConfig(
        priority=2,
        info_template="{row_count} rows, {column_count} cols",
    ),
    ".view": TypeConfig(
        priority=3,
        info_template="{column_count} cols",
    ),
    "directory": TypeConfig(
        priority=4,
        info_template="{child_count} children",
    ),
    ".json": TypeConfig(
        priority=10,
        info_template="{structure_type} with {array_length} items, {line_count} lines",
        max_str_len=50
    ),
    ".yaml": TypeConfig(
        priority=10,
        info_template="{structure_type} with {line_count} lines",
        max_str_len=50
    ),
    ".yml": TypeConfig(
        priority=10,
        info_template="{structure_type} with {line_count} lines",
        max_str_len=50
    ),
    ".xml": TypeConfig(
        priority=10,
        info_template="{line_count} lines, {char_count} chars",
        max_str_len=50
    ),
    ".toml": TypeConfig(
        priority=10,
        info_template="{line_count} lines",
        max_str_len=50
    ),
    ".hcl": TypeConfig(
        priority=10,
        info_template="{line_count} lines",
        max_str_len=50
    ),
    ".md": TypeConfig(
        priority=20,
        info_template="{line_count} lines, {char_count} chars",
        max_str_len=100
    ),
    ".markdown": TypeConfig(
        priority=20,
        info_template="{line_count} lines, {char_count} chars",
        max_str_len=100
    ),
    ".txt": TypeConfig(
        priority=21,
        info_template="{line_count} lines, {char_count} chars",
        max_str_len=100
    ),
    ".col": TypeConfig(
        priority=30,
        info_template="Distinct: {cardinality}, null: {null_percentage:.1f}%",
    ),
    ".sample": TypeConfig(
        priority=40,
        info_template="{sample_count} samples",
    ),
    ".topk": TypeConfig(
        priority=40,
        info_template="{topk_count} top values",
    ),
    ".fk": TypeConfig(
        priority=50,
        info_template="{source_table} -> {target_table}",
        ls_only_if_has_children=False
    ),
    ".rel": TypeConfig(
        priority=50,
        info_template="{relation_type}",
        ls_only_if_has_children=False
    ),
    ".flow": TypeConfig(
        priority=50,
        info_template="{flow_type}",
        ls_only_if_has_children=False
    ),
    ".chunk": TypeConfig(
        priority=60,
        info_template="{char_count} chars, {token_count} tokens",
    ),
    "file": TypeConfig(
        priority=100,
        info_template="{file_size}",
        ls_only_if_has_children=False
    ),
    # 序列化文件内部类型
    ".int": TypeConfig(priority=200, info_template="{value}", ls_only_if_has_children=True),
    ".str": TypeConfig(priority=201, info_template="{value}", max_str_len=30, ls_only_if_has_children=True),
    ".string": TypeConfig(priority=201, info_template="{value}", max_str_len=30, ls_only_if_has_children=True),
    ".bool": TypeConfig(priority=202, info_template="{value}", ls_only_if_has_children=True),
    ".float": TypeConfig(priority=203, info_template="{value}", ls_only_if_has_children=True),
    ".double": TypeConfig(priority=203, info_template="{value}", ls_only_if_has_children=True),
    ".dict": TypeConfig(priority=10, info_template="{count} pairs", ls_only_if_has_children=True),
    ".list": TypeConfig(priority=11, info_template="{count} items", ls_only_if_has_children=True),
    ".array": TypeConfig(priority=11, info_template="{count} items", ls_only_if_has_children=True),
}


# 序列化文件内部类型的简单配置
SERIALIZED_TYPE_CONFIG = {
    "DICT": {"info_template": "{count} pairs", "has_children": True},
    "LIST": {"info_template": "{count} items", "has_children": True},
    "ARRAY": {"info_template": "{count} items", "has_children": True},
    "STR": {"info_template": "{value}", "has_children": False},
    "INT": {"info_template": "{value}", "has_children": False},
    "FLOAT": {"info_template": "{value:.4f}", "has_children": False},
    "BOOL": {"info_template": "{value}", "has_children": False},
    "NULL": {"info_template": "null", "has_children": False},
}


# ========== Meta 类型配置 ==========
META_TYPE_CONFIG = {
    "Database": MetaTypeConfig(
        default_keys=["name", "type", "table_count", "view_count", "short_summary"],
    ),
    "Table": MetaTypeConfig(
        default_keys=["name", "type", "row_count", "column_count", "short_summary"],
    ),
    "View": MetaTypeConfig(
        default_keys=["name", "type", "column_count", "short_summary"],
    ),
    "Column": MetaTypeConfig(
        default_keys=["name", "type", "data_type", "cardinality", "null_percentage", "short_summary"],
    ),
    "Serialized": MetaTypeConfig(
        default_keys=["name", "type", "structure_type", "array_length", "line_count", "file_size"],
    ),
    "DICT": MetaTypeConfig(default_keys=["name", "type", "count"]),
    "LIST": MetaTypeConfig(default_keys=["name", "type", "count"]),
    "ARRAY": MetaTypeConfig(default_keys=["name", "type", "count"]),
    "STR": MetaTypeConfig(default_keys=["name", "type", "value"], show_value=True, max_value_len=50),
    "INT": MetaTypeConfig(default_keys=["name", "type", "value"], show_value=True),
    "FLOAT": MetaTypeConfig(default_keys=["name", "type", "value"], show_value=True),
    "BOOL": MetaTypeConfig(default_keys=["name", "type", "value"], show_value=True),
    "NULL": MetaTypeConfig(default_keys=["name", "type"]),
    "File": MetaTypeConfig(
        default_keys=["name", "type", "file_size", "line_count"],
    ),
    "Directory": MetaTypeConfig(
        default_keys=["name", "type", "child_count"],
    ),
    "Document": MetaTypeConfig(
        default_keys=["name", "type", "line_count", "char_count", "short_summary"],
    ),
    "default": MetaTypeConfig(
        default_keys=["name", "type"],
    ),
}
