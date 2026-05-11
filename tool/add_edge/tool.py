"""Add Edge tool — 通过 Cypher CREATE 添加无向边。"""

from tool.utils import execute_cypher
from tool.utils.resolve import resolve_entity_selector, selector_match_pattern


def add_edge_command(workspace, edges: list) -> str:
    """通过 Cypher 为已有节点添加无向边。

    edges 中 a/b 支持两种模式：
      - 精确名称 → 直接匹配
      - glob 模式 → 必须匹配唯一实体
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

        a_sel, a_err = resolve_entity_selector(workspace, a_ref)
        if a_err:
            results.append(f"跳过 a: {a_err}")
            continue

        b_sel, b_err = resolve_entity_selector(workspace, b_ref)
        if b_err:
            results.append(f"跳过 b: {b_err}")
            continue

        a_project = a_sel["project"]
        b_project = b_sel["project"]
        project = a_project or b_project
        if a_project and b_project and a_project != b_project:
            results.append(f"跳过: 暂不支持跨项目加边 ({a_ref} ↔ {b_ref})")
            continue

        valid_edges.append({
            "project": project,
            "a_sel": a_sel,
            "b_sel": b_sel,
            "a_ref": a_ref,
            "b_ref": b_ref,
        })

    if not valid_edges:
        if results:
            return "\n".join(results)
        return "没有有效的边可添加"

    for e in valid_edges:
        workspace.materialize(e["a_sel"]["name"], project=e["project"])
        workspace.materialize(e["b_sel"]["name"], project=e["project"])
        a_match = selector_match_pattern(e["a_sel"], "a", "a_name")
        b_match = selector_match_pattern(e["b_sel"], "b", "b_name")
        execute_cypher(
            workspace,
            f"MATCH {a_match}, {b_match} CREATE (a)--(b)",
            params={
                "a_name": e["a_sel"]["name"],
                "b_name": e["b_sel"]["name"],
            },
            project=e["project"],
        )

    results.insert(0, f"已添加 {len(valid_edges)} 条边:")
    for e in valid_edges:
        results.append(f"  {e['a_ref']} ↔ {e['b_ref']}")

    return "\n".join(results)
