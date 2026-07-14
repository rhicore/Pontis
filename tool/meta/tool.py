"""Meta tool — 双模式元数据查看。

模式1：meta(ref) → 自身 meta + related 分组
模式2：meta(ref, neighbor_label) → 只看匹配 neighbor_label 的邻居
"""
import json
from typing import Any, List, Optional, Union

from tool.config import resolve_meta_config
from tool.utils.display_ref import display_ref_for_node, node_selector
from tool.utils.formatters import format_entity_name, format_meta_output, get_display_property_value, get_info
from tool.utils.resolve import resolve_entity_selector, selector_match_pattern, selector_params

_ADJACENCY_KEYS = {
    "fk", "rel", "disambig", "hint", "hints", "col", "logical_col",
    "column_domain", "table", "view",
}
_DEFAULT_NEIGHBOR_LIMIT = 20
_MAX_NEIGHBOR_LIMIT = 100


def _get_project_name(workspace) -> str:
    ap = workspace.active_projects
    if ap:
        return ap[0]
    return "local"


def _label_matches(entity_labels: List[str], query: str) -> bool:
    from storage.query_inspector import label_matches
    return label_matches(entity_labels, query)


def _adjacency_group_key(labels: List[str]) -> str | None:
    for label in labels:
        if label in _ADJACENCY_KEYS:
            return label
    return None


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _trim_value(value: Any, max_len: int = 120) -> Any:
    if isinstance(value, str) and len(value) > max_len:
        return value[:max_len] + "..."
    return value


