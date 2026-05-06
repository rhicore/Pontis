"""Find_path tool — 图谱上的表级路径发现。

解析 FK/overlap 实体（通过 _labels）构建表级邻接，再 BFS 找最短路径。
兼容新旧实体命名（带或不带 .fk/.overlap 后缀）。
"""
import re
from collections import defaultdict, deque
from typing import Dict, List, Optional, Set, Tuple


def _get_store(obj):
    if hasattr(obj, 'get_store'):
        return obj.get_store()
    return obj


def _parse_fk_overlap_entities(store) -> Tuple[Dict[str, Set[str]], Dict[Tuple[str, str], List[str]]]:
    """解析所有 FK/overlap 实体，构建表级邻接。"""
    table_adj: Dict[str, Set[str]] = defaultdict(set)
    edge_map: Dict[Tuple[str, str], List[str]] = defaultdict(list)

    for ename, labels in store.list_all():
        is_fk = any("fk" in l.split("/") for l in labels) or ename.endswith(".fk")
        is_overlap = any("overlap" in l.split("/") for l in labels) or ename.endswith(".overlap")
        if not is_fk and not is_overlap:
            continue
        if "__to__" not in ename:
            continue

        rel_type = "fk" if is_fk else "overlap"

        # 兼容新旧命名
        base = ename.replace(".fk", "").replace(".overlap", "")
        m = re.match(r"(\w+)\.(\w+)__to__(\w+)\.(\w+)", base)
        if not m:
            continue

        src_table = m.group(1)
        dst_table = m.group(3)

        table_adj[src_table].add(dst_table)
        table_adj[dst_table].add(src_table)

        key = tuple(sorted([src_table, dst_table]))
        edge_map[key].append(ename)

    return dict(table_adj), dict(edge_map)


def _summarize_connections(entities: List[str], max_show: int = 5) -> str:
    """将同类 FK/overlap 合并摘要显示。"""
    groups: Dict[str, List[str]] = defaultdict(list)
    for e in entities:
        base = e.replace(".fk", "").replace(".overlap", "")
        m = re.match(r"(\w+)\.(\w+)__to__(\w+)\.(\w+)", base)
        if m:
            src_table, src_col, dst_table, dst_col = m.groups()
            rel = "fk" if ".fk" in e or "fk" in e else "overlap"
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
    obj,  # Workspace or Store
    from_ref: str,
    to_ref: str,
    max_depth: int = 4,
) -> str:
    """在知识图谱上查找两个表之间的最短路径。"""
    store = _get_store(obj)
    if hasattr(store, 'pontis_exists') and not store.pontis_exists:
        return f"Error: .pontis directory not found in {store.project_path}"

    def _table_name(ref: str) -> Optional[str]:
        for sep in ("::", "--"):
            if sep in ref:
                ref = ref.split(sep)[-1]
        ref = ref.replace(".table", "")
        return ref if ref else None

    src_name = _table_name(from_ref)
    dst_name = _table_name(to_ref)

    if not src_name:
        return f"Error: 无法解析起始实体：{from_ref}"
    if not dst_name:
        return f"Error: 无法解析目标实体：{to_ref}"

    if src_name == dst_name:
        return f"起始和目标是同一个表：{src_name}"

    table_adj, edge_map = _parse_fk_overlap_entities(store)

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

    hops = len(found) - 1
    lines = [f"路径（{hops} 跳）:"]

    for table in found:
        lines.append(f"  {table}")

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

    compact = " → ".join(found)
    lines.append("")
    lines.append(f"紧凑路径: {compact}")

    if hops >= 2:
        bridges = found[1:-1]
        lines.append(f"桥接表: {', '.join(bridges)}")

    return "\n".join(lines)
