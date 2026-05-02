"""
Display Configuration - 工具显示配置

包含：
- TOOL_PAGINATION: 各工具的分页默认值
- INFO_TYPE_CONFIG: glob/search 的 info 显示模板
- META_TYPE_CONFIG: meta 命令的显示配置（统一按后缀名匹配）
- ENTITY_DEPENDENCY_RANK: 实体依赖层级，用于应用层级联删除
"""
from typing import Callable, Dict, List, Optional, Set
from dataclasses import dataclass, field


# ========== 分页配置 ==========

@dataclass
class PaginationConfig:
    """工具分页配置"""
    default_limit: int      # 默认每页条数
    max_limit: int          # 最大每页条数


TOOL_PAGINATION = {
    "glob":   PaginationConfig(default_limit=20, max_limit=500),
    "search": PaginationConfig(default_limit=100, max_limit=500),
    "grep":   PaginationConfig(default_limit=250, max_limit=1000),
    "lookup": PaginationConfig(default_limit=50,  max_limit=200),
}


@dataclass
class InfoTypeConfig:
    """glob/search 的类型显示配置"""
    info_fn: Callable[[Dict], str]
    max_str_len: int = 30


@dataclass
class MetaTypeConfig:
    """meta 命令的类型显示配置"""
    default_keys: List[str]
    max_value_len: Optional[int] = 100
    folded_keys: Set[str] = field(default_factory=set)        # 折叠为一行摘要，需 property= 展开
    untruncated_keys: Set[str] = field(default_factory=lambda: {"detail", "brief"})  # 不截断，完整展示


# ========== 辅助函数 ==========

def _v(d, key, default="-"):
    """获取元属性值，None 时返回 default。file_size 自动格式化。"""
    val = d.get(key)
    if val is None:
        return default
    if key == "file_size" and isinstance(val, int):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if val < 1024:
                return f"{val:.1f} {unit}"
            val /= 1024
        return f"{val:.1f} TB"
    return val

def _c(d, suffix):
    """获取该实体与某类型邻接实体的数量，>0 返回 '2 rels'，否则返回 None。"""
    val = d.get(suffix)
    if isinstance(val, list) and len(val) > 0:
        return f"{len(val)} {suffix}{'s' if len(val) > 1 else ''}"
    return None


# ========== Info 类型配置 (glob/search) ==========

