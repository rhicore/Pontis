"""Delete tool executor — 删除节点，支持级联。"""


def delete_command(store, ref: str) -> str:
    """删除知识图谱节点。

    Args:
        store: Store 实例
        ref: 要删除的节点 ref

    Returns:
        删除结果描述
    """
    if not store.pontis_exists:
        return f"错误: 未找到 .pontis 目录 ({store.project_path})"

    if not store.node_exists(ref):
        return f"节点不存在: {ref}"

    deleted = store.delete_node(ref)

    if not deleted:
        return f"删除失败: {ref}"

    lines = [f"已删除 {len(deleted)} 个节点:"]
    for d in deleted:
        lines.append(f"  - {d}")

    return "\n".join(lines)
