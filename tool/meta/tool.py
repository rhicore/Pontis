"""Meta tool — 双模式元数据查看。

模式1：meta(ref) → 自身 meta + related 分组
模式2：meta(ref, neighbor_label) → 只看匹配 neighbor_label 的邻居
"""
from typing import List, Optional, Union

from tool.config import resolve_meta_config
from tool.utils.display_ref import display_ref_for_node, node_selector
from tool.utils.formatters import format_entity_name, format_meta_output, get_display_property_value, get_info
from tool.utils.resolve import resolve_entity_selector, selector_match_pattern, selector_params

_ADJACENCY_KEYS = {"fk", "rel", "disambig", "col", "overlap", "table", "view"}


def _get_project_name(workspace) -> str:
    ap = workspace.active_projects
    if ap:
        return ap[0]
    return "local"


def _label_matches(entity_labels: List[str], query: str) -> bool:
    from storage.labels import label_matches
    return label_matches(entity_labels, query)


def _adjacency_group_key(labels: List[str]) -> str | None:
    for label in labels:
        if label in _ADJACENCY_KEYS:
            return label
    return None


def _format_neighbor_list(workspace, project_name: str, project: str | None, neighbors: List[dict]) -> str:
    if not neighbors:
        return "No matching neighbors found"
    lines = []
    for meta in neighbors:
        labels = meta.get("labels", [])
        short_name = meta.get("name") or display_ref_for_node(workspace, project, meta)
        info = get_info(labels, meta)
        entity_name = format_entity_name(short_name, labels)
        lines.append(f"{project_name}::\t{entity_name}\t{info}")
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

    props = None
    if property:
        if isinstance(property, str):
            props = [property]
        else:
            props = list(property)

    if props and not (set(props) & _ADJACENCY_KEYS):
        from tool.utils.formatters import _format_meta_value
        lines = []
        missing = []
        raw_meta = dict(meta)
        for p in props:
            value = get_display_property_value(raw_meta, labels, p)
            if value is None:
                missing.append(p)
            else:
                lines.append(f"{p}: {_format_meta_value(value, None)}")
        if missing:
            available = sorted(raw_meta.keys())
            lines.append(f"未找到: {', '.join(missing)}. 可用字段: {', '.join(available)}")
        return "\n".join(lines)

    neighbor_rows = workspace.cypher(
        f"MATCH {match}--(m) RETURN m",
        params=selector_params(selector),
        project=project,
    )
    neighbors = [row.get("m") for row in neighbor_rows if row.get("m")]

    if neighbor_label:
        filtered = [m for m in neighbors if _label_matches(m.get("labels", []), neighbor_label)]
        return _format_neighbor_list(workspace, project_name, project, filtered)

    adjacency = {}
    raw_meta = dict(meta)
    plain_meta = dict(meta)
    hidden_keys = set(getattr(resolve_meta_config(labels), "hidden_keys", set()))
    for key in hidden_keys:
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
        short_name = adj_meta.get("name") or display_ref_for_node(workspace, project, adj_meta)
        info = get_info(adj_labels, adj_meta)
        entity_name = format_entity_name(short_name, adj_labels)
        adjacency.setdefault(group_key, []).append(f"  {entity_name}\t{info}")

    if props:
        from tool.utils.formatters import _format_meta_value
        lines = []
        missing = []
        for p in props:
            value = get_display_property_value(raw_meta, labels, p)
            if value is None and p in adjacency:
                value = "\n".join(adjacency[p])
            if value is None:
                missing.append(p)
            else:
                lines.append(f"{p}: {_format_meta_value(value, None)}")
        if missing:
            available = sorted(set(list(raw_meta.keys()) + list(adjacency.keys())))
            lines.append(f"未找到: {', '.join(missing)}. 可用字段: {', '.join(available)}")
        return "\n".join(lines)

    header_line = f"{format_entity_name(display_ref, labels)}\nproject: {project_name}"
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
