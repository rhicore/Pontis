"""Search tool — BM25 语义检索。

基于 brief/detail 的 BM25 评分，支持自然语言查询。
"""
import math
import re
from collections import Counter
from typing import List, Optional

from tool.config import TOOL_PAGINATION
from tool.glob.tool import _apply_post_filters, _build_cypher, parse_urn
from tool.utils.display_ref import display_ref_for_node, node_selector
from tool.utils.formatters import format_entity_name, get_info
from tool.utils.knowledge_meta import normalize_knowledge_meta


# ========== Tokenizer ==========

def _tokenize(text: str) -> List[str]:
    """简单的中文/英文分词器。"""
    tokens = []
    for part in re.split(r'[\s,，。.!！?？;；:：、/\\|()\[\]{}]+', text.lower()):
        if not part:
            continue
        if re.match(r'^[a-z0-9_]+$', part):
            # 按下划线拆分复合词（district_id → district + id）
            for sub in part.split('_'):
                if len(sub) > 1:
                    tokens.append(sub)
            # 保留完整复合词作为额外 token
            if '_' in part and len(part) > 1:
                tokens.append(part)
        else:
            chars = [c for c in part if c.strip()]
            for i in range(len(chars)):
                tokens.append(chars[i])
                if i + 1 < len(chars):
                    tokens.append(chars[i] + chars[i + 1])
    return tokens


def _get_project_name(workspace) -> str:
    ap = workspace.active_projects
    if ap:
        return ap[0]
    return "local"


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


def _knowledge_score_factor(labels: List[str]) -> float:
    label_set = set(labels or [])
    if "knowledge" not in label_set:
        return 1.0
    if "example" in label_set:
        return 0.7
    if label_set & {"convention", "pattern", "lesson", "term"}:
        return 1.15
    return 1.0


def _candidate_nodes(workspace, ref: str) -> list[dict] | None:
    ref = (ref or "").strip()
    if ref in ("", "*"):
        return None

    project, segments = parse_urn(ref)
    cypher, post_filters = _build_cypher(segments)
    rows = workspace.cypher(cypher, project=project)
    if post_filters:
        rows = _apply_post_filters(rows, post_filters)

    out = []
    seen = set()
    for row in rows:
        main_info = None
        for var_key in reversed(list(row.keys())):
            info = row.get(var_key)
            if info and isinstance(info, dict):
                main_info = info
                break
        if not main_info:
            continue
        key = main_info.get("id") or (
            main_info.get("project", ""),
            tuple(main_info.get("labels", [])),
            main_info.get("name", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(main_info)
    return out


def _bm25_search(workspace, query: str, ref: str = "",
                 k1: float = 1.5, b: float = 0.75) -> List[tuple]:
    """BM25 检索，只搜索 brief 和 detail 字段。"""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    docs = []
    candidates = _candidate_nodes(workspace, ref)
    if candidates is None:
        rows = workspace.cypher("MATCH (n) RETURN n")
        candidates = [row.get("n", {}) for row in rows]

    for n in candidates:
        n = normalize_knowledge_meta(n.get("project"), n.get("labels"), n)
        name = n.get("name", "")
        if not name:
            continue

        brief = n.get("brief", "") or ""
        detail = n.get("detail", "") or ""
        doc_text = f"{name} {brief} {detail}"
        if not doc_text.strip():
            continue

        tokens = _tokenize(doc_text)
        if not tokens:
            continue

        labels = n.get("labels", [])
        info = get_info(labels, n)

        docs.append((name, n, tokens, info, labels, n.get("project")))

    if not docs:
        return []

    N = len(docs)
    avgdl = sum(len(t) for _, _, t, _, _, _ in docs) / N
    query_token_set = set(query_tokens)

    df = Counter()
    for _, _, tokens, _, _, _ in docs:
        seen = set(tokens) & query_token_set
        for t in seen:
            df[t] += 1

    idf = {}
    for t in query_token_set:
        idf[t] = math.log((N - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5) + 1)

    results = []
    for ref_name, node_meta, tokens, info, labels, node_project in docs:
        tf = Counter(tokens)
        dl = len(tokens)
        score = 0.0
        matched_terms = set()
        for t in query_token_set:
            if t not in tf:
                continue
            matched_terms.add(t)
            tf_val = tf[t]
            numerator = tf_val * (k1 + 1)
            denominator = tf_val + k1 * (1 - b + b * dl / avgdl)
            score += idf.get(t, 0) * numerator / denominator
        score *= _knowledge_score_factor(labels)
        if score <= 0:
            continue
        if len(query_token_set) >= 3 and len(matched_terms) < 2:
            continue
        results.append((score, ref_name, node_meta, info, labels, node_project))

    results.sort(key=lambda x: (-x[0], _knowledge_priority(x[4]), x[1].lower()))
    return results


def search_command(
    workspace,
    ref: str,
    query: str,
    offset: int = 0,
    limit: Optional[int] = None,
    current_cwd: str = ""
) -> str:
    """BM25 语义检索，搜索实体的 brief 和 detail。"""
    page_conf = TOOL_PAGINATION["search"]
    if limit is None:
        limit = page_conf.default_limit
    limit = min(limit, page_conf.max_limit)

    results = _bm25_search(workspace, query, ref)

    if not results:
        return "No objects found"

    total = len(results)
    page = results[offset:offset + limit]

    if not page:
        return f"No results at offset {offset}. Total results: {total}"

    lines = []
    for score, ref_name, node_meta, info, labels, node_project in page:
        display_ref = display_ref_for_node(workspace, node_project, node_meta)
        entity_name = format_entity_name(display_ref, labels)
        project_name = node_project or _get_project_name(workspace)
        lines.append(f"{project_name}::\t{entity_name}\t{info}")
    output = '\n'.join(lines)

    end = offset + len(page)
    if end < total:
        output += f"\n(共 {total} 条结果，当前显示第 {offset + 1}-{end} 条。使用 offset={end} 查看后续结果)"

    return output
