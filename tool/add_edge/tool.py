"""Add Edge tool — 通过 Cypher CREATE 添加无向边。"""

from tool.utils import execute_cypher
from tool.utils.resolve import resolve_entity


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

        a_id, a_err = resolve_entity(workspace, a_ref)
        if a_err:
            results.append(f"跳过 a: {a_err}")
            continue

        b_id, b_err = resolve_entity(workspace, b_ref)
        if b_err:
            results.append(f"跳过 b: {b_err}")
            continue

        valid_edges.append({"a_id": a_id, "b_id": b_id,
                            "a_ref": a_ref, "b_ref": b_ref})

    if not valid_edges:
        if results:
            return "\n".join(results)
        return "没有有效的边可添加"

    for e in valid_edges:
        cypher = 'MATCH (a {id: $a_id}),(b {id: $b_id}) CREATE (a)--(b)'
        execute_cypher(workspace, cypher, params={"a_id": e["a_id"], "b_id": e["b_id"]})

    results.insert(0, f"已添加 {len(valid_edges)} 条边:")
    for e in valid_edges:
        results.append(f"  {e['a_ref']} ↔ {e['b_ref']}")

    return "\n".join(results)
