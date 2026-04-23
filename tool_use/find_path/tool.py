"""Find_path tool — 图谱上的表级路径发现。

不依赖 _adjacent 图（FK 实体不连接目标表），
而是解析 FK/overlap 实体名构建表级邻接，再 BFS 找最短路径。
"""
import re
from collections import defaultdict, deque
from typing import Dict, List, Optional, Set, Tuple


def _parse_fk_overlap_entities(store) -> Tuple[Dict[str, Set[str]], Dict[Tuple[str, str], List[str]]]:
    """解析所有 FK/overlap 实体，构建表级邻接。

    Returns:
        table_adj: {table_name: {connected_table_names}}
        edge_map: {(t1, t2): [entity_names]}  (t1 < t2 排序)
    """
    store._ensure_index()

    table_adj: Dict[str, Set[str]] = defaultdict(set)
    edge_map: Dict[Tuple[str, str], List[str]] = defaultdict(list)

    for eid, ref in store._id_index.items():
        if "::" not in ref:
            continue
        entity = ref.split("::", 1)[1]

        if ".fk" not in entity and ".overlap" not in entity:
            continue

        # Match.country_id__to__Country.id.fk
        # Player.id__to__Player_Attributes.id.overlap
        m = re.match(r"(\w+)\.(\w+)__to__(\w+)\.(\w+)\.(fk|overlap)", entity)
        if not m:
            continue

        src_table = m.group(1)
        dst_table = m.group(3)
        rel_type = m.group(5)

        table_adj[src_table].add(dst_table)
        table_adj[dst_table].add(src_table)

        key = tuple(sorted([src_table, dst_table]))
        edge_map[key].append(entity)

    return dict(table_adj), dict(edge_map)


def _extract_file_prefix(ref: str) -> str:
    """从 ref 中提取文件前缀（如 'european_football_2.sqlite::'）。"""
    if "::" in ref:
        return ref.split("::")[0] + "::"
    return ""


def _table_ref(table_name: str, file_prefix: str) -> str:
    """构造完整的 table ref。"""
    return f"{file_prefix}{table_name}.table"


def _summarize_connections(entities: List[str], max_show: int = 5) -> str:
    """将同类 FK/overlap 合并摘要显示。"""
    # 按关系类型和目标表分组
    groups: Dict[str, List[str]] = defaultdict(list)
    for e in entities:
        m = re.match(r"(\w+)\.(\w+)__to__(\w+)\.(\w+)\.(fk|overlap)", e)
        if m:
            src_table, src_col, dst_table, dst_col, rel = m.groups()
            key = f"{rel} → {dst_table}.{dst_col}"
            groups[key].append(f"{src_table}.{src_col}")

    lines = []
    for rel_target, src_cols in groups.items():
        if len(src_cols) == 1:
            lines.append(f"  {src_cols[0]} ({rel_target})")
        else:
            lines.append(f"  {src_cols[0]} 等 {len(src_cols)} 列 ({rel_target})")
            for c in src_cols[1:max_show]:
                lines.append(f"    {c}")
            if len(src_cols) > max_show:
                lines.append(f"    ... +{len(src_cols) - max_show} more")

    return "\n".join(lines)


def find_path_command(
    store,
    from_ref: str,
    to_ref: str,
    max_depth: int = 4,
) -> str:
    """在知识图谱上查找两个表之间的最短路径。

    Args:
        store: Store 实例
        from_ref: 起始实体 ref（通常是 .table）
        to_ref: 目标实体 ref（通常是 .table）
        max_depth: 最大搜索深度（表跳数），默认 4
    """
    if not store.pontis_exists:
        return f"Error: .pontis directory not found in {store.project_path}"

    # 解析 ref 提取表名
    def _table_name(ref: str) -> Optional[str]:
        if "::" not in ref:
            return None
        entity = ref.split("::", 1)[1]
        m = re.match(r"(\w+)\.table", entity)
        return m.group(1) if m else None

    src_name = _table_name(from_ref)
    dst_name = _table_name(to_ref)

    if not src_name:
        return f"Error: 无法解析起始实体：{from_ref}（需要 *.table 格式）"
    if not dst_name:
        return f"Error: 无法解析目标实体：{to_ref}（需要 *.table 格式）"

    if src_name == dst_name:
        return f"起始和目标是同一个表：{src_name}"

    # 从输入 ref 提取文件前缀
    file_prefix = _extract_file_prefix(from_ref) or _extract_file_prefix(to_ref)

    # 构建表级邻接
    table_adj, edge_map = _parse_fk_overlap_entities(store)

    # BFS
    visited = {src_name}
    queue = deque([(src_name, [src_name])])
    found = None

    while queue:
        current, path = queue.popleft()
        if len(path) - 1 >= max_depth:
            continue

        for neighbor in table_adj.get(current, set()):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            new_path = path + [neighbor]

            if neighbor == dst_name:
                found = new_path
                break

            queue.append((neighbor, new_path))

        if found:
            break

    if not found:
        tables = sorted(table_adj.keys())
        return (
            f"未找到从 {src_name} 到 {dst_name} 的路径（深度 ≤ {max_depth}）\n"
            f"图谱中存在的表: {', '.join(tables)}"
        )

    # 格式化输出
    hops = len(found) - 1
    lines = [f"路径（{hops} 跳）:"]

    for table in found:
        lines.append(f"  {_table_ref(table, file_prefix)}")

    # 详细的连接信息
    if hops > 0:
        lines.append("")
        lines.append("连接详情:")
        for i in range(hops):
            t1, t2 = found[i], found[i + 1]
            key = tuple(sorted([t1, t2]))
            entities = edge_map.get(key, [])
            lines.append(f"")
            lines.append(f"{t1} ↔ {t2}（{len(entities)} 个关系）:")
            lines.append(_summarize_connections(entities))

    # 紧凑格式
    compact = " → ".join(found)
    lines.append("")
    lines.append(f"紧凑路径: {compact}")

    # 桥接表提示
    if hops >= 2:
        bridges = found[1:-1]
        lines.append(f"桥接表: {', '.join(bridges)}")

    return "\n".join(lines)
