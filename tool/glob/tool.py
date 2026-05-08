"""Glob tool — URN 语法查询，翻译为标准 Cypher。

语法：
  pattern                              名称 glob
  pattern:label:label                  名称 glob + 标签过滤
  pattern:label1|label2                OR 标签过滤
  seg1/seg2/seg3                       多跳遍历（/ 分隔）
  seg1/**/seg3                         变长遍历（** = [*1..N]）
  project::pattern:label               项目前缀（:: 分隔）

翻译规则：
  Pattern → WHERE n.name STARTS/ENDS/CONTAINS 或 fnmatch 后处理
  :Label  → :Label 节点标签
  project:: → WHERE n.project = "xxx"
  seg1/seg2 → MATCH (a:lbl)--(b:lbl) RETURN a, b
  **       → MATCH (a)-[*1..]-(b) 变长遍历
"""
import os
from fnmatch import fnmatch
from typing import Dict, List, Optional, Tuple

from tool.config import TOOL_PAGINATION
from tool.utils.formatters import format_labels, get_info


# ═══════════════════════════════════════════════════════════
#  URN 解析
# ═══════════════════════════════════════════════════════════

def _split_segment(text: str) -> Tuple[str, List[str], List[str]]:
    """将 `Pattern:Label1:Label2|Label3` 拆分为 (pattern, labels_and, labels_or)。"""
    parts = text.split(":")
    pattern = parts[0] if parts else "*"
    labels_and = []
    labels_or = []
    for label in parts[1:]:
        if "|" in label:
            labels_or.extend(label.split("|"))
        else:
            labels_and.append(label)
    return pattern or "*", labels_and, labels_or


def parse_urn(urn: str) -> Tuple[Optional[str], List[dict]]:
    """解析 URN 为 (project, segments)。

    语法：
      project::seg1/seg2/seg3
      seg1/seg2/seg3
      pattern:label
      pattern
    """
    project = None

    # 拆项目前缀
    if "::" in urn:
        idx = urn.index("::")
        project = urn[:idx]
        urn = urn[idx + 2:]

    # 拆多跳段（/ 分隔）
    raw_segments = urn.split("/")
    segments = []
    for raw in raw_segments:
        pattern, labels_and, labels_or = _split_segment(raw)
        segments.append({
            "project": None,  # 项目只在顶层设置
            "pattern": pattern,
            "labels_and": labels_and,
            "labels_or": labels_or,
        })

    # 项目属性设到第一段（用于 Cypher 生成）
    if project and segments:
        segments[0]["project"] = project

    return project, segments


# ═══════════════════════════════════════════════════════════
#  URN → 标准 Cypher 翻译
# ═══════════════════════════════════════════════════════════

def _pattern_to_cypher_name(pattern: str, var: str = "n") -> Tuple[str, Optional[str]]:
    """将 glob pattern 翻译为 Cypher WHERE 子句。

    Returns:
        (cypher_clause, post_filter_pattern)
        cypher_clause 为空字符串表示无法翻译，需后处理
    """
    if pattern == "*":
        return ("", None)

    # 无通配符 → 精确匹配
    if "*" not in pattern and "?" not in pattern and "[" not in pattern:
        return (f'{var}.name = "{pattern}"', None)

    # *suffix → ENDS WITH
    if pattern.startswith("*") and "*" not in pattern[1:] and "?" not in pattern and "[" not in pattern:
        return (f'{var}.name ENDS WITH "{pattern[1:]}"', None)

    # prefix* → STARTS WITH
    if pattern.endswith("*") and "*" not in pattern[:-1] and "?" not in pattern and "[" not in pattern:
        return (f'{var}.name STARTS WITH "{pattern[:-1]}"', None)

    # *middle* → CONTAINS
    if (pattern.startswith("*") and pattern.endswith("*")
            and "*" not in pattern[1:-1] and "?" not in pattern and "[" not in pattern):
        return (f'{var}.name CONTAINS "{pattern[1:-1]}"', None)

    # 复杂 glob → 后处理
    return ("", pattern)


def _is_varlen(seg: dict) -> bool:
    """判断段是否是 ** 变长标记。"""
    return (seg["pattern"] == "**"
            and not seg["labels_and"]
            and not seg["labels_or"])


