"""Create entity tool — 统一创建入口。

创建规则（类似 mkdir）：
  - ref 只表示待创建实体的名称和标签，不承载路径或边匹配逻辑
  - labels 从 ref 中的 :tag 语法提取，例如 name:rel
  - 创建实体必须通过 edges 连接到至少一个已有实体
"""

import json

from tool.utils import execute_cypher
from storage.query_inspector import cypher_label_clause, is_valid_label
from tool.utils.knowledge_meta import is_bird_knowledge, normalize_knowledge_meta
from tool.utils.resolve import resolve_entity_selector


def _selector_pattern(selector: dict, var: str, prefix: str) -> tuple[str, dict]:
    labels = cypher_label_clause(selector.get("labels", []))
    if selector.get("id"):
        return f"({var}{labels} {{id: ${prefix}_id}})", {f"{prefix}_id": selector["id"]}
    if selector.get("path"):
        return f"({var}{labels} {{path: ${prefix}_path}})", {f"{prefix}_path": selector["path"]}
    if selector.get("ref"):
        ref_key = selector.get("ref_key") or "_ref"
        return f"({var}{labels} {{{ref_key}: ${prefix}_ref}})", {f"{prefix}_ref": selector["ref"]}
    return f"({var}{labels} {{name: ${prefix}_name}})", {f"{prefix}_name": selector["name"]}

def _parse_ref(ref: str) -> tuple:
    """从 ref 中提取实体名和标签。

    格式: name[:tag1[:tag2[...]]]

    Returns:
        (name, labels, project)
    """
    project = None

    if "::" in ref:
        idx = ref.index("::")
        project = ref[:idx]
        ref = ref[idx + 2:]

    # 解析 :tag 标签
    parts = ref.split(":")
    name = parts[0]
    labels = parts[1:] if len(parts) > 1 else []

    return (name, labels, project)


def _edge_source_for_created_node(edges: list | None, name: str, ref: str) -> str | None:
    if not edges:
        return None
    for edge in edges:
        if edge.get("ref"):
            return edge["ref"]
    return None


def _has_wildcards(ref: str) -> bool:
    # Entity names may contain punctuation. Only explicit glob operators are
    # treated as wildcards.
    return any(c in ref for c in '*?')


def _has_entity_ref_structure(name: str) -> bool:
    return "/" in name or "\\" in name


def _link_relation_entity_to_db(workspace, *, project: str | None, node_id: str | None) -> int:
    if not node_id:
        return 0
    rows = workspace.cypher(
        """
        MATCH (r {id: $id})--(endpoint)
        WHERE any(label IN coalesce(r.labels, []) WHERE label IN ['fk', 'rel', 'overlap'])
          AND coalesce(endpoint._db_ref, endpoint.db_ref) IS NOT NULL
        WITH r, collect(DISTINCT coalesce(endpoint._db_ref, endpoint.db_ref)) AS db_refs
        UNWIND db_refs AS db_ref
        MATCH (db {_ref: db_ref})
        MERGE (db)-[:RELATED_TO]->(r)
        RETURN count(DISTINCT db) AS n
        """,
        params={"id": node_id},
        project=project,
    )
    if not rows:
        return 0
    return int(rows[0].get("n") or 0)


def _create_explicit_edges(
    workspace,
    *,
    project: str | None,
    name: str,
    ref: str,
    labels: list,
    node_id: str | None,
    edges: list | None,
) -> list[str]:
    edge_results = []
    if not edges:
        return edge_results

    for e in edges:
        endpoint = e.get("ref", "")
        if not endpoint:
            edge_results.append("  跳过: edges 项缺少 ref")
            continue
        if endpoint in {name, ref}:
            edge_results.append(f"  跳过: edges.ref 不能指向正在创建的实体 '{endpoint}'")
            continue

        endpoint_selector, endpoint_err = resolve_entity_selector(workspace, endpoint)
        if endpoint_err:
            edge_results.append(f"  跳过: {endpoint_err}")
            continue
        if not endpoint_selector:
            edge_results.append(f"  跳过: 无法解析端点 '{endpoint}'")
            continue
        endpoint_pat, endpoint_params = _selector_pattern(endpoint_selector, "endpoint", "endpoint")
        rows = workspace.cypher(
            f"MATCH (created {{id: $created_id}}), {endpoint_pat} "
            "MERGE (endpoint)-[r:RELATED_TO]->(created) "
            "RETURN count(r) AS created",
            params={**endpoint_params, "created_id": node_id},
            project=project,
        )
        if not rows:
            edge_results.append(f"  跳过: 无法创建边 '{name}' / '{endpoint}'")
            continue
        edge_results.append(f"  {name} ↔ {endpoint}")

    return edge_results


