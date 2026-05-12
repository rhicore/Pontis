"""Tool-layer entity resolution.

所有 ref 都必须唯一命中。解析失败时只返回两类简单错误：
- 未找到匹配的实体
- 匹配到多个实体
"""

from tool.utils.entity_refs import dotted_ref_to_path


def _split_project_ref(ref: str) -> tuple[str | None, str]:
    if "::" not in ref:
        return None, ref
    project, local_ref = ref.split("::", 1)
    return project, local_ref


def _strip_display_label_suffix(segment: str) -> str:
    if ":" not in segment:
        return segment
    return segment.split(":", 1)[0]


def _normalize_relation_endpoint(text: str) -> str:
    parts = [p for p in text.split("/") if p]
    if not parts:
        return text
    if len(parts) >= 2 and "." not in parts[-1]:
        return f"{parts[-2]}.{parts[-1]}"
    last = parts[-1]
    dotted = [p for p in last.split(".") if p]
    if len(dotted) >= 3:
        # Be lenient with malformed relation refs like `table.alias.column` and
        # collapse them back to `table.column`.
        return f"{dotted[0]}.{dotted[-1]}"
    return last


def _normalize_relation_ref(local_ref: str) -> str | None:
    if "->" not in local_ref:
        return None
    left, right = local_ref.split("->", 1)
    left_norm = _normalize_relation_endpoint(left)
    right_norm = _normalize_relation_endpoint(right)
    if not left_norm or not right_norm:
        return None
    return f"{left_norm}->{right_norm}"


def resolve_entity(workspace, ref: str) -> tuple[dict | None, str | None]:
    """将 ref 解析为唯一节点元数据。"""
    project, local_ref = _split_project_ref(ref)
    local_ref = dotted_ref_to_path(local_ref)

    has_wildcards = any(c in local_ref for c in "*?[]")
    is_path_like = "/" in local_ref

    if not has_wildcards and not is_path_like:
        nodes = _lookup_exact_named_nodes(workspace, project, local_ref)
        if not nodes:
            return None, f"未找到匹配的实体: {ref}"
        if len(nodes) > 1:
            return None, f"匹配到多个实体: {ref}"
        return nodes[0], None

    if not has_wildcards and is_path_like:
        node, err = _resolve_exact_path(workspace, project, local_ref)
        if err or not node:
            return None, err or f"未找到匹配的实体: {ref}"
        return node, None

    from tool.glob.tool import glob_command

    output = glob_command(workspace, ref)
    if output.startswith("No objects"):
        return None, f"未找到匹配的实体: {ref}"

    lines = [l for l in output.strip().split("\n") if l.strip() and not l.startswith("(")]
    matched = []
    seen = set()
    for line in lines:
        parts = line.split("\t")
        if not parts:
            continue
        display_ref = parts[0]
        node, err = _resolve_exact_path(workspace, project, display_ref)
        if err or not node:
            continue
        key = (node.get("project", ""), tuple(node.get("labels", [])), node.get("name", ""))
        if key not in seen:
            seen.add(key)
            matched.append(node)

    if not matched:
        return None, f"未找到匹配的实体: {ref}"
    if len(matched) > 1:
        return None, f"匹配到多个实体: {ref}"
    return matched[0], None


def resolve_entity_selector(workspace, ref: str) -> tuple[dict | None, str | None]:
    """将 ref 解析为 Cypher 可用的稳定选择器。"""
    node, err = resolve_entity(workspace, ref)
    if err:
        return None, err
    return {
        "project": node.get("project") or None,
        "name": node.get("name", ref),
        "labels": list(node.get("labels", [])),
        "path": node.get("path"),
        "ref": node.get("ref"),
    }, None


def canonical_ref(node: dict, fallback: str = "") -> str:
    """Return the most stable writable ref for a resolved node."""
    return node.get("ref") or node.get("path") or node.get("name") or fallback


def selector_match_pattern(selector: dict, var: str = "n", name_param: str = "name") -> str:
    labels = "".join(f":{label}" for label in selector.get("labels", []))
    if selector.get("path"):
        return f"({var}{labels} {{path: $path}})"
    if selector.get("ref"):
        return f"({var}{labels} {{ref: $ref}})"
    return f"({var}{labels} {{name: ${name_param}}})"


def selector_params(selector: dict, base: dict | None = None, name_param: str = "name") -> dict:
    params = dict(base or {})
    if selector.get("path"):
        params["path"] = selector["path"]
    elif selector.get("ref"):
        params["ref"] = selector["ref"]
    else:
        params[name_param] = selector["name"]
    return params


