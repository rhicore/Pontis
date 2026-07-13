"""Tool-layer entity resolution.

所有 ref 都必须唯一命中。解析失败时只返回两类简单错误：
- 未找到匹配的实体
- 匹配到多个实体
"""

from tool.utils.entity_refs import dotted_ref_to_path
from storage.query_inspector import cypher_label_clause


def _split_project_ref(ref: str) -> tuple[str | None, str]:
    if "::" not in ref:
        return None, ref
    project, local_ref = ref.split("::", 1)
    return project, local_ref


def _strip_display_label_suffix(segment: str) -> str:
    if ":" not in segment:
        return segment
    return segment.split(":", 1)[0]


def _strip_outer_quotes(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _normalize_display_segment(segment: str) -> str:
    return _strip_outer_quotes(_strip_display_label_suffix(segment).strip())


def _normalize_relation_endpoint(text: str) -> str:
    parts = [p for p in text.split("/") if p]
    if not parts:
        return text
    if len(parts) >= 2 and "." not in parts[-1]:
        return f"{parts[-2]}.{parts[-1]}"
    last = parts[-1]
    dotted = [p for p in last.split(".") if p]
    if len(dotted) >= 3:
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


def _query_projects(workspace, project: str | None) -> list[str | None]:
    if project:
        return [project]
    active = list(getattr(workspace, "active_projects", []) or [])
    return active or [None]


def _ambiguity_error(workspace, ref: str, nodes: list[dict]) -> str:
    """Return copyable refs rooted at explicit storage source nodes."""
    from tool.utils.display_ref import display_ref_for_node

    options = []
    for node in nodes[:20]:
        option = display_ref_for_node(workspace, node.get("__project") or None, node)
        if option not in options:
            options.append(option)
    suffix = "\n可选 source ref：\n" + "\n".join(f"- {item}" for item in options) if options else ""
    return f"匹配到多个实体: {ref}{suffix}"


def _run_cypher_projects(workspace, query: str, params: dict, project: str | None) -> list[dict]:
    rows = []
    for candidate in _query_projects(workspace, project):
        for row in workspace.cypher(query, params=params, project=candidate):
            rows.append(_tag_row_project(row, candidate))
    return rows


def _tag_row_project(row: dict, project: str | None) -> dict:
    if not project:
        return row
    tagged = {}
    for key, value in row.items():
        if isinstance(value, dict):
            copy = dict(value)
            copy.setdefault("__project", project)
            tagged[key] = copy
        else:
            tagged[key] = value
    return tagged


def _candidate_structured_refs(local_ref: str) -> list[str]:
    candidates: list[str] = []

    def add(ref: str):
        if ref and ref not in candidates:
            candidates.append(ref)

    add(local_ref)

    if "->" in local_ref:
        return candidates

    if "/" in local_ref:
        parts = [p for p in local_ref.split("/") if p]
        if parts:
            last = parts[-1]
            dotted = [p for p in last.split(".") if p]
            if len(dotted) == 2:
                add("/".join(parts[:-1] + dotted))
    else:
        dotted = [p for p in local_ref.split(".") if p]
        if len(dotted) == 2:
            add(f"{dotted[0]}/{dotted[1]}")

    return candidates


def resolve_entity(workspace, ref: str) -> tuple[dict | None, str | None]:
    """将 ref 解析为唯一节点元数据。"""
    from tool.utils.ref_match import normalize_project_slash_ref

    try:
        ref = normalize_project_slash_ref(workspace, ref)
    except ValueError as exc:
        return None, str(exc)
    project, local_ref = _split_project_ref(ref)
    local_ref = _strip_outer_quotes(dotted_ref_to_path(local_ref))

    # Brackets are ordinary entity-name punctuation. Only the public glob
    # operators make a ref a wildcard input.
    has_wildcards = any(c in local_ref for c in "*?")
    is_path_like = "/" in local_ref
    looks_structured = is_path_like or ("." in local_ref and "->" not in local_ref)

    if not is_path_like:
        nodes = _lookup_exact_named_nodes(workspace, project, local_ref)
        if len(nodes) == 1:
            return nodes[0], None
        if len(nodes) > 1:
            return None, _ambiguity_error(workspace, ref, nodes)
        if not has_wildcards and not looks_structured:
            return None, f"未找到匹配的实体: {ref}"

    if not has_wildcards and looks_structured:
        node, err = _resolve_exact_path(workspace, project, local_ref)
        if err or not node:
            return None, err or f"未找到匹配的实体: {ref}"
        return node, None

    matched = _lookup_urn_nodes(workspace, ref)
    if not matched:
        return None, f"未找到匹配的实体: {ref}"
    if len(matched) > 1:
        return None, _ambiguity_error(workspace, ref, matched)
    return matched[0], None


def resolve_entity_selector(workspace, ref: str) -> tuple[dict | None, str | None]:
    """将 ref 解析为 Cypher 可用的稳定选择器。"""
    node, err = resolve_entity(workspace, ref)
    if err:
        return None, err
    return {
        "project": node.get("__project") or None,
        "id": None,
        "name": node.get("name", ref),
        "labels": list(node.get("labels", [])),
        "path": node.get("path"),
        "ref": node.get("_ref") or node.get("ref"),
        "ref_key": "_ref" if node.get("_ref") else ("ref" if node.get("ref") else None),
        "meta": dict(node),
    }, None


def canonical_ref(node: dict, fallback: str = "") -> str:
    """Return the most stable writable ref for a resolved node."""
    return node.get("_ref") or node.get("ref") or node.get("path") or node.get("name") or fallback


def selector_match_pattern(selector: dict, var: str = "n", name_param: str = "name") -> str:
    labels = cypher_label_clause(selector.get("labels", []))
    if selector.get("id"):
        return f"({var}{labels} {{id: $id}})"
    if selector.get("path"):
        return f"({var}{labels} {{path: $path}})"
    if selector.get("ref"):
        ref_key = selector.get("ref_key") or "_ref"
        return f"({var}{labels} {{{ref_key}: $ref}})"
    return f"({var}{labels} {{name: ${name_param}}})"


def selector_params(selector: dict, base: dict | None = None, name_param: str = "name") -> dict:
    params = dict(base or {})
    if selector.get("id"):
        params["id"] = selector["id"]
    elif selector.get("path"):
        params["path"] = selector["path"]
    elif selector.get("ref"):
        params["ref"] = selector["ref"]
    else:
        params[name_param] = selector["name"]
    return params


def _lookup_exact_named_nodes(workspace, project: str | None, local_ref: str) -> list[dict]:
    rows = _run_cypher_projects(
        workspace,
        "MATCH (n {name: $name}) RETURN n",
        params={"name": local_ref},
        project=project,
    )
    nodes = _dedupe_nodes(row.get("n") for row in rows if row.get("n"))
    if not nodes:
        rows = _run_cypher_projects(
            workspace,
            "MATCH (n:file) WHERE n.name = $name OR n.path = $name RETURN n",
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
    rows = _run_cypher_projects(
        workspace,
        "MATCH (n {name: $name}) RETURN n",
        params={"name": base_name},
        project=project,
    )
    matched = []
    fallback_knowledge = []
    for row in rows:
        node = row.get("n")
        if not node:
            continue
        labels = set(node.get("labels", []))
        if requested_labels.issubset(labels):
            matched.append(node)
        if "knowledge" in requested_labels and "knowledge" in labels:
            fallback_knowledge.append(node)
    matched = _dedupe_nodes(matched)
    if matched:
        return matched
    fallback_knowledge = _dedupe_nodes(fallback_knowledge)
    if len(fallback_knowledge) == 1:
        return fallback_knowledge
    return matched


def _resolve_exact_path(workspace, project: str | None, local_ref: str) -> tuple[dict | None, str | None]:
    # Public refs are real graph paths rooted at the project source node and may be
    # longer than the legacy db/table/col shapes. Resolve the exact path first
    # before applying old shorthand repairs.
    # ref_match supports fnmatch character classes, so a literal bracketed
    # path cannot be sent through it as-is. Resolve the terminal entity by its
    # exact name/label, then verify the actual source-rooted display path.
    if "[" in local_ref or "]" in local_ref:
        terminal = local_ref.rsplit("/", 1)[-1]
        candidates = _lookup_exact_named_nodes(workspace, project, terminal)
        if candidates:
            from tool.utils.display_ref import display_ref_for_node

            matched = []
            for candidate in candidates:
                candidate_project = candidate.get("__project") or project
                display_ref = display_ref_for_node(workspace, candidate_project, candidate)
                if "::" in display_ref:
                    _, display_ref = display_ref.split("::", 1)
                if display_ref == local_ref:
                    matched.append(candidate)
            matched = _dedupe_nodes(matched)
            if len(matched) == 1:
                return matched[0], None
            if len(matched) > 1:
                return None, _ambiguity_error(workspace, local_ref, matched)

    exact_urn = f"{project}::{local_ref}" if project else local_ref
    exact_nodes = _lookup_urn_nodes(workspace, exact_urn)
    if len(exact_nodes) == 1:
        return exact_nodes[0], None
    if len(exact_nodes) > 1:
        return None, _ambiguity_error(workspace, local_ref, exact_nodes)

    normalized_relation_ref = _normalize_relation_ref(local_ref)
    if normalized_relation_ref:
        rows = _run_cypher_projects(
            workspace,
            "MATCH (n {name: $name}) RETURN n",
            params={"name": normalized_relation_ref},
            project=project,
        )
        nodes = _dedupe_nodes(row.get("n") for row in rows if row.get("n"))
        if len(nodes) == 1:
            return nodes[0], None
        if len(nodes) > 1:
            return None, f"匹配到多个实体: {local_ref}"

    parts = _split_structured_path(local_ref)
    parts = _repair_repeated_file_label(parts)
    parts = _repair_missing_table_column_segment(parts)
    label_err = _validate_display_labels(parts, local_ref)
    if label_err:
        return None, label_err
    normalized_parts = [_normalize_display_segment(p) for p in parts]
    normalized_ref = "/".join(normalized_parts)
    requested_labels = _display_labels_from_last_segment(parts[-1] if parts else "")

    pattern_node, pattern_err = _resolve_file_pattern_ref(
        workspace,
        project,
        local_ref,
        requested_labels,
    )
    if pattern_node or pattern_err:
        return pattern_node, pattern_err

    candidates = []
    for ref in _candidate_structured_refs(normalized_ref):
        if ref not in candidates:
            candidates.append(ref)
    if normalized_ref != local_ref:
        for ref in _candidate_structured_refs(local_ref):
            if ref not in candidates:
                candidates.append(ref)

    for candidate in candidates:
        direct = _resolve_direct_candidate(workspace, project, candidate, requested_labels)
        if len(direct) == 1:
            return direct[0], None
        if len(direct) > 1:
            return None, f"匹配到多个实体: {local_ref}"

        urn = f"{project}::{candidate}" if project else candidate
        nodes = _lookup_urn_nodes(workspace, urn)
        if requested_labels:
            nodes = _filter_by_requested_labels(nodes, requested_labels)
        if len(nodes) == 1:
            return nodes[0], None
        if len(nodes) > 1:
            return None, f"匹配到多个实体: {local_ref}"

    if requested_labels and parts:
        tail_name = _normalize_display_segment(parts[-1])
        fallback = _lookup_exact_named_nodes(
            workspace, project, f"{tail_name}:{':'.join(sorted(requested_labels))}"
        )
        if len(fallback) == 1:
            return fallback[0], None
        if len(fallback) > 1:
            return None, f"匹配到多个实体: {local_ref}"

    if len(normalized_parts) == 2:
        if requested_labels and not (requested_labels & {"file", "db", "table", "view", "csv_table"}):
            return None, (
                f"ref 路径结构不合法: {local_ref}；列路径应写为 "
                "db.sqlite:db/table_name:table/column_name:col"
            )
        file_name, table_name = normalized_parts
        rows = _run_cypher_projects(
            workspace,
            "MATCH (f:file {name: $file_name})--(t:table {name: $table_name}) RETURN t",
            params={"file_name": file_name, "table_name": table_name},
            project=project,
        )
        if not rows:
            rows = _run_cypher_projects(
                workspace,
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
            rows = _run_cypher_projects(
                workspace,
                "MATCH (f:file {name: $file_name})--(t)--(k:fk) "
                "WHERE k.name = $fk_name "
                "RETURN k",
                params={"file_name": file_name, "fk_name": fk_name},
                project=project,
            )
            nodes = _dedupe_nodes(row.get("k") for row in rows if row.get("k"))
            if not nodes:
                return None, f"未找到匹配的实体: {local_ref}"
            if len(nodes) > 1:
                return None, f"匹配到多个实体: {local_ref}"
            return nodes[0], None

        file_name, table_name, col_name = normalized_parts
        rows = _run_cypher_projects(
            workspace,
            "MATCH (f:file {name: $file_name})--(t)--(c:col) "
            "WHERE t.name = $table_name AND c.name = $col_name "
            "RETURN c",
            params={"file_name": file_name, "table_name": table_name, "col_name": col_name},
            project=project,
        )
        nodes = _dedupe_nodes(row.get("c") for row in rows if row.get("c"))
        if requested_labels:
            nodes = _filter_by_requested_labels(nodes, requested_labels)
        if not nodes:
            return None, f"未找到匹配的实体: {local_ref}"
        if len(nodes) > 1:
            return None, f"匹配到多个实体: {local_ref}"
        return nodes[0], None

    return None, f"未找到匹配的实体: {local_ref}"


def _resolve_file_pattern_ref(
    workspace,
    project: str | None,
    local_ref: str,
    requested_labels: set[str],
) -> tuple[dict | None, str | None]:
    raw_parts = [p for p in local_ref.split("/") if p]
    if len(raw_parts) < 2:
        return None, None

    pattern_ref = _strip_display_label_suffix(raw_parts[-1])
    if "pattern" not in requested_labels:
        return None, None

    file_ref = "/".join(_strip_display_label_suffix(part) for part in raw_parts[:-1])
    json_path = pattern_ref
    candidates = [pattern_ref]

    rows = _run_cypher_projects(
        workspace,
        "MATCH (f:file)--(p:pattern) "
        "WHERE (f.path = $file_ref OR f.name = $file_ref) "
        "AND (p.name IN $candidates OR p.json_path IN $candidates) "
        "RETURN p",
        params={"file_ref": file_ref, "candidates": candidates},
        project=project,
    )
    nodes = _dedupe_nodes(row.get("p") for row in rows if row.get("p"))
    if requested_labels:
        nodes = _filter_by_requested_labels(nodes, requested_labels)
    if not nodes:
        return None, f"未找到匹配的实体: {local_ref}"
    if len(nodes) > 1:
        return None, f"匹配到多个实体: {local_ref}"
    return nodes[0], None


def _resolve_direct_candidate(
    workspace,
    project: str | None,
    candidate: str,
    requested_labels: set[str],
) -> list[dict]:
    direct = []
    rows = _run_cypher_projects(
        workspace,
        "MATCH (n {name: $name}) RETURN n",
        params={"name": candidate},
        project=project,
    )
    direct = [row.get("n") for row in rows if row.get("n")]
    if not direct:
        rows = _run_cypher_projects(
            workspace,
            "MATCH (n) WHERE n._ref = $ref OR n.ref = $ref RETURN n",
            params={"ref": candidate},
            project=project,
        )
        direct = [row.get("n") for row in rows if row.get("n")]
    if not direct:
        rows = _run_cypher_projects(
            workspace,
            "MATCH (n {path: $path}) RETURN n",
            params={"path": candidate},
            project=project,
        )
        direct = [row.get("n") for row in rows if row.get("n")]
    if not direct:
        rows = _run_cypher_projects(
            workspace,
            "MATCH (n:file) WHERE n.name = $value OR n.path = $value RETURN n",
            params={"value": candidate},
            project=project,
        )
        direct = [row.get("n") for row in rows if row.get("n")]
    candidate_parts = _split_structured_path(candidate)
    candidate_ref = _ref_from_path_parts(candidate_parts)
    if not direct and candidate_ref:
        rows = _run_cypher_projects(
            workspace,
            "MATCH (n) WHERE n._ref = $ref OR n.ref = $ref RETURN n",
            params={"ref": candidate_ref},
            project=project,
        )
        direct = [row.get("n") for row in rows if row.get("n")]
    if requested_labels:
        direct = _filter_by_requested_labels(direct, requested_labels)
    return _dedupe_nodes(direct)


def _dedupe_nodes(nodes) -> list[dict]:
    result = []
    seen = set()
    for node in nodes:
        key = (
            node.get("__project", ""),
            node.get("id") or node.get("_ref") or node.get("ref") or node.get("path") or node.get("name"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(node)
    return result


def _lookup_urn_nodes(workspace, ref: str) -> list[dict]:
    from tool.utils.ref_match import _apply_post_filters, _build_cypher, _execute_projects, parse_urn

    project, segments = parse_urn(ref)
    cypher, post_filters = _build_cypher(segments)

    rows = _execute_projects(workspace, cypher, project)
    if post_filters:
        rows = _apply_post_filters(rows, post_filters)

    nodes = []
    for row in rows:
        for var_key in reversed(list(row.keys())):
            node = row.get(var_key)
            if isinstance(node, dict):
                nodes.append(node)
                break
    return _dedupe_nodes(nodes)


def _ref_from_path_parts(parts: list[str]) -> str | None:
    if len(parts) == 2:
        return f"{parts[0]}--{parts[1]}"
    if len(parts) == 3 and parts[1] == "fks":
        return f"{parts[0]}--{parts[2]}"
    if len(parts) == 3:
        return f"{parts[0]}--{parts[1]}--{parts[2]}"
    return None


def _display_labels_from_last_segment(segment: str) -> set[str]:
    if ":" not in segment:
        return set()
    parts = [p for p in segment.split(":")[1:] if p]
    return set(parts)


def _validate_display_labels(parts: list[str], local_ref: str) -> str | None:
    for segment in parts:
        if ":" not in segment:
            continue
        raw_parts = [p for p in segment.split(":") if p]
        if len(raw_parts) < 2:
            continue
        name = _strip_outer_quotes(raw_parts[0].strip())
        labels = {_strip_outer_quotes(label.strip()) for label in raw_parts[1:]}
        if name and name in labels:
            return (
                f"ref 路径结构不合法: {local_ref}；路径段应写为 "
                "entity_name:label，不要把实体名重复写成标签"
            )
    return None


def _repair_missing_table_column_segment(parts: list[str]) -> list[str]:
    if len(parts) != 2:
        return parts
    table_col = [p for p in parts[1].split(":") if p]
    if len(table_col) != 3 or table_col[2] != "col":
        return parts
    table_name, column_name, _ = table_col
    if not table_name or not column_name:
        return parts
    return [parts[0], f"{table_name}:table", f"{column_name}:col"]


def _repair_repeated_file_label(parts: list[str]) -> list[str]:
    if not parts or ":" not in parts[0]:
        return parts
    raw = [p for p in parts[0].split(":") if p]
    if len(raw) < 3:
        return parts
    if raw[0] != raw[1]:
        return parts
    if raw[-1] not in {"db", "file"}:
        return parts
    return [":".join([raw[0], *raw[2:]]), *parts[1:]]


def _split_structured_path(local_ref: str) -> list[str]:
    parts = [p for p in local_ref.split("/") if p]
    if len(parts) > 3:
        return [parts[0], parts[1], "/".join(parts[2:])]
    return parts


def _filter_by_requested_labels(nodes: list[dict], requested_labels: set[str]) -> list[dict]:
    matched = [
        node for node in nodes
        if requested_labels.issubset(set(node.get("labels", [])))
        and not (
            "table" in requested_labels
            and "csv_table" not in requested_labels
            and "csv_table" in set(node.get("labels", []))
        )
    ]
    return matched if requested_labels else nodes
