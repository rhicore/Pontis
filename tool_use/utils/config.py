"""
Display Configuration - 工具显示配置

包含：
- TOOL_PAGINATION: 各工具的分页默认值
- INFO_TYPE_CONFIG: glob/search 的 info 显示模板
- META_TYPE_CONFIG: meta 命令的显示配置（统一按后缀名匹配）
"""
from typing import List, Optional, Set
from dataclasses import dataclass, field


# ========== 分页配置 ==========

@dataclass
class PaginationConfig:
    """工具分页配置"""
    default_limit: int      # 默认每页条数
    max_limit: int          # 最大每页条数


TOOL_PAGINATION = {
    "glob":   PaginationConfig(default_limit=100, max_limit=500),
    "search": PaginationConfig(default_limit=100, max_limit=500),
    "grep":   PaginationConfig(default_limit=250, max_limit=1000),
    "lookup": PaginationConfig(default_limit=50,  max_limit=200),
}


@dataclass
class InfoTypeConfig:
    """glob/search 的类型显示配置"""
    info_template: str
    max_str_len: int = 30


@dataclass
class MetaTypeConfig:
    """meta 命令的类型显示配置"""
    default_keys: List[str]
    max_value_len: Optional[int] = 100
    folded_keys: Set[str] = field(default_factory=set)        # 折叠为一行摘要，需 property= 展开
    untruncated_keys: Set[str] = field(default_factory=lambda: {"detail", "brief"})  # 不截断，完整展示


# ========== Info 类型配置 (glob/search) ==========

INFO_TYPE_CONFIG = {
    ".db": InfoTypeConfig(info_template="{table_count} tables, {view_count} views"),
    ".database": InfoTypeConfig(info_template="{table_count} tables, {view_count} views"),
    ".table": InfoTypeConfig(info_template="{row_count} rows, {column_count} cols"),
    ".view": InfoTypeConfig(info_template="{column_count} cols"),
    ".col": InfoTypeConfig(info_template="Distinct: {cardinality}, null: {null_percentage}%"),
    ".fk": InfoTypeConfig(info_template="{from_table}.{from_column} → {to_table}.{to_column}"),
    ".rel": InfoTypeConfig(info_template="{relation_type}"),
    ".overlap": InfoTypeConfig(info_template="jaccard={jaccard_score}"),
    ".pattern": InfoTypeConfig(info_template="{pattern}", max_str_len=80),
    ".chunk": InfoTypeConfig(info_template="{char_count} chars"),
    ".json": InfoTypeConfig(info_template="{structure_type}, {line_count} lines"),
    ".yaml": InfoTypeConfig(info_template="{structure_type}, {line_count} lines"),
    ".yml": InfoTypeConfig(info_template="{structure_type}, {line_count} lines"),
    ".csv": InfoTypeConfig(info_template="{row_count} rows, {column_count} cols"),
    ".tsv": InfoTypeConfig(info_template="{row_count} rows, {column_count} cols"),
    ".md": InfoTypeConfig(info_template="{line_count} lines"),
    ".txt": InfoTypeConfig(info_template="{line_count} lines"),
    ".directory": InfoTypeConfig(info_template="{file_count} files, {subdir_count} dirs"),
    ".file": InfoTypeConfig(info_template="{file_size}"),
    ".dict": InfoTypeConfig(info_template="{count} keys"),
    ".list": InfoTypeConfig(info_template="{count} items"),
    ".array": InfoTypeConfig(info_template="{count} items"),
    ".str": InfoTypeConfig(info_template="{value}", max_str_len=30),
    ".int": InfoTypeConfig(info_template="{value}"),
    ".float": InfoTypeConfig(info_template="{value}"),
    ".bool": InfoTypeConfig(info_template="{value}"),
}


# ========== Meta 类型配置（统一按后缀名匹配）==========

META_TYPE_CONFIG = {
    # 文件级
    ".db": MetaTypeConfig(
        default_keys=["table_count", "view_count", "index_count", "file_size", "brief", "detail"],
    ),
    ".json": MetaTypeConfig(
        default_keys=["structure_type", "key_count", "array_length", "top_level_keys", "line_count", "char_count", "file_size", "brief", "detail"],
        folded_keys={"top_level_keys"},
    ),
    ".yaml": MetaTypeConfig(
        default_keys=["structure_type", "key_count", "top_level_keys", "sequence_length", "line_count", "char_count", "file_size", "brief", "detail"],
        folded_keys={"top_level_keys"},
    ),
    ".yml": MetaTypeConfig(
        default_keys=["structure_type", "key_count", "top_level_keys", "sequence_length", "line_count", "char_count", "file_size", "brief", "detail"],
        folded_keys={"top_level_keys"},
    ),
    ".csv": MetaTypeConfig(
        default_keys=["row_count", "column_count", "delimiter", "line_count", "char_count", "file_size"],
    ),
    ".tsv": MetaTypeConfig(
        default_keys=["row_count", "column_count", "line_count", "char_count", "file_size"],
    ),
    ".md": MetaTypeConfig(
        default_keys=["line_count", "char_count", "non_empty_line_count", "avg_line_length", "max_line_length", "file_size", "brief", "detail"],
    ),
    ".txt": MetaTypeConfig(
        default_keys=["line_count", "char_count", "non_empty_line_count", "avg_line_length", "max_line_length", "file_size", "brief", "detail"],
    ),
    # 实体级
    ".table": MetaTypeConfig(
        default_keys=["row_count", "column_count", "primary_key", "brief", "detail"],
    ),
    ".view": MetaTypeConfig(
        default_keys=["row_count", "column_count", "brief", "detail"],
    ),
    ".col": MetaTypeConfig(
        default_keys=["cardinality", "null_count", "null_percentage", "source_table",
                       "min_value", "max_value", "mean_value",
                       "min_length", "max_length", "avg_length",
                       "min", "max", "mean",
                       "detail"],
        folded_keys={"sample", "topk"},
    ),
    ".fk": MetaTypeConfig(
        default_keys=["relation_type", "from_table", "from_column", "to_table", "to_column", "confidence"],
    ),
    ".rel": MetaTypeConfig(
        default_keys=["relation_type", "from_table", "from_column", "to_table", "to_column",
                       "confidence", "can_join", "reason"],
    ),
    ".overlap": MetaTypeConfig(
        default_keys=["relation_type", "from_table", "from_column", "to_table", "to_column",
                       "match_type", "reason"],
    ),
    ".pattern": MetaTypeConfig(
        default_keys=["name", "pattern"],
    ),
    ".chunk": MetaTypeConfig(
        default_keys=["char_count"],
    ),
    # 序列化文件内部类型
    ".dict": MetaTypeConfig(default_keys=["count"]),
    ".list": MetaTypeConfig(default_keys=["count"]),
    ".array": MetaTypeConfig(default_keys=["count"]),
    ".str": MetaTypeConfig(default_keys=["value"]),
    ".int": MetaTypeConfig(default_keys=["value"]),
    ".float": MetaTypeConfig(default_keys=["value"]),
    ".bool": MetaTypeConfig(default_keys=["value"]),
    # 特殊
    "directory": MetaTypeConfig(
        default_keys=["child_count", "file_count", "subdir_count"],
    ),
    "default": MetaTypeConfig(
        default_keys=["file_size", "line_count", "char_count", "detail"],
    ),
}