def _lookup_exact_named_nodes(workspace, project: str | None, local_ref: str) -> list[dict]:
    rows = workspace.cypher(
        "MATCH (n {name: $name}) RETURN n",
        params={"name": local_ref},
        project=project,
    )
    nodes = _dedupe_nodes(row.get("n") for row in rows if row.get("n"))
    if len(nodes) > 1 and local_ref == "README":
        file_nodes = [node for node in nodes if "file" in set(node.get("labels", []))]
        if len(file_nodes) == 1:
            return file_nodes
    if len(nodes) > 1:
        disambig_nodes = [node for node in nodes if "disambig" in set(node.get("labels", []))]
        if len(disambig_nodes) == 1:
            return disambig_nodes
    if len(nodes) > 1 and "->" in local_ref:
        relation_nodes = [
            node for node in nodes
            if {"fk", "rel", "overlap"} & set(node.get("labels", []))
        ]
        if len(relation_nodes) == 1:
            return relation_nodes
    if nodes or ":" not in local_ref:
        return nodes

    parts = [p for p in local_ref.split(":") if p]
    if len(parts) < 2:
        return nodes
    base_name = parts[0]
    requested_labels = set(parts[1:])
    rows = workspace.cypher(
        "MATCH (n {name: $name}) RETURN n",
        params={"name": base_name},
        project=project,
    )
    matched = []
    for row in rows:
        node = row.get("n")
        if not node:
            continue
        labels = set(node.get("labels", []))
        if requested_labels.issubset(labels):
            matched.append(node)
    return _dedupe_nodes(matched)


def _resolve_exact_path(workspace, project: str | None, local_ref: str) -> tuple[dict | None, str | None]:
    normalized_relation_ref = _normalize_relation_ref(local_ref)
    if normalized_relation_ref:
        rows = workspace.cypher(
            "MATCH (n {name: $name}) RETURN n",
            params={"name": normalized_relation_ref},
            project=project,
        )
        nodes = _dedupe_nodes(row.get("n") for row in rows if row.get("n"))
        if len(nodes) == 1:
            return nodes[0], None
        if len(nodes) > 1:
            return None, f"匹配到多个实体: {local_ref}"

    parts = [p for p in local_ref.split("/") if p]
    normalized_parts = [_strip_display_label_suffix(p) for p in parts]
    normalized_ref = "/".join(normalized_parts)

    rows = workspace.cypher(
        "MATCH (n {name: $name}) RETURN n",
        params={"name": local_ref},
        project=project,
    )
    direct = [row.get("n") for row in rows if row.get("n")]
    if not direct and normalized_ref != local_ref:
        rows = workspace.cypher(
            "MATCH (n {name: $name}) RETURN n",
            params={"name": normalized_ref},
            project=project,
        )
        direct = [row.get("n") for row in rows if row.get("n")]
    if len(direct) == 1:
        return direct[0], None
    if len(direct) > 1:
        return None, f"匹配到多个实体: {local_ref}"

    if len(normalized_parts) == 2:
        file_name, table_name = normalized_parts
        rows = workspace.cypher(
            "MATCH (f:file {name: $file_name})--(t:table {name: $table_name}) RETURN t",
            params={"file_name": file_name, "table_name": table_name},
            project=project,
        )
        if not rows:
            rows = workspace.cypher(
                "MATCH (f:file {name: $file_name})--(t:view {name: $table_name}) RETURN t",
                params={"file_name": file_name, "table_name": table_name},
                project=project,
            )
        nodes = [row.get("t") for row in rows if row.get("t")]
        if not nodes:
            return None, f"未找到匹配的实体: {local_ref}"
        if len(nodes) > 1:
            return None, f"匹配到多个实体: {local_ref}"
        return nodes[0], None

    if len(normalized_parts) == 3:
        if normalized_parts[1] == "fks":
            file_name, _, fk_name = normalized_parts
            rows = workspace.cypher(
                "MATCH (f:file {name: $file_name})--(t)--(k:fk) "
                "WHERE k.name = $fk_name "
                "RETURN k",
                params={
                    "file_name": file_name,
                    "fk_name": fk_name,
                },
                project=project,
            )
            nodes = _dedupe_nodes(row.get("k") for row in rows if row.get("k"))
            if not nodes:
                return None, f"未找到匹配的实体: {local_ref}"
            if len(nodes) > 1:
                return None, f"匹配到多个实体: {local_ref}"
            return nodes[0], None

        file_name, table_name, col_name = normalized_parts
        rows = workspace.cypher(
            "MATCH (f:file {name: $file_name})--(t)--(c:col) "
            "WHERE t.name = $table_name AND c.name = $col_name "
            "RETURN c",
            params={
                "file_name": file_name,
                "table_name": table_name,
                "col_name": col_name,
            },
            project=project,
        )
        nodes = _dedupe_nodes(row.get("c") for row in rows if row.get("c"))
        if not nodes:
            return None, f"未找到匹配的实体: {local_ref}"
        if len(nodes) > 1:
            return None, f"匹配到多个实体: {local_ref}"
        return nodes[0], None

    return None, f"未找到匹配的实体: {local_ref}"


def _dedupe_nodes(nodes) -> list[dict]:
    result = []
    seen = set()
    for node in nodes:
        key = node.get("id") or (node.get("project", ""), node.get("ref"), node.get("path"), node.get("name"))
        if key in seen:
            continue
        seen.add(key)
        result.append(node)
    return result
