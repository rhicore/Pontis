"""Search tool — BM25 语义检索。

基于 brief/detail 的 BM25 评分，支持自然语言查询。
"""
import math
import re
from collections import Counter
from typing import List, Optional

from tool.config import TOOL_PAGINATION
from tool.utils.formatters import format_labels, get_info


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


def _bm25_search(workspace, query: str, ref: str = "",
                 k1: float = 1.5, b: float = 0.75) -> List[tuple]:
    """BM25 检索，只搜索 brief 和 detail 字段。"""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    docs = []
    rows = workspace.cypher("MATCH (n) RETURN n")
    for row in rows:
        n = row.get("n", {})
        name = n.get("name", "")
        if not name:
            continue
        if ref and ref.strip() not in ("", "*"):
            import fnmatch
            if not fnmatch.fnmatch(name.lower(), ref.lower()):
                continue

        brief = n.get("brief", "") or ""
        detail = n.get("detail", "") or ""
        doc_text = f"{brief} {detail}"
        if not doc_text.strip():
            continue

        tokens = _tokenize(doc_text)
        if not tokens:
            continue

        labels = n.get("labels", [])
        info = get_info(labels, n)

        docs.append((name, tokens, info, labels))

    if not docs:
        return []

    N = len(docs)
    avgdl = sum(len(t) for _, t, _, _ in docs) / N
    query_token_set = set(query_tokens)

    df = Counter()
    for _, tokens, _, _ in docs:
        seen = set(tokens) & query_token_set
        for t in seen:
            df[t] += 1

    idf = {}
    for t in query_token_set:
        idf[t] = math.log((N - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5) + 1)

    results = []
    for ref_name, tokens, info, labels in docs:
        tf = Counter(tokens)
        dl = len(tokens)
        score = 0.0
        for t in query_token_set:
            if t not in tf:
                continue
            tf_val = tf[t]
            numerator = tf_val * (k1 + 1)
            denominator = tf_val + k1 * (1 - b + b * dl / avgdl)
            score += idf.get(t, 0) * numerator / denominator
        if score > 0:
            results.append((score, ref_name, info, labels))

    results.sort(key=lambda x: -x[0])
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
    if not workspace.pontis_exists:
        return f"Error: .pontis directory not found in {workspace.project_path}"

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

    project_name = _get_project_name(workspace)

    lines = []
    for score, ref_name, info, labels in page:
        label_str = format_labels(labels)
        lines.append(f"{project_name}::\t{ref_name}\t{label_str}\t{info}")
    output = '\n'.join(lines)

    end = offset + len(page)
    if end < total:
        output += f"\n(共 {total} 条结果，当前显示第 {offset + 1}-{end} 条。使用 offset={end} 查看后续结果)"

    return output
