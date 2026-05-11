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


def resolve_entity(workspace, ref: str) -> tuple[dict | None, str | None]:
    """将 ref 解析为唯一节点元数据。"""
    project, local_ref = _split_project_ref(ref)
    local_ref = dotted_ref_to_path(local_ref)

    has_wildcards = any(c in local_ref for c in "*?[]")
    is_path_like = "/" in local_ref

    if not has_wildcards and not is_path_like:
        rows = workspace.cypher(
            "MATCH (n {name: $name}) RETURN n",
            params={"name": local_ref},
            project=project,
        )
        nodes = [row.get("n") for row in rows if row.get("n")]
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
    }, None


def selector_match_pattern(selector: dict, var: str = "n", name_param: str = "name") -> str:
    labels = "".join(f":{label}" for label in selector.get("labels", []))
    return f"({var}{labels} {{name: ${name_param}}})"


def _resolve_exact_path(workspace, project: str | None, local_ref: str) -> tuple[dict | None, str | None]:
    parts = [p for p in local_ref.split("/") if p]

    rows = workspace.cypher(
        "MATCH (n {name: $name}) RETURN n",
        params={"name": local_ref},
        project=project,
    )
    direct = [row.get("n") for row in rows if row.get("n")]
    if len(direct) == 1:
        return direct[0], None
    if len(direct) > 1:
        return None, f"匹配到多个实体: {local_ref}"

    if len(parts) == 2:
        file_name, table_name = parts
        rows = workspace.cypher(
            "MATCH (f:file {name: $file_name})--(t) "
            "WHERE ('table' IN t.labels OR 'view' IN t.labels) "
            "AND t.name = $table_name "
            "RETURN t",
            params={"file_name": file_name, "table_name": table_name},
            project=project,
        )
        nodes = [row.get("t") for row in rows if row.get("t")]
        if not nodes:
            return None, f"未找到匹配的实体: {local_ref}"
        if len(nodes) > 1:
            return None, f"匹配到多个实体: {local_ref}"
        return nodes[0], None

    if len(parts) == 3:
        file_name, table_name, col_name = parts
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
        nodes = [row.get("c") for row in rows if row.get("c")]
        if not nodes:
            return None, f"未找到匹配的实体: {local_ref}"
        if len(nodes) > 1:
            return None, f"匹配到多个实体: {local_ref}"
        return nodes[0], None

    return None, f"未找到匹配的实体: {local_ref}"