def create_entity_command(workspace, ref: str, meta: dict = None,
                          edges: list = None) -> str:
    """创建实体节点。

    Args:
        workspace: Workspace 实例
        ref: 实体引用，格式 name[:tag1[:tag2]]
        meta: 元数据
        edges: 连接端点列表 [{"ref": "..."}]
    """
    if not edges:
        return "错误: create_entity 必须提供至少一条 edges，不能创建孤立实体"
    if any(not isinstance(edge, dict) or not edge.get("ref") for edge in edges):
        return '错误: create_entity.edges 每一项必须是 {"ref": "..."}'

    # 禁止通配符
    if _has_wildcards(ref):
        return "错误: 实体名不允许包含通配符 (*, ?)"
    name, labels, project = _parse_ref(ref)

    if not name:
        return "错误: 实体名不能为空"
    if _has_entity_ref_structure(name):
        return "错误: create_entity.ref 的实体名称不能包含 / 或 \\；路径端点请写在 edges[].ref"
    invalid_labels = [label for label in labels if not is_valid_label(label)]
    if invalid_labels:
        return f"错误: 非法标签: {', '.join(invalid_labels)}"

    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            return "错误: create_entity.meta 必须是对象；收到的是无法解析为 JSON 对象的字符串"
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        return "错误: create_entity.meta 必须是对象"
    meta = dict(meta)

    existing_rows = workspace.cypher(
        'MATCH (n {name: $name}) RETURN n',
        params={"name": name},
        project=project,
    )
    requested_labels = set(labels or [])
    if existing_rows:
        same_label_exists = any(
            set(row.get("n", {}).get("labels", [])) == requested_labels
            for row in existing_rows
        )
        if "chunk" in requested_labels and same_label_exists:
            source_ref = _edge_source_for_created_node(edges, name, ref)
            if not source_ref:
                return f"实体已存在: {name}"
            source_selector, source_err = resolve_entity_selector(workspace, source_ref)
            if source_err:
                return f"Error: {source_err}"
            source_pat, source_params = _selector_pattern(source_selector, "s", "s")
            label_str = cypher_label_clause(labels or [])
            rows = workspace.cypher(
                f"MATCH {source_pat}--(c{label_str} {{name: $name}}) RETURN c",
                params={**source_params, "name": name},
                project=project,
            )
            if rows:
                return f"实体已存在: {name}"
            existing_rows = [
                row for row in existing_rows
                if set(row.get("n", {}).get("labels", [])) != requested_labels
            ]

        if not requested_labels:
            return f"实体已存在: {name}"
        for row in existing_rows:
            existing_labels = set(row.get("n", {}).get("labels", []))
            if existing_labels == requested_labels:
                existing = row.get("n", {})
                edge_results = _create_explicit_edges(
                    workspace,
                    project=project,
                    name=name,
                    ref=ref,
                    labels=labels or [],
                    node_id=existing.get("id"),
                    edges=edges,
                )
                db_edges = 0
                if set(labels or []) & {"fk", "rel", "overlap"}:
                    db_edges = _link_relation_entity_to_db(
                        workspace,
                        project=project,
                        node_id=existing.get("id"),
                    )
                if edge_results or db_edges:
                    lines = [f"实体已存在: {name}", "Updated existing edges"]
                    if edge_results:
                        lines.append(f"Edges ({len(edge_results)}):")
                        lines.extend(edge_results)
                    if db_edges:
                        lines.append(f"DB relation index edges: {db_edges}")
                    return "\n".join(lines)
                return f"实体已存在: {name}"

    meta = normalize_knowledge_meta(project, labels, meta)
    if is_bird_knowledge(project, labels):
        if not str(meta.get("brief", "")).strip():
            return "错误: bird 知识实体必须提供非空 brief（可由结构化字段自动推导）"
        if not str(meta.get("detail", "")).strip():
            return "错误: bird 知识实体必须提供非空 detail（可由结构化字段自动推导）"

    props = dict(meta)
    props["name"] = name
    props["labels"] = list(labels or [])
    prop_values = {k: v for k, v in props.items() if not k.startswith("_")}

    label_str = cypher_label_clause(labels or [])
    created = execute_cypher(
        workspace,
        f"CREATE (n{label_str} {{name: $name}}) "
        "SET n.id = 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8) "
        "SET n += $props RETURN n",
        params={"name": name, "props": prop_values},
        project=project,
    )
    if not created:
        return f"Error: failed to create entity: {name}"
    created_node = created[0].get("n", {}) if isinstance(created[0], dict) else {}
    created_id = created_node.get("id")

    edge_results = _create_explicit_edges(
        workspace,
        project=project,
        name=name,
        ref=ref,
        labels=labels or [],
        node_id=created_id,
        edges=edges,
    )

    db_edges = 0
    if set(labels or []) & {"fk", "rel", "overlap"}:
        db_edges = _link_relation_entity_to_db(workspace, project=project, node_id=created_id)

    lines = [f"Created: {name}"]
    if labels:
        lines.append(f"Labels: {', '.join(labels)}")
    if edge_results:
        lines.append(f"Edges ({len(edge_results)}):")
        lines.extend(edge_results)
    if db_edges:
        lines.append(f"DB relation index edges: {db_edges}")

    return "\n".join(lines)


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m tool.create_entity.tool <project_name> <ref> [meta_json]")
        sys.exit(1)

    from storage.workspace import Workspace
    ws = Workspace(active_projects=[sys.argv[1]])
    _ref = sys.argv[2]
    _meta = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    print(create_entity_command(ws, _ref, _meta))
