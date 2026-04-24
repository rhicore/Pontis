"""Search tool — BM25 检索实体的 brief 和 detail 字段。"""
import math
import re
import os
from collections import Counter
from typing import List, Optional

from tool_use.utils.formatters import get_type_config, format_info_from_meta, get_file_type_from_name
from tool_use.config import TOOL_PAGINATION


def _tokenize(text: str) -> List[str]:
    """中英文分词：英文按空白拆分，中文按字符拆分，过滤短词。"""
    tokens = []
    for part in re.split(r'[\s,，。.!！?？;；:：、/\\|()\[\]{}]+', text.lower()):
        if not part:
            continue
        # 纯 ASCII 词 → 直接加入
        if re.match(r'^[a-z0-9_]+$', part):
            if len(part) > 1:
                tokens.append(part)
        else:
            # 含 CJK → 按字符 bigram 拆分
            chars = [c for c in part if c.strip()]
            for i in range(len(chars)):
                tokens.append(chars[i])
                if i + 1 < len(chars):
                    tokens.append(chars[i] + chars[i + 1])
    return tokens


def _bm25_search(store, query: str, path_pattern: str = "",
                 k1: float = 1.5, b: float = 0.75) -> List[tuple]:
    """BM25 检索，只搜索 brief 和 detail 字段。"""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    # 收集所有文档
    docs = []  # [(ref, tokens, info_str)]
    for ref, meta in store.walk_metas():
        # path_pattern 过滤
        if path_pattern and path_pattern.strip() not in ("", "*"):
            import fnmatch
            if not fnmatch.fnmatch(ref.lower(), path_pattern.lower()):
                continue

        brief = meta.get("brief", "") or ""
        detail = meta.get("detail", "") or ""
        doc_text = f"{brief} {detail}"
        if not doc_text.strip():
            continue

        tokens = _tokenize(doc_text)
        if not tokens:
            continue

        # 显示信息
        name = meta.get('name', os.path.basename(ref))
        node_type = meta.get('type', '')
        file_type = get_file_type_from_name(name, node_type)
        config = get_type_config(file_type)
        info = format_info_from_meta(meta, config)
        display_info = f"{info}, {brief}" if brief and info != "-" else (brief or info)

        docs.append((ref, tokens, display_info))

    if not docs:
        return []

    # BM25 计算
    N = len(docs)
    avgdl = sum(len(t) for _, t, _ in docs) / N
    query_token_set = set(query_tokens)

    # IDF
    df = Counter()
    for _, tokens, _ in docs:
        seen = set(tokens) & query_token_set
        for t in seen:
            df[t] += 1

    idf = {}
    for t in query_token_set:
        idf[t] = math.log((N - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5) + 1)

    results = []
    for ref, tokens, info in docs:
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
            results.append((score, ref, info))

    results.sort(key=lambda x: -x[0])
    return results


def search_command(
    store,
    path_pattern: str,
    query: str,
    offset: int = 0,
    limit: Optional[int] = None,
    current_cwd: str = ""
) -> str:
    """BM25 语义检索，搜索实体的 brief 和 detail。

    Args:
        store: Store 实例
        path_pattern: glob 模式限定搜索范围
        query: 搜索查询
        offset: 起始位置
        limit: 每页最大条数
    """
    if not store.pontis_exists:
        return f"Error: .pontis directory not found in {store.project_path}"

    page_conf = TOOL_PAGINATION["search"]
    if limit is None:
        limit = page_conf.default_limit
    limit = min(limit, page_conf.max_limit)

    results = _bm25_search(store, query, path_pattern)

    if not results:
        return "No objects found"

    total = len(results)
    page = results[offset:offset + limit]

    if not page:
        return f"No results at offset {offset}. Total results: {total}"

    page_refs = [ref for _, ref, _ in page]
    page_infos = [info for _, _, info in page]

    # 检测 ref 公共前缀缩写
    prefix = _common_ref_prefix(page_refs)
    if prefix:
        header = f"[{prefix}]\n"
    else:
        header = ""
        prefix = ""

    lines = []
    for ref, info in zip(page_refs, page_infos):
        display = ref[len(prefix):] if prefix else ref
        lines.append(f"{display} | {info}")
    output = header + '\n'.join(lines)

    end = offset + len(page)
    if end < total:
        output += f"\n(共 {total} 条结果，当前显示第 {offset + 1}-{end} 条。使用 offset={end} 查看后续结果)"

    return output


def _common_ref_prefix(refs: list) -> str:
    """找所有 ref 的最长公共前缀，停在 :: 边界上。"""
    if not refs:
        return ""
    prefix = refs[0]
    for s in refs[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
        if not prefix:
            return ""
    if "::" not in prefix:
        return ""
    last_sep = prefix.rfind("::")
    return prefix[:last_sep + 2]
