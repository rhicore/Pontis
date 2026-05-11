"""Meta tool — 双模式元数据查看。

模式1：meta(ref) → 自身 meta + related 分组
模式2：meta(ref, neighbor_label) → 只看匹配 neighbor_label 的邻居
"""
from typing import List, Optional, Union

from tool.config import resolve_meta_config
from tool.utils.formatters import format_labels, format_meta_output, get_info
from tool.utils.resolve import resolve_entity_selector, selector_match_pattern

_ADJACENCY_KEYS = {"fk", "rel", "disambig", "col", "overlap", "table", "view"}


def _get_project_name(workspace) -> str:
    ap = workspace.active_projects
    if ap:
        return ap[0]
    return "local"


def _label_matches(entity_labels: List[str], query: str) -> bool:
    from storage.labels import label_matches
    return label_matches(entity_labels, query)


def _display_ref(workspace, project: str | None, selector: dict, node_meta: dict) -> str:
    """尽量生成与 glob 一致的展示引用。"""
    labels = set(node_meta.get("labels", []))
    name = node_meta.get("name", selector["name"])
    match = selector_match_pattern(selector, "n")

    if "col" in labels:
        rows = workspace.cypher(
            f"MATCH (f:file)--(t)--{match} RETURN f, t",
            params={"name": selector["name"]},
            project=project,
        )
        for row in rows:
            f = row.get("f") or {}
            t = row.get("t") or {}
            t_labels = set(t.get("labels", []))
            if "file" in set(f.get("labels", [])) and ({"table", "view"} & t_labels):
                return f"{f.get('name', '')}/{t.get('name', '')}/{name}"
        return name

    if "table" in labels or "view" in labels:
        rows = workspace.cypher(
            f"MATCH (f:file)--{match} RETURN f",
            params={"name": selector["name"]},
            project=project,
        )
        for row in rows:
            f = row.get("f") or {}
            if "file" in set(f.get("labels", [])):
                return f"{f.get('name', '')}/{name}"

    return name


def _neighbor_selector(project: str | None, meta: dict) -> dict:
    return {
        "project": project,
        "name": meta.get("name", ""),
        "labels": list(meta.get("labels", [])),
    }


def _format_neighbor_list(workspace, project_name: str, project: str | None, neighbors: List[dict]) -> str:
    if not neighbors:
        return "No matching neighbors found"
    lines = []
    for meta in neighbors:
        selector = _neighbor_selector(project, meta)
        display_ref = _display_ref(workspace, project, selector, meta)
        labels = meta.get("labels", [])
        info = get_info(labels, meta)
        label_str = format_labels(labels)
        lines.append(f"{project_name}::\t{display_ref}\t{label_str}\t{info}")
    return "\n".join(lines)


def meta_command(
    workspace,
    ref: str,
    all: bool = False,
    property: Optional[Union[str, List[str]]] = None,
    neighbor_label: Optional[str] = None,
    current_cwd: str = ""
) -> str:
    """查看节点元数据。"""
    if not workspace.pontis_exists:
        return f"Error: .pontis directory not found in {workspace.project_path}"

    selector, err = resolve_entity_selector(workspace, ref)
    if err:
        return f"Error: {err}"

    project = selector["project"]
    project_name = project or _get_project_name(workspace)
    match = selector_match_pattern(selector, "n")

    rows = workspace.cypher(
        f"MATCH {match} RETURN n",
        params={"name": selector["name"]},
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
    display_ref = _display_ref(workspace, project, selector, meta)

    neighbor_rows = workspace.cypher(
        f"MATCH {match}--(m) RETURN m",
        params={"name": selector["name"]},
        project=project,
    )
    neighbors = [row.get("m") for row in neighbor_rows if row.get("m")]

    if neighbor_label:
        filtered = [m for m in neighbors if _label_matches(m.get("labels", []), neighbor_label)]
        return _format_neighbor_list(workspace, project_name, project, filtered)

    adjacency = {}
    plain_meta = dict(meta)
    for key in _ADJACENCY_KEYS:
        plain_meta.pop(key, None)

    for adj_meta in neighbors:
        adj_labels = adj_meta.get("labels", [])
        if not adj_labels:
            continue
        group_key = adj_labels[0]
        if group_key not in _ADJACENCY_KEYS:
            continue
        adj_selector = _neighbor_selector(project, adj_meta)
        disp = _display_ref(workspace, project, adj_selector, adj_meta)
        info = get_info(adj_labels, adj_meta)
        label_str = format_labels(adj_labels)
        adjacency.setdefault(group_key, []).append(f"  {disp}\t{label_str}\t{info}")

    props = None
    if property:
        if isinstance(property, str):
            props = [property]
        else:
            props = list(property)

    if props:
        from tool.utils.formatters import _format_meta_value
        lines = []
        missing = []
        for p in props:
            value = plain_meta.get(p)
            if value is None and p in adjacency:
                value = "\n".join(adjacency[p])
            if value is None:
                missing.append(p)
            else:
                lines.append(f"{p}: {_format_meta_value(value, None)}")
        if missing:
            available = sorted(set(list(plain_meta.keys()) + list(adjacency.keys())))
            lines.append(f"未找到: {', '.join(missing)}. 可用字段: {', '.join(available)}")
        return "\n".join(lines)

    header_line = f"{display_ref}\t{format_labels(labels)}\nproject: {project_name}"
    config = resolve_meta_config(labels)
    result = format_meta_output(plain_meta, config, show_all=all, specific_key=None)

    if not result and not all:
        lines = [header_line, ""]
        for key, value in sorted(plain_meta.items()):
            lines.append(f"{key}: {value}")
        result = "\n".join(lines)
    else:
        result = header_line + "\n\n" + result

    if adjacency:
        visible_adj = {k: v for k, v in adjacency.items()
                       if not config.adjacency_keys or k in config.adjacency_keys}
        if visible_adj:
            summary_parts = [f"{k}: {len(v)}" for k, v in sorted(visible_adj.items())]
            result += f"\n\nRelated ({', '.join(summary_parts)})\n"
            for key in sorted(visible_adj.keys()):
                result += f"\n{key}:\n" + "\n".join(visible_adj[key]) + "\n"

    return result


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
