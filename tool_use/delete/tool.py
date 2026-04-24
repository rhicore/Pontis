"""Delete tool executor — 删除节点，支持按实体层级级联删除。"""
from tool_use.config import ENTITY_DEPENDENCY_RANK


def _get_entity_suffix(ref: str) -> str:
    """从 ref 中提取实体类型后缀。"""
    if "::" in ref:
        entity_part = ref.split("::", 1)[1]
    else:
        entity_part = ref
    dot_idx = entity_part.rfind(".")
    if dot_idx >= 0:
        return entity_part[dot_idx:]
    return ""


def _is_more_derived(ref_a: str, ref_b: str) -> bool:
    """判断 ref_a 是否比 ref_b 更是派生实体（rank 更高）。"""
    rank_a = ENTITY_DEPENDENCY_RANK.get(_get_entity_suffix(ref_a), -1)
    rank_b = ENTITY_DEPENDENCY_RANK.get(_get_entity_suffix(ref_b), -1)
    return rank_a > rank_b


def delete_command(store, ref: str) -> str:
    """删除知识图谱节点，级联删除依赖该节点的派生实体。

    级联规则（应用层）：
    1. 找到所有与该节点直接相连的邻居
    2. 如果邻居是更派生的实体（rank 更高），加入级联队列
    3. storage 层只负责删节点+边（无悬挂边不变量）

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

    deleted = []
    queue = [ref]

    while queue:
        current = queue.pop(0)

        if not store.node_exists(current):
            continue

        # 先获取邻居（删节点后边就没了）
        neighbors = store.find_connected(current, pattern="*")

        # storage 层：删节点 + 清理边
        removed = store.delete_node(current)
        if removed:
            deleted.append(removed)

        # 应用层级联：rank 更高的邻居是派生实体，应级联删除
        for neighbor_ref in neighbors:
            if _is_more_derived(neighbor_ref, current):
                if store.node_exists(neighbor_ref):
                    queue.append(neighbor_ref)

    if not deleted:
        return f"删除失败: {ref}"

    lines = [f"已删除 {len(deleted)} 个节点:"]
    for d in deleted:
        lines.append(f"  - {d}")

    return "\n".join(lines)
