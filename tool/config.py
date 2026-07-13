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
    "find":   PaginationConfig(default_limit=50, max_limit=500),
    "grep":   PaginationConfig(default_limit=250, max_limit=1000),
}


@dataclass
class InfoTypeConfig:
    """find/meta 的类型显示配置。info_fn 返回 dict。"""
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
    hidden_keys: Set[str] = field(default_factory=set)
    adjacency_keys: Set[str] = field(default_factory=set)  # 默认显示的邻接类型


ALWAYS_VISIBLE_ADJACENCY_KEYS: Set[str] = {"disambig", "hint"}


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


def _first_present(d, keys, default="-"):
    for key in keys:
        val = d.get(key)
        if val is not None and str(val).strip() not in ("", "-"):
            return str(val)
    return default


def _knowledge_brief(d):
    return _first_present(
        d,
        [
            "brief",
            "mistake_summary",
            "decision_summary",
            "transfer_hint",
            "why_this_case_matters",
            "question",
        ],
    )


def _file_access(d):
    opener = d.get("_file_open") or d.get("file_open")
    labels = set(d.get("labels", []))
    if callable(opener) and "text" in labels:
        return "readable text"
    if callable(opener) and labels & {"db", "csv", "tsv"}:
        return "queryable"
    if callable(opener) and labels & {"json", "yaml"}:
        return "structured"
    return "metadata only"


def _c(d, suffix):
    """获取该实体与某类型邻接实体的数量，>0 返回 '2 rels'，否则返回 None。"""
    val = d.get(suffix)
    if isinstance(val, list) and len(val) > 0:
        return f"{len(val)} {suffix}{'s' if len(val) > 1 else ''}"
    return None


def _count_stats(d, *items):
    parts = []
    for key, label in items:
        val = d.get(key)
        if val is None or str(val).strip() in ("", "-"):
            continue
        parts.append(f"{val} {label}")
    return ", ".join(parts) or "-"


# ========== Info 类型配置 (find/meta) — 按标签段索引 ==========

