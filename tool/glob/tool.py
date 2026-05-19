"""Glob tool — URN 语法查询，翻译为标准 Cypher。

语法：
  pattern                              名称 glob
  pattern:label:label                  名称 glob + 标签过滤
  pattern:label1|label2                OR 标签过滤
  seg1/seg2/seg3                       多跳遍历（/ 分隔）
  seg1/**/seg3                         变长遍历（** = [*1..N]）
  project::pattern:label               项目路由前缀（:: 分隔）

翻译规则：
  Pattern → WHERE n.name STARTS/ENDS/CONTAINS 或 fnmatch 后处理
  :Label  → :Label 节点标签
  project:: → workspace.cypher(..., project=project)
  seg1/seg2 → MATCH (a:lbl)--(b:lbl) RETURN a, b
  **       → MATCH (a)-[*1..]-(b) 变长遍历
"""
import json
from fnmatch import fnmatch
from typing import Dict, List, Optional, Tuple

from storage.query_inspector import cypher_label_clause, is_valid_label
from tool.config import TOOL_PAGINATION
from tool.utils.formatters import format_entity_name, get_info
from tool.utils.display_ref import display_ref_for_node
from tool.utils.knowledge_meta import normalize_knowledge_meta
from tool.utils.resolve import selector_match_pattern


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
            "pattern": pattern,
            "labels_and": labels_and,
            "labels_or": labels_or,
        })

    return project, segments


def normalize_project_slash_ref(workspace, ref: str) -> str:
    """Accept common `project/...` typo as `project::...` for active projects.

    `project::` is the route for the whole query. Models sometimes write
    `project/*:table`, which otherwise means "find an entity named project and
    traverse from it". Normalizing only active project names keeps normal file
    paths unchanged.
    """
    text = (ref or "").strip()
    if "::" in text or "/" not in text:
        return text
    first, rest = text.split("/", 1)
    active = set(getattr(workspace, "active_projects", []) or [])
    if first in active and rest:
        return f"{first}::{rest}"
    return text


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
        return (f"{var}.name = {json.dumps(pattern)}", None)

    # *suffix → ENDS WITH
    if pattern.startswith("*") and "*" not in pattern[1:] and "?" not in pattern and "[" not in pattern:
        return (f"{var}.name ENDS WITH {json.dumps(pattern[1:])}", None)

    # prefix* → STARTS WITH
    if pattern.endswith("*") and "*" not in pattern[:-1] and "?" not in pattern and "[" not in pattern:
        return (f"{var}.name STARTS WITH {json.dumps(pattern[:-1])}", None)

    # *middle* → CONTAINS
    if (pattern.startswith("*") and pattern.endswith("*")
            and "*" not in pattern[1:-1] and "?" not in pattern and "[" not in pattern):
        return (f"{var}.name CONTAINS {json.dumps(pattern[1:-1])}", None)

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
    for seg in segments:
        labels = list(seg.get("labels_and", [])) + list(seg.get("labels_or", []))
        if any(not is_valid_label(label) for label in labels):
            return "MATCH (n) WHERE false RETURN n", []

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
        labels_str = cypher_label_clause(seg["labels_and"])
        where_parts = []
        var = "n"

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
        labels_str = cypher_label_clause(seg["labels_and"])

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


def _query_projects(workspace, project: str | None) -> list[str | None]:
    if project:
        return [project]
    active = list(getattr(workspace, "active_projects", []) or [])
    return active or [None]


def _tag_row_project(row: dict, project: str | None) -> dict:
    if not project:
        return row
    tagged = {}
    for key, value in row.items():
        if isinstance(value, dict):
            copy = dict(value)
            copy.setdefault("__project", project)
            tagged[key] = copy
        else:
            tagged[key] = value
    return tagged


def _execute_projects(workspace, cypher: str, project: str | None) -> list[dict]:
    rows = []
    for candidate in _query_projects(workspace, project):
        project_rows = workspace.cypher(cypher, project=candidate)
        rows.extend(_tag_row_project(row, candidate) for row in project_rows)
    return rows


def _knowledge_priority(labels: List[str]) -> int:
    label_set = set(labels or [])
    if "knowledge" not in label_set:
        return 50
    order = {
        "convention": 0,
        "pattern": 1,
        "lesson": 2,
        "term": 3,
        "example": 9,
    }
    for label, priority in order.items():
        if label in label_set:
            return priority
    return 5


def glob_command(workspace, ref: str, offset: int = 0,
                 limit: Optional[int] = None, current_cwd: str = "") -> str:
    """URN 语法查询，翻译为标准 Cypher 执行。

    Args:
        workspace: Workspace 实例
        ref: URN pattern（glob 模式、标签过滤、多跳遍历）
        offset: 起始索引
        limit: 每页最大条数
    """
    ref = normalize_project_slash_ref(workspace, ref)
    page_conf = TOOL_PAGINATION["glob"]
    if limit is None:
        limit = page_conf.default_limit
        # bird 知识总表常作为索引页使用，默认多展示一些，减少翻页和漏看。
        if ref == "bird::*:knowledge":
            limit = max(limit, 100)
    limit = min(limit, page_conf.max_limit)

    # 解析 URN → Cypher
    project, segments = parse_urn(ref)
    cypher, post_filters = _build_cypher(segments)

    # 执行 Cypher。project:: 是整条 glob 的路由，不是节点属性过滤。
    results = _execute_projects(workspace, cypher, project)

    # 后处理（复杂 glob / label OR）
    if post_filters:
        results = _apply_post_filters(results, post_filters)

    if not results:
        return "No objects found"

    # 格式化：取最后一个变量的信息作为主结果
    all_items = []
    for row in results:
        main_info = None
        main_var = None
        for var_key in reversed(list(row.keys())):
            info = row.get(var_key)
            if info and isinstance(info, dict):
                main_info = info
                main_var = var_key
                break
        if not main_info:
            continue
        node_project = main_info.get("__project") or project
        main_info = normalize_knowledge_meta(node_project, main_info.get("labels"), main_info)

        name = display_ref_for_node(workspace, node_project, main_info, row=row, main_var=main_var)
        labels = main_info.get("labels", [])
        info_str = get_info(labels, main_info or {})
        entity_name = format_entity_name(name, labels)
        all_items.append((_knowledge_priority(labels), entity_name.lower(), node_project, entity_name, info_str))

    if not all_items:
        return "No objects found"

    all_items.sort(key=lambda x: (x[0], x[1]))
    total = len(all_items)
    page = all_items[offset:offset + limit]

    if not page:
        return f"No results at offset {offset}. Total results: {total}"

    lines = []
    show_project = project is None and len(_query_projects(workspace, project)) > 1
    for _, _, node_project, entity_name, info in page:
        prefix = f"{node_project}::\t" if show_project and node_project else ""
        lines.append(f"{prefix}{entity_name}\t{info}")
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
