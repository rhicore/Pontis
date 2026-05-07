"""Display Configuration - 工具显示配置

按标签段索引的显示配置。
INFO_TYPE_CONFIG: label segment → info_fn (returns dict)
META_TYPE_CONFIG: label segment → MetaTypeConfig
resolve_info(): 按标签段叠加 info dict
resolve_meta_config(): 按标签段合并 MetaTypeConfig
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
}


@dataclass
class InfoTypeConfig:
    """glob/search 的类型显示配置。info_fn 返回 dict。"""
    info_fn: Callable[[Dict], Dict]
    max_str_len: int = 30


@dataclass
class MetaTypeConfig:
    """meta 命令的类型显示配置"""
    default_keys: List[str]
    max_value_len: Optional[int] = 100
    max_detail_lines: Optional[int] = 15
    folded_keys: Set[str] = field(default_factory=set)
    untruncated_keys: Set[str] = field(default_factory=lambda: {"detail", "brief"})


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


# ========== Info 类型配置 (glob/search) — 按标签段索引 ==========

INFO_TYPE_CONFIG = {
    # 文件段
    "file":     InfoTypeConfig(info_fn=lambda m: {"size": _v(m, "file_size")}),
    "db":       InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m,'table_count')} tables, {_v(m,'view_count')} views"}),
    "csv":      InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m,'row_count')} rows, {_v(m,'column_count')} cols"}),
    "tsv":      InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m,'row_count')} rows, {_v(m,'column_count')} cols"}),
    "json":     InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m,'structure_type')}, {_v(m,'line_count')} lines"}),
    "yaml":     InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m,'structure_type')}, {_v(m,'line_count')} lines"}),
    "md":       InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m,'line_count')} lines"}),
    "text":     InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m,'line_count')} lines"}),
    # 结构段
    "table":    InfoTypeConfig(info_fn=lambda m: {
                    "stats": f"{_v(m,'row_count')} rows, {_v(m,'column_count')} cols",
                    "links": ", ".join(filter(None, [_c(m,'fk'), _c(m,'rel')])),
                }),
    "view":     InfoTypeConfig(info_fn=lambda m: {
                    "stats": f"{_v(m,'column_count')} cols",
                    "links": ", ".join(filter(None, [_c(m,'fk'), _c(m,'disambig')])),
                }),
    "col":      InfoTypeConfig(info_fn=lambda m: {
                    "links": ", ".join(filter(None, [_c(m,'rel'), _c(m,'fk'), _c(m,'disambig')])) or "-",
                }),
    # 关系段
    "fk":       InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    "rel":      InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    "overlap":  InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    "disambig": InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    # 知识段
    "pattern":     InfoTypeConfig(info_fn=lambda m: {"pattern": _v(m, "pattern")}, max_str_len=80),
    "convention":  InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    "term":        InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    "lesson":      InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    "example":     InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    # 其他
    "chunk":    InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m, 'char_count')} chars"}),
    "directory": InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m, 'file_count')} files, {_v(m, 'subdir_count')} dirs"}),
}


def resolve_info(entity_labels: List[str], meta: dict) -> str:
    """按标签段叠加 info dict，合并后 join 为显示字符串。

    每个标签按 '/' 拆段，每段查 INFO_TYPE_CONFIG，匹配则调用 info_fn 得 dict。
    所有 dict 按 key 合并（后者覆盖前者）。最后用 ' | ' join 所有值。
    meta 中的 brief 也会被追加（若 info_fn 未提供）。
    """
    merged = {}
    for segment in entity_labels:
        if segment in INFO_TYPE_CONFIG:
            merged.update(INFO_TYPE_CONFIG[segment].info_fn(meta))
    brief = meta.get("brief")
    if brief and str(brief) != "-":
        merged["brief"] = str(brief)
    return " | ".join(str(v) for v in merged.values() if v and str(v) != "-") or "-"


# ========== Meta 类型配置 — 按标签段索引 ==========

META_TYPE_CONFIG = {
    # 文件段
    "file": MetaTypeConfig(
        default_keys=["file_size", "brief", "detail"],
    ),
    "db": MetaTypeConfig(
        default_keys=["table_count", "view_count", "index_count", "file_size", "brief", "detail"],
    ),
    "csv": MetaTypeConfig(
        default_keys=["row_count", "column_count", "delimiter", "line_count", "char_count", "file_size"],
    ),
    "tsv": MetaTypeConfig(
        default_keys=["row_count", "column_count", "line_count", "char_count", "file_size"],
    ),
    "json": MetaTypeConfig(
        default_keys=["structure_type", "key_count", "array_length", "top_level_keys", "line_count", "char_count", "file_size", "brief", "detail"],
        folded_keys={"top_level_keys"},
    ),
    "yaml": MetaTypeConfig(
        default_keys=["structure_type", "key_count", "top_level_keys", "sequence_length", "line_count", "char_count", "file_size", "brief", "detail"],
        folded_keys={"top_level_keys"},
    ),
    "md": MetaTypeConfig(
        default_keys=["line_count", "char_count", "non_empty_line_count", "avg_line_length", "max_line_length", "file_size", "brief", "detail"],
    ),
    "text": MetaTypeConfig(
        default_keys=["line_count", "char_count", "non_empty_line_count", "avg_line_length", "max_line_length", "file_size", "brief", "detail"],
    ),
    # 结构段
    "table": MetaTypeConfig(
        default_keys=["row_count", "column_count", "primary_key", "fk", "rel", "disambig", "brief", "detail"],
    ),
    "view": MetaTypeConfig(
        default_keys=["row_count", "column_count", "brief", "detail"],
    ),
    "col": MetaTypeConfig(
        default_keys=["cardinality", "null_count", "null_percentage",
                       "sample",
                       "min_value", "max_value", "mean_value",
                       "min_length", "max_length", "avg_length",
                       "fk", "disambig", "brief", "detail"],
        folded_keys={"topk"},
    ),
    # 关系段
    "fk": MetaTypeConfig(
        default_keys=["brief", "detail"],
    ),
    "rel": MetaTypeConfig(
        default_keys=["brief", "detail"],
    ),
    "overlap": MetaTypeConfig(
        default_keys=["stats", "brief", "detail"],
        folded_keys={"stats"},
    ),
    "disambig": MetaTypeConfig(
        default_keys=["level", "brief", "detail"],
    ),
    # 知识段
    "pattern": MetaTypeConfig(
        default_keys=["name", "pattern"],
    ),
    "convention": MetaTypeConfig(
        default_keys=["brief", "detail"],
    ),
    "term": MetaTypeConfig(
        default_keys=["brief", "detail"],
    ),
    "lesson": MetaTypeConfig(
        default_keys=["brief", "detail"],
    ),
    "example": MetaTypeConfig(
        default_keys=["brief", "detail"],
    ),
    # 其他
    "chunk": MetaTypeConfig(
        default_keys=["char_count"],
    ),
    "directory": MetaTypeConfig(
        default_keys=["child_count", "file_count", "subdir_count"],
    ),
    # 兜底
    "default": MetaTypeConfig(
        default_keys=["file_size", "line_count", "char_count", "detail"],
    ),
}


def resolve_meta_config(entity_labels: List[str]) -> MetaTypeConfig:
    """从实体标签合并 META_TYPE_CONFIG。

    每个标签按 '/' 拆段，每段查 META_TYPE_CONFIG。
    多标签取 default_keys 并集（保持顺序）、folded_keys 并集。
    """
    merged_keys = []
    merged_folded: Set[str] = set()
    seen_keys: Set[str] = set()

    for segment in entity_labels:
        if segment in META_TYPE_CONFIG:
                cfg = META_TYPE_CONFIG[segment]
                for k in cfg.default_keys:
                    if k not in seen_keys:
                        merged_keys.append(k)
                        seen_keys.add(k)
                merged_folded.update(cfg.folded_keys)

    if not merged_keys:
        return META_TYPE_CONFIG["default"]

    return MetaTypeConfig(
        default_keys=merged_keys,
        folded_keys=merged_folded,
    )


# ========== 实体依赖层级（级联删除用）— 按标签段索引 ==========

ENTITY_DEPENDENCY_RANK = {
    # Rank 0: 文件级（最基础）
    "file": 0, "db": 0, "csv": 0, "tsv": 0, "json": 0,
    "yaml": 0, "md": 0, "text": 0, "directory": 0,
    # Rank 1: 结构级
    "table": 1, "view": 1,
    # Rank 2: 列级
    "col": 2,
    # Rank 3: 派生关系
    "fk": 3, "rel": 3, "overlap": 3,
    # Rank 4: 语义消歧
    "disambig": 4,
}


def resolve_rank(entity_labels: List[str]) -> int:
    """从标签中取最高 rank。"""
    max_rank = 0
    for segment in entity_labels:
        if segment in ENTITY_DEPENDENCY_RANK:
                max_rank = max(max_rank, ENTITY_DEPENDENCY_RANK[segment])
    return max_rank