INFO_TYPE_CONFIG = {
    # 文件段
    "file":     InfoTypeConfig(info_fn=lambda m: {
                    "path": _v(m, "path"), "size": _v(m, "file_size"), "access": _file_access(m)
                }),
    "db":       InfoTypeConfig(info_fn=lambda m: {"brief": _first_present(m, ["brief", "detail"], default="database")}),
    "csv":      InfoTypeConfig(info_fn=lambda m: {"stats": _count_stats(m, ("row_count", "rows"), ("column_count", "cols"))}),
    "tsv":      InfoTypeConfig(info_fn=lambda m: {"stats": _count_stats(m, ("row_count", "rows"), ("column_count", "cols"))}),
    "json":     InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m,'structure_type')}, {_v(m,'line_count')} lines"}),
    "yaml":     InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m,'structure_type')}, {_v(m,'line_count')} lines"}),
    "md":       InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m,'line_count')} lines"}),
    "text":     InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m,'line_count')} lines"}),
    # 结构段
    "table":    InfoTypeConfig(info_fn=lambda m: {
                    "stats": _count_stats(m, ("row_count", "rows")),
                    "brief": _first_present(m, ["brief", "official_table_description"], default="-"),
                    "links": ", ".join(filter(None, [_c(m,'fk'), _c(m,'rel'), _c(m,'hint')])),
                }),
    "view":     InfoTypeConfig(info_fn=lambda m: {
                    "stats": f"{_v(m,'column_count')} cols",
                    "brief": _first_present(m, ["brief", "official_view_description"], default="-"),
                    "links": ", ".join(filter(None, [_c(m,'fk'), _c(m,'disambig'), _c(m,'hint')])),
                }),
    "col":      InfoTypeConfig(info_fn=lambda m: {
                    "brief": _first_present(m, ["brief", "official_column_description", "official_value_description"], default="-"),
                    "links": ", ".join(filter(None, [_c(m,'rel'), _c(m,'fk'), _c(m,'disambig'), _c(m,'hint')])) or "-",
                }),
    # 关系段
    "fk":       InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    "rel":      InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    "overlap":  InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    "disambig": InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    "hint":     InfoTypeConfig(info_fn=lambda m: {"brief": _knowledge_brief(m)}),
    # 知识段
    "knowledge":    InfoTypeConfig(info_fn=lambda m: {"brief": _knowledge_brief(m)}),
    "pattern":     InfoTypeConfig(info_fn=lambda m: {
                    "path": _v(m, "json_path"),
                    "brief": _v(m, "brief") or _v(m, "pattern"),
                }, max_str_len=80),
    "convention":  InfoTypeConfig(info_fn=lambda m: {"brief": _knowledge_brief(m)}),
    "term":        InfoTypeConfig(info_fn=lambda m: {"brief": _knowledge_brief(m)}),
    "lesson":      InfoTypeConfig(info_fn=lambda m: {"brief": _knowledge_brief(m)}),
    "example":     InfoTypeConfig(info_fn=lambda m: {"brief": _knowledge_brief(m)}),
    # 其他
    "chunk":    InfoTypeConfig(info_fn=lambda m: {
                    "range": f"L{_v(m, 'start_line')}-L{_v(m, 'end_line')}",
                    "brief": _v(m, "brief"),
                }),
    "directory": InfoTypeConfig(info_fn=lambda m: {"path": _v(m, "path"), "stats": f"{_v(m, 'file_count')} files, {_v(m, 'subdir_count')} dirs"}),
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
        default_keys=["path", "file_size", "brief", "detail"],
    ),
    "db": MetaTypeConfig(
        default_keys=["index_count", "file_size", "brief", "detail"],
        adjacency_keys={"table", "view"},
    ),
    "csv": MetaTypeConfig(
        default_keys=["row_count", "column_count", "delimiter", "line_count", "char_count", "file_size", "brief", "detail"],
    ),
    "tsv": MetaTypeConfig(
        default_keys=["row_count", "column_count", "line_count", "char_count", "file_size", "brief", "detail"],
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
        default_keys=["row_count", "primary_key", "official_table_description", "fk", "rel", "disambig", "brief", "detail"],
        hidden_keys={"name", "labels", "project", "path", "db_name", "db_path", "table_name"},
        adjacency_keys={"col"},
    ),
    "view": MetaTypeConfig(
        default_keys=["row_count", "column_count", "official_view_description", "brief", "detail"],
        hidden_keys={"name", "labels", "project", "path", "db_name", "db_path", "table_name"},
        adjacency_keys={"col"},
    ),
    "col": MetaTypeConfig(
        default_keys=["official_column_description", "official_value_description",
                       "cardinality", "null_count", "null_percentage",
                       "sample",
                       "min_value", "max_value", "mean_value",
                       "min_length", "max_length", "avg_length",
                       "fk", "disambig", "brief", "detail"],
        folded_keys={"topk"},
        hidden_keys={"name", "labels", "project", "path", "db_name", "db_path", "table_name", "col_type"},
        adjacency_keys={"fk", "rel", "disambig", "table"},
    ),
    # 关系段
    "fk": MetaTypeConfig(
        default_keys=[
            "confidence", "match_rate", "total_count", "violation_count",
            "brief", "detail",
        ],
        hidden_keys={
            "name",
            "labels",
            "project",
            "path",
            "db_name",
            "db_path",
            "relation_type",
            "from_schema",
            "from_table",
            "from_column",
            "to_schema",
            "to_table",
            "to_column",
        },
        adjacency_keys={"col", "table"},
    ),
    "rel": MetaTypeConfig(
        default_keys=["brief", "detail"],
    ),
    "overlap": MetaTypeConfig(
        default_keys=["sources", "stats", "brief", "detail"],
        folded_keys={"stats"},
    ),
    "disambig": MetaTypeConfig(
        default_keys=["level", "brief", "detail"],
        adjacency_keys={"col", "table"},
    ),
    "hint": MetaTypeConfig(
        default_keys=["brief", "detail"],
        max_detail_lines=None,
    ),
    # 知识段
    "knowledge": MetaTypeConfig(
        default_keys=["brief", "detail"],
        max_detail_lines=None,
    ),
    "pattern": MetaTypeConfig(
        default_keys=["json_path", "type", "pattern", "brief", "detail"],
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
        default_keys=["start_line", "end_line", "brief", "detail"],
    ),
    "directory": MetaTypeConfig(
        default_keys=["path", "child_count", "file_count", "subdir_count"],
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
    merged_hidden: Set[str] = {
        "id",
        "name",
        "labels",
        "project",
        "path",
        "ref",
        "db_ref",
        "db_connect",
        "file_open",
        "from_col_ref",
        "to_col_ref",
        "db_name",
        "db_path",
        "table_name",
        "column_name",
        "col_type",
        "view_name",
        "source_column",
        "relation_type",
        "detail_embedding",
        "detail_embedding_model",
        "detail_embedding_hash",
        "detail_embedding_dimensions",
        "sample_method",
        "topk_method",
        "cardinality_method",
        "cardinality_lower_bound",
        "cardinality_upper_bound",
    }
    merged_adjacency: Set[str] = set(ALWAYS_VISIBLE_ADJACENCY_KEYS)
    seen_keys: Set[str] = set()
    merged_max_value_len: Optional[int] = 100
    merged_max_detail_lines: Optional[int] = 15

    for segment in entity_labels:
        if segment in META_TYPE_CONFIG:
                cfg = META_TYPE_CONFIG[segment]
                if cfg.max_value_len is None:
                    merged_max_value_len = None
                elif merged_max_value_len is not None:
                    merged_max_value_len = max(merged_max_value_len, cfg.max_value_len)

                if cfg.max_detail_lines is None:
                    merged_max_detail_lines = None
                elif merged_max_detail_lines is not None:
                    merged_max_detail_lines = max(merged_max_detail_lines, cfg.max_detail_lines)

                for k in cfg.default_keys:
                    if k not in seen_keys:
                        merged_keys.append(k)
                        seen_keys.add(k)
                merged_folded.update(cfg.folded_keys)
                merged_hidden.update(cfg.hidden_keys)
                merged_adjacency.update(cfg.adjacency_keys)

    if not merged_keys:
        return META_TYPE_CONFIG["default"]

    return MetaTypeConfig(
        default_keys=merged_keys,
        max_value_len=merged_max_value_len,
        max_detail_lines=merged_max_detail_lines,
        folded_keys=merged_folded,
        hidden_keys=merged_hidden,
        adjacency_keys=merged_adjacency,
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
    "hint": 4,
}


def resolve_rank(entity_labels: List[str]) -> int:
    """从标签中取最高 rank。"""
    max_rank = 0
    for segment in entity_labels:
        if segment in ENTITY_DEPENDENCY_RANK:
                max_rank = max(max_rank, ENTITY_DEPENDENCY_RANK[segment])
    return max_rank
