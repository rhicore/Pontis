"""Add Edge tool — 通过 RELATED_TO 关系连接已有节点。"""

from storage.query_inspector import cypher_label_clause
from tool.utils.resolve import resolve_entity_selector


def _selector_pattern(selector: dict, var: str, prefix: str) -> tuple[str, dict]:
    labels = cypher_label_clause(selector.get("labels", []))
    if selector.get("path"):
        return f"({var}{labels} {{path: ${prefix}_path}})", {f"{prefix}_path": selector["path"]}
    if selector.get("ref"):
        ref_key = selector.get("ref_key") or "_ref"
        return f"({var}{labels} {{{ref_key}: ${prefix}_ref}})", {f"{prefix}_ref": selector["ref"]}
    return f"({var}{labels} {{name: ${prefix}_name}})", {f"{prefix}_name": selector["name"]}


def _relation_endpoint_db_refs(workspace, *, project: str | None, node_id: str | None) -> list[str]:
    if not node_id:
        return []
    rows = workspace.cypher(
        """
        MATCH (r {id: $id})--(endpoint)
        WHERE any(label IN coalesce(r.labels, []) WHERE label IN ['fk', 'rel', 'overlap'])
          AND endpoint._db_ref IS NOT NULL
        RETURN collect(DISTINCT endpoint._db_ref) AS db_refs
        """,
        params={"id": node_id},
        project=project,
    )
    if not rows:
        return []
    return list(rows[0].get("db_refs") or [])


def _link_relation_entity_to_db(workspace, *, project: str | None, node: dict) -> int:
    if not set(node.get("labels") or []) & {"fk", "rel", "overlap"}:
        return 0
    db_refs = _relation_endpoint_db_refs(workspace, project=project, node_id=node.get("id"))
    if not db_refs:
        return 0
    rows = workspace.cypher(
        """
        UNWIND $db_refs AS db_ref
        MATCH (db {_ref: db_ref}), (r {id: $id})
        MERGE (db)-[:RELATED_TO]->(r)
        RETURN count(DISTINCT db) AS n
        """,
        params={"id": node.get("id"), "db_refs": db_refs},
        project=project,
    )
    if not rows:
        return 0
    return int(rows[0].get("n") or 0)


def add_edge_command(workspace, edges: list) -> str:
    """通过 Cypher 为已有节点添加 RELATED_TO 边。

    edges 中 a/b 支持两种模式：
      - 精确名称 → 直接匹配
      - ref 模式 → 必须匹配唯一实体
    """
    if not edges:
        return "错误: edges 不能为空"

    results = []
    valid_edges = []

    for e in edges:
        a_ref = e.get("a", "")
        b_ref = e.get("b", "")

        if not a_ref or not b_ref:
            results.append(f"跳过: 缺少必填字段 (a={a_ref}, b={b_ref})")
            continue

        a_node, a_err = resolve_entity_selector(workspace, a_ref)
        if a_err:
            results.append(f"跳过 a: {a_err}")
            continue

        b_node, b_err = resolve_entity_selector(workspace, b_ref)
        if b_err:
            results.append(f"跳过 b: {b_err}")
            continue

        a_project = a_node.get("project") or None
        b_project = b_node.get("project") or None
        project = a_project or b_project
        if a_project and b_project and a_project != b_project:
            results.append(f"跳过: 暂不支持跨项目加边 ({a_ref} ↔ {b_ref})")
            continue

        valid_edges.append({
            "project": project,
            "a_selector": a_node,
            "b_selector": b_node,
            "a_ref": a_ref,
            "b_ref": b_ref,
        })

    if not valid_edges:
        if results:
            return "\n".join(results)
        return "没有有效的边可添加"

    for e in valid_edges:
        a_pat, a_params = _selector_pattern(e["a_selector"], "a", "a")
        b_pat, b_params = _selector_pattern(e["b_selector"], "b", "b")
        rows = workspace.cypher(
            f"MATCH {a_pat}, {b_pat} MERGE (a)-[r:RELATED_TO]->(b) RETURN count(r) AS created",
            params={**a_params, **b_params},
            project=e["project"],
        )
        if not rows:
            results.append(f"跳过: 无法创建边 ({e['a_ref']} ↔ {e['b_ref']})")
            continue
        e["db_edges"] = (
            _link_relation_entity_to_db(workspace, project=e["project"], node=e["a_selector"])
            + _link_relation_entity_to_db(workspace, project=e["project"], node=e["b_selector"])
        )

    results.insert(0, f"已添加 {len(valid_edges)} 条边:")
    for e in valid_edges:
        results.append(f"  {e['a_ref']} ↔ {e['b_ref']}")
    db_edges = sum(int(e.get("db_edges") or 0) for e in valid_edges)
    if db_edges:
        results.append(f"DB relation index edges: {db_edges}")

    return "\n".join(results)