INFO_TYPE_CONFIG = {
    ".db": InfoTypeConfig(info_fn=lambda m: f"{_v(m, 'table_count')} tables, {_v(m, 'view_count')} views"),
    ".database": InfoTypeConfig(info_fn=lambda m: f"{_v(m, 'table_count')} tables, {_v(m, 'view_count')} views"),
    ".table": InfoTypeConfig(info_fn=lambda m: ", ".join(filter(None, [
        f"{_v(m, 'row_count')} rows, {_v(m, 'column_count')} cols",
        _c(m, 'fk'), _c(m, 'disambig'),
    ]))),
    ".view": InfoTypeConfig(info_fn=lambda m: ", ".join(filter(None, [
        f"{_v(m, 'column_count')} cols",
        _c(m, 'fk'), _c(m, 'disambig'),
    ]))),
    ".col": InfoTypeConfig(info_fn=lambda m: ", ".join(filter(None, [
        _c(m, 'rel'), _c(m, 'fk'), _c(m, 'disambig'),
    ])) or "-"),
    ".fk": InfoTypeConfig(info_fn=lambda m: _v(m, 'brief')),
    ".rel": InfoTypeConfig(info_fn=lambda m: _v(m, 'brief')),
    ".overlap": InfoTypeConfig(info_fn=lambda m: _v(m, 'brief')),
    ".disambig": InfoTypeConfig(info_fn=lambda m: _v(m, 'brief')),
    ".pattern": InfoTypeConfig(info_fn=lambda m: _v(m, "pattern"), max_str_len=80),
    ".chunk": InfoTypeConfig(info_fn=lambda m: f"{_v(m, 'char_count')} chars"),
    ".json": InfoTypeConfig(info_fn=lambda m: f"{_v(m, 'structure_type')}, {_v(m, 'line_count')} lines"),
    ".yaml": InfoTypeConfig(info_fn=lambda m: f"{_v(m, 'structure_type')}, {_v(m, 'line_count')} lines"),
    ".yml": InfoTypeConfig(info_fn=lambda m: f"{_v(m, 'structure_type')}, {_v(m, 'line_count')} lines"),
    ".csv": InfoTypeConfig(info_fn=lambda m: f"{_v(m, 'row_count')} rows, {_v(m, 'column_count')} cols"),
    ".tsv": InfoTypeConfig(info_fn=lambda m: f"{_v(m, 'row_count')} rows, {_v(m, 'column_count')} cols"),
    ".md": InfoTypeConfig(info_fn=lambda m: f"{_v(m, 'line_count')} lines"),
    ".txt": InfoTypeConfig(info_fn=lambda m: f"{_v(m, 'line_count')} lines"),
    ".directory": InfoTypeConfig(info_fn=lambda m: f"{_v(m, 'file_count')} files, {_v(m, 'subdir_count')} dirs"),
    ".file": InfoTypeConfig(info_fn=lambda m: _v(m, "file_size")),
    ".dict": InfoTypeConfig(info_fn=lambda m: f"{_v(m, 'count')} keys"),
    ".list": InfoTypeConfig(info_fn=lambda m: f"{_v(m, 'count')} items"),
    ".array": InfoTypeConfig(info_fn=lambda m: f"{_v(m, 'count')} items"),
    ".str": InfoTypeConfig(info_fn=lambda m: str(_v(m, "value")), max_str_len=30),
    ".int": InfoTypeConfig(info_fn=lambda m: str(_v(m, "value"))),
    ".float": InfoTypeConfig(info_fn=lambda m: str(_v(m, "value"))),
    ".bool": InfoTypeConfig(info_fn=lambda m: str(_v(m, "value"))),
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
        default_keys=["row_count", "column_count", "primary_key", "fk", "rel", "disambig", "brief", "detail"],
    ),
    ".view": MetaTypeConfig(
        default_keys=["row_count", "column_count", "brief", "detail"],
    ),
    ".col": MetaTypeConfig(
        default_keys=["cardinality", "null_count", "null_percentage",
                       "sample",
                       "min_value", "max_value", "mean_value",
                       "min_length", "max_length", "avg_length",
                       "fk", "disambig", "brief", "detail"],
        folded_keys={"topk"},
    ),
    ".fk": MetaTypeConfig(
        default_keys=["brief", "detail"],
    ),
    ".rel": MetaTypeConfig(
        default_keys=["brief", "detail"],
    ),
    ".overlap": MetaTypeConfig(
        default_keys=["stats", "brief", "detail"],
        folded_keys={"stats"},
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


# ========== 实体依赖层级（级联删除用）==========

# 数值越高 = 越是被派生的实体，越依赖其他实体。
# 用于应用层级联删除：删除源端节点时，rank 更高的邻居也一并删除。
ENTITY_DEPENDENCY_RANK = {
    # Rank 0: 文件级（最基础）
    ".db": 0, ".sqlite": 0, ".sqlite3": 0, ".duckdb": 0,
    ".csv": 0, ".tsv": 0, ".json": 0, ".yaml": 0, ".yml": 0,
    ".md": 0, ".txt": 0, ".directory": 0, ".file": 0,
    # Rank 1: 结构级
    ".table": 1, ".view": 1,
    # Rank 2: 列级
    ".col": 2,
    # Rank 3: 派生关系
    ".fk": 3, ".rel": 3, ".overlap": 3,
    # Rank 4: 语义消歧
    ".disambig": 4,
}