def _build_cypher(segments: List[dict]) -> Tuple[str, List[tuple]]:
    """将段列表翻译为标准 Cypher 查询。

    ** 段不是节点，是关系修饰符，将 -- 变为 [*1..N]。
    首尾的 ** 被忽略（等同于无约束）。

    Returns:
        (cypher_query, post_filters)
        post_filters: [(var, "name", glob_pattern), ...]
    """
    post_filters = []

    # 分离节点段和变长标记
    node_segs = []
    varlen_hops = set()  # hop 索引（在 node_segs 之间的位置）
    pending_varlen = False

    for seg in segments:
        if _is_varlen(seg):
            pending_varlen = True
        else:
            node_segs.append(seg)
            if pending_varlen and len(node_segs) >= 2:
                varlen_hops.add(len(node_segs) - 2)
            pending_varlen = False

    # 全是 ** 或为空
    if not node_segs:
        return "MATCH (n) RETURN n", []

    # 单段
    if len(node_segs) == 1:
        seg = node_segs[0]
        labels_str = "".join(f":{l}" for l in seg["labels_and"])
        where_parts = []
        var = "n"

        if seg["project"]:
            where_parts.append(f'n.project = "{seg["project"]}"')

        clause, post_pat = _pattern_to_cypher_name(seg["pattern"], var)
        if clause:
            where_parts.append(clause)
        elif post_pat:
            post_filters.append((var, "name", post_pat))

        if seg["labels_or"]:
            post_filters.append((var, "labels_or", seg["labels_or"]))

        where_str = f' WHERE {" AND ".join(where_parts)}' if where_parts else ""
        cypher = f"MATCH (n{labels_str}){where_str} RETURN n"
        return cypher, post_filters

    # 多段 → MATCH (a:lbl)--(b:lbl) / (a)-[*1..6]-(b) RETURN ...
    var_names = [chr(ord('a') + i) for i in range(len(node_segs))]
    match_parts = []
    where_parts = []

    for i, seg in enumerate(node_segs):
        var = var_names[i]
        labels_str = "".join(f":{l}" for l in seg["labels_and"])

        if seg["project"]:
            where_parts.append(f'{var}.project = "{seg["project"]}"')

        clause, post_pat = _pattern_to_cypher_name(seg["pattern"], var)
        if clause:
            where_parts.append(clause)
        elif post_pat:
            post_filters.append((var, "name", post_pat))

        if seg["labels_or"]:
            post_filters.append((var, "labels_or", seg["labels_or"]))

        if i == 0:
            match_parts.append(f"({var}{labels_str})")
        else:
            hop_idx = i - 1
            if hop_idx in varlen_hops:
                match_parts.append(f"-[*1..]-({var}{labels_str})")
            else:
                match_parts.append(f"--({var}{labels_str})")

    where_str = f' WHERE {" AND ".join(where_parts)}' if where_parts else ""
    ret_vars = ", ".join(var_names)
    cypher = f"MATCH {''.join(match_parts)}{where_str} RETURN {ret_vars}"
    return cypher, post_filters


# ═══════════════════════════════════════════════════════════
#  后处理过滤
# ═══════════════════════════════════════════════════════════

def _apply_post_filters(results: list, filters: list) -> list:
    """对 Cypher 结果做 glob/label-or 后处理。"""
    if not filters:
        return results

    filtered = []
    for row in results:
        ok = True
        for var, prop, pattern in filters:
            info = row.get(var)
            if not info or not isinstance(info, dict):
                ok = False
                break

            if prop == "name":
                if not fnmatch(info.get("name", ""), pattern):
                    ok = False
                    break
            elif prop == "labels_or":
                labels = info.get("labels", [])
                if not any(l in labels for l in pattern):
                    ok = False
                    break

        if ok:
            filtered.append(row)
    return filtered


# ═══════════════════════════════════════════════════════════
#  公共 API
# ═══════════════════════════════════════════════════════════

def _get_project_name(workspace) -> str:
    ap = workspace.active_projects
    if ap:
        return ap[0]
    return "local"


def glob_command(workspace, ref: str, offset: int = 0,
                 limit: Optional[int] = None, current_cwd: str = "") -> str:
    """URN 语法查询，翻译为标准 Cypher 执行。

    Args:
        workspace: Workspace 实例
        ref: URN pattern（glob 模式、标签过滤、多跳遍历）
        offset: 起始索引
        limit: 每页最大条数
    """
    page_conf = TOOL_PAGINATION["glob"]
    if limit is None:
        limit = page_conf.default_limit
    limit = min(limit, page_conf.max_limit)

    if not workspace.pontis_exists:
        return "No .pontis directory found. Run extractor first."

    # 解析 URN → Cypher
    project, segments = parse_urn(ref)
    cypher, post_filters = _build_cypher(segments)

    # 执行 Cypher
    results = workspace.cypher(cypher)

    # 后处理（复杂 glob / label OR）
    if post_filters:
        results = _apply_post_filters(results, post_filters)

    if not results:
        return "No objects found"

    project_name = _get_project_name(workspace)

    # 格式化：取最后一个变量的信息作为主结果
    all_items = []
    for row in results:
        main_info = None
        for var_key in reversed(list(row.keys())):
            info = row.get(var_key)
            if info and isinstance(info, dict):
                main_info = info
                break
        if not main_info:
            continue

        name = main_info.get("name", "?")
        labels = main_info.get("labels", [])
        meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": name})
        meta = meta_rows[0].get("n") if meta_rows else {}
        info_str = get_info(labels, meta or {})
        label_str = format_labels(labels)
        all_items.append((name, label_str, info_str))

    if not all_items:
        return "No objects found"

    total = len(all_items)
    page = all_items[offset:offset + limit]

    if not page:
        return f"No results at offset {offset}. Total results: {total}"

    lines = []
    for name, label_str, info in page:
        lines.append(f"{name}\t{label_str}\t{info}")
    output = "\n".join(lines)

    end = offset + len(page)
    if end < total:
        output += f"\n(共 {total} 条结果，当前显示第 {offset + 1}-{end} 条。使用 offset={end} 查看后续结果)"

    return output


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m tool.glob.tool <project_name> <ref>")
        sys.exit(1)

    from storage.workspace import Workspace
    ws = Workspace(active_projects=[sys.argv[1]])
    print(glob_command(ws, sys.argv[2]))
