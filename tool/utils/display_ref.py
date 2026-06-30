"""Shared helpers for building stable, copyable display refs."""

from tool.utils.resolve import selector_match_pattern, selector_params


def node_selector(project: str | None, node_meta: dict) -> dict:
    return {
        "project": project,
        "id": None,
        "name": node_meta.get("name", ""),
        "labels": list(node_meta.get("labels", [])),
        "path": node_meta.get("path"),
        "ref": node_meta.get("_ref") or node_meta.get("ref"),
    }


def display_ref_for_node(
    workspace,
    project: str | None,
    node_meta: dict,
    row: dict | None = None,
    main_var: str | None = None,
) -> str:
    """Build a display ref consistent with find/meta outputs."""
    labels = set(node_meta.get("labels", []))
    name = node_meta.get("name", "?")
    path = node_meta.get("path")
    ref = node_meta.get("_ref") or node_meta.get("ref")

    if path and ({"file", "dir"} & labels):
        return path

    inline_ref = _display_ref_from_ref(labels, ref)
    if inline_ref:
        return inline_ref

    if "col" in labels:
        table_name = None
        file_name = None
        if row:
            for var, info in row.items():
                if var == main_var or not isinstance(info, dict):
                    continue
                var_labels = set(info.get("labels", []))
                if table_name is None and ({"table", "view"} & var_labels):
                    table_name = info.get("name")
                if file_name is None and "file" in var_labels:
                    file_name = info.get("name")
        if file_name and table_name:
            return f"{file_name}/{table_name}/{name}"
        if table_name:
            return f"{table_name}/{name}"

        selector = node_selector(project, node_meta)
        match = selector_match_pattern(selector, "n")
        rows = workspace.cypher(
            f"MATCH (f:file)--(t)--{match} RETURN f, t",
            params=selector_params(selector),
            project=project,
        )
        for extra in rows:
            f = extra.get("f") or {}
            t = extra.get("t") or {}
            if "file" in set(f.get("labels", [])) and ({"table", "view"} & set(t.get("labels", []))):
                return f"{f.get('name', '')}/{t.get('name', '')}/{name}"

    if "table" in labels or "view" in labels:
        file_name = None
        if row:
            for var, info in row.items():
                if var == main_var or not isinstance(info, dict):
                    continue
                if "file" in set(info.get("labels", [])):
                    file_name = info.get("name")
                    break
        if file_name:
            return f"{file_name}/{name}"

        selector = node_selector(project, node_meta)
        match = selector_match_pattern(selector, "n")
        rows = workspace.cypher(
            f"MATCH (f:file)--{match} RETURN f",
            params=selector_params(selector),
            project=project,
        )
        for extra in rows:
            f = extra.get("f") or {}
            if "file" in set(f.get("labels", [])):
                return f"{f.get('name', '')}/{name}"

    if "pattern" in labels:
        file_path = None
        if row:
            for var, info in row.items():
                if var == main_var or not isinstance(info, dict):
                    continue
                if "file" in set(info.get("labels", [])):
                    file_path = info.get("path") or info.get("name")
                    break
        if file_path:
            return f"{file_path}/{name}"

        selector = node_selector(project, node_meta)
        match = selector_match_pattern(selector, "n")
        rows = workspace.cypher(
            f"MATCH (f:file)--{match} RETURN f",
            params=selector_params(selector),
            project=project,
        )
        for extra in rows:
            f = extra.get("f") or {}
            if "file" in set(f.get("labels", [])):
                file_path = f.get("path") or f.get("name")
                if file_path:
                    return f"{file_path}/{name}"

    return name


def _display_ref_from_ref(labels: set[str], ref: str | None) -> str | None:
    if not ref or "--" not in ref:
        return None
    parts = [part for part in ref.split("--") if part]
    if "col" in labels:
        if len(parts) >= 3:
            return f"{parts[0]}/{parts[1]}/{parts[2]}"
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    if ("table" in labels or "view" in labels) and len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return None
