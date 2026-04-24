"""Add Edge tool executor — 为已有实体添加关系边。"""


def add_edge_command(store, edges: list) -> str:
    """为已有节点添加无向边。

    Args:
        store: Store 实例
        edges: 边列表 [{"a": "...", "b": "..."}, ...]
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

        if not store.node_exists(a_ref):
            results.append(f"跳过: 节点不存在: {a_ref}")
            continue

        if not store.node_exists(b_ref):
            results.append(f"跳过: 节点不存在: {b_ref}")
            continue

        valid_edges.append(e)

    if not valid_edges:
        if results:
            return "\n".join(results)
        return "没有有效的边可添加"

    store.add_edges(valid_edges)
    results.insert(0, f"已添加 {len(valid_edges)} 条边:")
    for e in valid_edges:
        results.append(f"  {e['a']} ↔ {e['b']}")

    return "\n".join(results)