def _normalize_hint_items(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                return _normalize_hint_items(parsed)
        return [line.strip() for line in text.splitlines() if line.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _hint_node_line(workspace, project: str | None, meta: dict) -> str:
    name = display_ref_for_node(workspace, project, meta)
    brief = meta.get("brief") or get_info(meta.get("labels", []), meta)
    detail = meta.get("detail")
    if brief and str(brief).strip() not in ("", "-"):
        return f"  - {name}: {str(brief).strip()}"
    if detail and str(detail).strip() not in ("", "-"):
        first_line = str(detail).strip().splitlines()[0]
        return f"  - {name}: {first_line}"
    return f"  - {name}"


def _compute_column_properties(meta: dict, labels: List[str], props: List[str]) -> dict:
    """Compute lightweight column stats through the resolved database handle.

    These values are only computed when explicitly requested. They are runtime
    metadata, not persisted graph facts.
    """
    wanted = set(props) & {"sample", "topk", "cardinality"}
    if not wanted or "col" not in set(labels or []):
        return {}

    db_connect = meta.get("_db_connect") or meta.get("db_connect")
    if not callable(db_connect):
        return {}

    table = meta.get("table_name")
    column = meta.get("column_name")
    if not table or not column:
        return {}

    table_sql = _quote_ident(table)
    column_sql = _quote_ident(column)
    result: dict[str, Any] = {}
    conn = db_connect(readonly=True)
    try:
        cur = conn.cursor()
        if "cardinality" in wanted:
            cur.execute(f"SELECT COUNT(DISTINCT {column_sql}) FROM {table_sql}")
            row = cur.fetchone()
            result["cardinality"] = row[0] if row else 0
        if "sample" in wanted:
            cur.execute(
                f"SELECT DISTINCT {column_sql} FROM {table_sql} "
                f"WHERE {column_sql} IS NOT NULL LIMIT 20"
            )
            result["sample"] = [_trim_value(row[0]) for row in cur.fetchall()]
        if "topk" in wanted:
            cur.execute(
                f"SELECT {column_sql}, COUNT(*) AS c FROM {table_sql} "
                f"WHERE {column_sql} IS NOT NULL "
                f"GROUP BY {column_sql} ORDER BY c DESC LIMIT 10"
            )
            result["topk"] = [
                {"value": _trim_value(value), "count": count}
                for value, count in cur.fetchall()
            ]
    except Exception as exc:
        result["_error"] = str(exc)
    finally:
        conn.close()
    return result


def _neighbor_display_ref(workspace, project: str | None, meta: dict) -> str:
    return display_ref_for_node(workspace, project, meta)


def _format_neighbor_list(
    workspace,
    project_name: str,
    project: str | None,
    neighbors: List[dict],
    offset: int = 0,
    limit: int = _DEFAULT_NEIGHBOR_LIMIT,
) -> str:
    if not neighbors:
        return "No matching neighbors found"
    offset = max(0, int(offset or 0))
    limit = min(_MAX_NEIGHBOR_LIMIT, max(1, int(limit or _DEFAULT_NEIGHBOR_LIMIT)))
    neighbors = sorted(
        neighbors,
        key=lambda item: (
            _adjacency_group_key(item.get("labels", [])) or "",
            str(item.get("name") or "").casefold(),
        ),
    )
    page = neighbors[offset:offset + limit]
    if not page:
        return f"No neighbors at offset {offset}. Total neighbors: {len(neighbors)}"
    lines = []
    for meta in page:
        labels = meta.get("labels", [])
        name = _neighbor_display_ref(workspace, project, meta)
        info = get_info(labels, meta)
        lines.append(f"{name}\t{info}")
    end = offset + len(page)
    if end < len(neighbors):
        lines.append(
            f"(共 {len(neighbors)} 条邻接，当前显示第 {offset + 1}-{end} 条。"
            f"使用 offset={end}, limit={limit} 查看后续结果)"
        )
    else:
        lines.append(
            f"(共 {len(neighbors)} 条邻接，当前显示第 {offset + 1}-{end} 条，已到最后一页)"
        )
    return "\n".join(lines)


def _format_related_disambig_notice(
    workspace,
    project: str | None,
    match: str,
    params: dict,
    labels: List[str],
) -> str:
    if "disambig" in set(labels or []):
        return ""

    rows = workspace.cypher(
        f"MATCH {match}--(d) "
        "WHERE 'disambig' IN coalesce(d.labels, []) "
        "RETURN d ORDER BY d.name",
        params=params,
        project=project,
    )
    disambigs = [row.get("d") for row in rows if row.get("d")]
    if not disambigs:
        return ""

    lines = [
        "相关字段边界",
        "当前实体连接了消歧义实体；相关字段边界见以下实体：",
    ]
    for meta in disambigs:
        display_ref = display_ref_for_node(workspace, project, meta)
        brief = meta.get("brief")
        line = f'  - meta({{"ref": "{display_ref}"}})'
        if brief and str(brief).strip():
            line += f"  # {str(brief).strip()}"
        lines.append(line)
    return "\n".join(lines)


def _append_notice(result: str, notice: str) -> str:
    if not notice:
        return result
    if not result:
        return notice
    return result.rstrip() + "\n\n" + notice


def _normalize_property_request(property: Optional[Union[str, List[str]]]) -> list[str] | None:
    if not property:
        return None
    if not isinstance(property, str):
        return [str(item).strip().strip("\"'") for item in property if str(item).strip().strip("\"'")]

    text = property.strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, list):
        return [str(item).strip().strip("\"'") for item in parsed if str(item).strip().strip("\"'")]
    if isinstance(parsed, str):
        text = parsed.strip()

    if text.startswith("[") and text.endswith("]"):
        inner = text.strip("[]")
        props = [
            item.strip().strip("\"'")
            for item in inner.split(",")
            if item.strip().strip("\"'")
        ]
        return props or None

    prop = text.strip("\"'")
    return [prop] if prop else None


def meta_command(
    workspace,
    ref: str,
    all: bool = False,
    property: Optional[Union[str, List[str]]] = None,
    neighbor_label: Optional[str] = None,
    offset: int = 0,
    limit: int = _DEFAULT_NEIGHBOR_LIMIT,
    current_cwd: str = ""
) -> str:
    """查看节点元数据。"""
    input_ref = (ref or "").strip()
    if int(offset or 0) < 0:
        return "Error: meta offset 必须大于或等于 0。"
    if int(limit) < 1:
        return "Error: meta limit 必须大于或等于 1。"
    # Brackets are ordinary entity-name punctuation; only public glob
    # operators are rejected here.
    if any(char in input_ref for char in "*?"):
        return (
            "Error: meta 只接受单个确定实体，不能使用通配 ref。"
            f"请先调用 find(ref={input_ref!r})，再把结果第一列交给 meta。"
        )
    if input_ref.rstrip("/").endswith(("/all", "/Related", "/related")):
        parent = input_ref.rsplit("/", 1)[0]
        return (
            "Error: meta 不使用 /all 或 /Related 路径。"
            f"请调用 meta(ref={parent!r}, neighbor_label=\"col\", offset=0, limit=20)，"
            "并将 col 替换为需要的邻接标签。"
        )
    selector, err = resolve_entity_selector(workspace, ref)
    if err:
        return f"Error: {err}"

    project = selector["project"]
    project_name = project or _get_project_name(workspace)
    match = selector_match_pattern(selector, "n")

    rows = workspace.cypher(
        f"MATCH {match} RETURN n",
        params=selector_params(selector),
        project=project,
    )
    if not rows:
        return f"No metadata found for '{ref}'"

    meta = rows[0].get("n")
    if meta is None:
        return f"No metadata found for '{ref}'"
    if not meta:
        return f"Empty metadata for '{ref}'"

    labels = meta.get("labels", [])
    display_ref = display_ref_for_node(workspace, project, meta)
    related_disambig_notice = _format_related_disambig_notice(
        workspace,
        project,
        match,
        selector_params(selector),
        labels,
    )

    props = _normalize_property_request(property)

    if props and not (set(props) & _ADJACENCY_KEYS):
        from tool.utils.formatters import _format_meta_value
        lines = []
        missing = []
        raw_meta = dict(meta)
        hidden = set(resolve_meta_config(labels).hidden_keys)
        computed = _compute_column_properties(raw_meta, labels, props)
        for p in props:
            value = None if p.startswith("_") or p in hidden else get_display_property_value(raw_meta, labels, p)
            if value is None and p in computed:
                value = computed[p]
            if value is None:
                missing.append(p)
            else:
                lines.append(f"{p}: {_format_meta_value(value, None)}")
        if missing:
            available = sorted(
                key for key in set(list(raw_meta.keys()) + list(computed.keys()))
                if not key.startswith("_") and key not in hidden
            )
            lines.append(f"未找到: {', '.join(missing)}. 可用字段: {', '.join(available)}")
        return _append_notice("\n".join(lines), related_disambig_notice)

    neighbor_rows = workspace.cypher(
        f"MATCH {match}--(m) RETURN m",
        params=selector_params(selector),
        project=project,
    )
    neighbors = [row.get("m") for row in neighbor_rows if row.get("m")]

    if neighbor_label:
        filtered = [m for m in neighbors if _label_matches(m.get("labels", []), neighbor_label)]
        result = _format_neighbor_list(
            workspace, project_name, project, filtered, offset=offset, limit=limit
        )
        if not _label_matches(["disambig"], neighbor_label):
            result = _append_notice(result, related_disambig_notice)
        return result

    raw_meta = dict(meta)
    adjacency = {}
    own_hint_lines = [f"  - {hint}" for hint in _normalize_hint_items(raw_meta.get("hints"))]
    neighbor_hint_lines: List[str] = []
    plain_meta = dict(meta)
    hidden_keys = set(getattr(resolve_meta_config(labels), "hidden_keys", set()))
    for key in hidden_keys:
        plain_meta.pop(key, None)
    for key in list(plain_meta.keys()):
        if key.startswith("_"):
            plain_meta.pop(key, None)
    for key in _ADJACENCY_KEYS:
        plain_meta.pop(key, None)

    for adj_meta in neighbors:
        adj_labels = adj_meta.get("labels", [])
        if not adj_labels:
            continue
        group_key = _adjacency_group_key(adj_labels)
        if not group_key:
            continue
        name = _neighbor_display_ref(workspace, project, adj_meta)
        info = get_info(adj_labels, adj_meta)
        adjacency.setdefault(group_key, []).append(f"  {name}\t{info}")
        if group_key == "hint":
            neighbor_hint_lines.append(_hint_node_line(workspace, project, adj_meta))

    hint_lines: List[str] = []
    seen_hints = set()
    for line in own_hint_lines + neighbor_hint_lines:
        key = line.strip().casefold()
        if key in seen_hints:
            continue
        seen_hints.add(key)
        hint_lines.append(line)

    if props:
        from tool.utils.formatters import _format_meta_value
        lines = []
        missing = []
        computed = _compute_column_properties(raw_meta, labels, props)
        for p in props:
            if p == "hints":
                value = "\n".join(hint_lines) if hint_lines else None
            else:
                value = get_display_property_value(raw_meta, labels, p)
            if value is None and p in computed:
                value = computed[p]
            if value is None and p in adjacency:
                value = "\n".join(adjacency[p])
            if value is None:
                missing.append(p)
            else:
                lines.append(f"{p}: {_format_meta_value(value, None)}")
        if missing:
            available = sorted(
                key for key in set(list(raw_meta.keys()) + list(adjacency.keys()) + list(computed.keys()))
                if not key.startswith("_") and key not in hidden_keys
            )
            lines.append(f"未找到: {', '.join(missing)}. 可用字段: {', '.join(available)}")
        return _append_notice("\n".join(lines), related_disambig_notice)

    header_ref = input_ref or format_entity_name(display_ref, labels)
    header_line = f"{header_ref}\nproject: {project_name}"
    config = resolve_meta_config(labels)
    result = format_meta_output(plain_meta, config, show_all=all, specific_key=None)

    if not result and not all:
        lines = [header_line, ""]
        for key, value in sorted(plain_meta.items()):
            lines.append(f"{key}: {value}")
        result = "\n".join(lines)
    else:
        result = header_line + "\n\n" + result

    if hint_lines:
        result += "\n\nHints\n" + "\n".join(hint_lines)

    visible_adj = {}
    if adjacency:
        public_adj = {k: v for k, v in adjacency.items() if k != "hint"}
        visible_adj = {
            k: v for k, v in public_adj.items()
            if not config.adjacency_keys or k in config.adjacency_keys
        }
        if public_adj:
            summary_parts = [f"{k}: {len(v)}" for k, v in sorted(public_adj.items())]
            result += f"\n\nRelated ({', '.join(summary_parts)})\n"
            for key in sorted(visible_adj.keys()):
                values = sorted(visible_adj[key], key=str.casefold)
                page = values[:_DEFAULT_NEIGHBOR_LIMIT]
                result += f"\n{key}:\n" + "\n".join(page) + "\n"
                if len(values) > len(page):
                    result += (
                        f"  ... 共 {len(values)} 条；使用 "
                        f"meta(ref={display_ref!r}, neighbor_label=\"{key}\", "
                        f"offset={len(page)}, limit={_DEFAULT_NEIGHBOR_LIMIT}) 继续\n"
                    )
            hidden_adj = sorted(set(public_adj) - set(visible_adj))
            if hidden_adj:
                result += (
                    "\n其他邻接只显示计数；neighbor_label 可选: "
                    + ", ".join(f'"{key}"' for key in hidden_adj)
                    + "。\n"
                )

    if "disambig" in visible_adj:
        related_disambig_notice = ""
    return _append_notice(result, related_disambig_notice)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m tool.meta.tool <project_name> <path> [--all] [+property]")
        raise SystemExit(1)

    from storage.workspace import Workspace
    ws = Workspace(active_projects=[sys.argv[1]])
    _path = sys.argv[2]
    _all = '--all' in sys.argv or '-a' in sys.argv
    _prop = None
    for arg in sys.argv[3:]:
        if arg.startswith('+'):
            _prop = arg[1:]

    print(meta_command(ws, _path, _all, _prop))
