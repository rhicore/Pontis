"""Build public refs by navigating from an explicit storage source node."""

from __future__ import annotations

from tool.utils.public_ref import public_label
from tool.utils.resolve import selector_match_pattern, selector_params


def node_selector(project: str | None, node_meta: dict) -> dict:
    return {
        "project": project,
        "id": None,
        "name": node_meta.get("name", ""),
        "labels": list(node_meta.get("labels", [])),
        "path": node_meta.get("path"),
        "ref": node_meta.get("_ref") or node_meta.get("ref"),
        "ref_key": "_ref" if node_meta.get("_ref") else ("ref" if node_meta.get("ref") else None),
    }


def display_ref_for_node(
    workspace,
    project: str | None,
    node_meta: dict,
    row: dict | None = None,
    main_var: str | None = None,
) -> str:
    """Return a deterministic, source-anchored graph navigation ref.

    `_ref`, `path`, and entity ids are used only to identify the target node in
    the storage query.  The returned value is rebuilt from actual graph nodes
    on a shortest path starting at the project's internal source anchor.
    """
    selector = node_selector(project, node_meta)
    match = selector_match_pattern(selector, "n")
    if "col" in set(node_meta.get("labels", []) or []):
        # A column also connects to FK/overlap/disambiguation entities.  Those
        # paths can be as short as its containment path, but they are not its
        # structural coordinate. Resolve the table/view parent through the
        # graph first, then navigate from the source to that parent.
        rows = workspace.cypher(
            "MATCH (s {_source_anchor: true}) "
            f"MATCH {match}--(container) "
            "WHERE any(label IN coalesce(container.labels, []) WHERE label IN ['table', 'view']) "
            "MATCH p = shortestPath((s)-[*0..12]-(container)) "
            "RETURN nodes(p) + [n] AS path_nodes, length(p) + 1 AS hops "
            "ORDER BY hops, coalesce(container.name, '') "
            "LIMIT 1",
            params=selector_params(selector),
            project=project,
        )
    else:
        rows = workspace.cypher(
            "MATCH (s {_source_anchor: true}) "
            f"MATCH p = shortestPath((s)-[*0..12]-{match}) "
            "RETURN nodes(p) AS path_nodes, length(p) AS hops "
            "ORDER BY hops, reduce(key = '', item IN nodes(p) | "
            "key + '/' + coalesce(item.name, '')) "
            "LIMIT 1",
            params=selector_params(selector),
            project=project,
        )
    if rows and rows[0].get("path_nodes"):
        parts = [_format_segment(node) for node in rows[0]["path_nodes"]]
        local_ref = "/".join(part for part in parts if part)
    else:
        # A disconnected derived node is a graph-integrity problem. Keep the
        # fallback readable, but do not manufacture a fake source path.
        local_ref = _format_segment(node_meta)

    active_projects = list(getattr(workspace, "active_projects", []) or [])
    if project and len(active_projects) > 1:
        return f"{project}::{local_ref}"
    return local_ref


def _format_segment(node: dict) -> str:
    name = str(node.get("name") or "?")
    labels = list(node.get("labels", []))
    tag = public_label(labels)
    suffixes = [tag]
    return name + "".join(f":{label}" for label in suffixes)


__all__ = ["display_ref_for_node", "node_selector"]
