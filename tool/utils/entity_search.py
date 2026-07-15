"""Entity query matching — vector semantic lookup with BM25 fallback.

优先使用 Neo4j detail embedding vector index；没有 embedding 配置或索引时，
回退到基于 brief/detail 的 BM25 评分。
"""
import math
import re
import threading
from collections import Counter
from typing import List, Optional

from tool.config import TOOL_PAGINATION
from tool.utils.ref_match import (
    _apply_post_filters,
    _build_cypher,
    _execute_projects,
    _format_ref_segment,
    _labels_for_output,
    _node_segments,
    normalize_project_slash_ref,
    parse_urn,
)
from tool.utils.display_ref import display_ref_for_node, node_selector
from tool.utils.formatters import format_entity_name, get_info
from tool.utils.knowledge_meta import normalize_knowledge_meta
from utils.embedding import load_embedding_config

VECTOR_INDEX_PREFIX = "pontis_detail_embedding"
_EMBED_CACHE_LOCK = threading.Lock()
_EMBED_CACHE: dict[tuple, list[float]] = {}
_EMBED_KEY_LOCKS: dict[tuple, threading.Lock] = {}
_VECTOR_INDEX_CACHE_LOCK = threading.Lock()
_VECTOR_INDEX_CACHE: dict[tuple, list[str]] = {}
BM25_TEXT_FIELDS = (
    "name",
    "brief",
    "detail",
    "official_table_description",
    "official_view_description",
    "official_column_description",
    "official_value_description",
)


# ========== Tokenizer ==========

def _tokenize(text: str) -> List[str]:
    """简单的中文/英文分词器。"""
    tokens = []
    # Treat qualified identifiers and relation arrows as boundaries.  Without
    # this, `Match.country_id->Country.id` becomes punctuation-level character
    # noise and a query such as `country` cannot retrieve the FK.
    for raw_part in re.split(r'[\s,，。.!！?？;；:：、/\\|()\[\]{}<>\-=]+', text):
        # Preserve case until CamelCase boundaries have been identified.
        # CDSCode -> CDS + Code; awayTeamId -> away + Team + Id.
        part = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", raw_part).lower()
        if not part:
            continue
        if re.match(r'^[a-z0-9_]+$', part):
            # 按下划线拆分复合词（district_id → district + id）
            for sub in part.split('_'):
                if len(sub) > 1:
                    tokens.append(sub)
                    # Lightweight English singular normalization is enough
                    # for structural words such as school/schools and
                    # player/players without introducing a stemmer runtime.
                    if len(sub) > 3 and sub.endswith("s") and not sub.endswith("ss"):
                        tokens.append(sub[:-1])
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


def _structured_search_text(node: dict) -> str:
    """Build deterministic searchable text for relation-like entities."""
    values = []
    for key in (
        "from_table", "from_column", "to_table", "to_column",
        "source_table", "source_column", "target_table", "target_column",
        "sources", "filter_evidence", "stats",
    ):
        value = node.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, dict):
            text = " ".join(f"{k} {v}" for k, v in value.items())
        elif isinstance(value, (list, tuple, set)):
            text = " ".join(str(item) for item in value)
        else:
            text = str(value)
        values.append(f"{key} {text}")
    return " ".join(values)


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
    ref = normalize_project_slash_ref(workspace, ref)
    ref = (ref or "").strip()
    if ref in ("", "*"):
        return None

    project, segments = parse_urn(ref)
    cypher, post_filters = _build_cypher(segments)
    rows = _execute_projects(workspace, cypher, project)
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
        key = (
            main_info.get("__project", ""),
            main_info.get("id")
            or main_info.get("_ref")
            or main_info.get("ref")
            or main_info.get("path")
            or (tuple(main_info.get("labels", [])), main_info.get("name", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(main_info)
    return out


def _node_key(node: dict) -> tuple:
    return (
        node.get("__project", ""),
        node.get("id")
        or node.get("_ref")
        or node.get("ref")
        or node.get("path")
        or (tuple(node.get("labels", [])), node.get("name", "")),
    )


def _candidate_key_set(workspace, ref: str) -> set[tuple] | None:
    candidates = _candidate_nodes(workspace, ref)
    if candidates is None:
        return None
    return {_node_key(node) for node in candidates}


def _simple_ref_labels(ref: str) -> tuple[set[str], set[str]] | None:
    """Return label filters for simple one-hop refs like `*:col`.

    Complex path refs still use the existing candidate materialization path.
    """
    _, segments = parse_urn(ref or "")
    if len(segments) != 1:
        return None
    seg = segments[0]
    if seg.get("pattern") not in ("", "*"):
        return None
    return set(seg.get("labels_and") or []), set(seg.get("labels_or") or [])


def _labels_pass(labels: list[str], labels_and: set[str], labels_or: set[str]) -> bool:
    label_set = set(labels or [])
    if labels_and and not labels_and.issubset(label_set):
        return False
    if labels_or and not (labels_or & label_set):
        return False
    return True


def _cached_query_embedding(embed_config, client, query: str) -> list[float]:
    key = (embed_config.provider, embed_config.model, embed_config.dimensions, query)
    with _EMBED_CACHE_LOCK:
        cached = _EMBED_CACHE.get(key)
        if cached is not None:
            return list(cached)
        key_lock = _EMBED_KEY_LOCKS.get(key)
        if key_lock is None:
            key_lock = threading.Lock()
            _EMBED_KEY_LOCKS[key] = key_lock
    with key_lock:
        with _EMBED_CACHE_LOCK:
            cached = _EMBED_CACHE.get(key)
            if cached is not None:
                return list(cached)
        vector = client.embed_one(query)
        with _EMBED_CACHE_LOCK:
            if not vector:
                _EMBED_KEY_LOCKS.pop(key, None)
                return []
            if len(_EMBED_CACHE) > 512:
                _EMBED_CACHE.clear()
            _EMBED_CACHE[key] = list(vector)
            _EMBED_KEY_LOCKS.pop(key, None)
            return vector


def _vector_search(workspace, query: str, ref: str = "", fetch_k: int = 100) -> List[tuple]:
    ref = normalize_project_slash_ref(workspace, ref)
    embed_config = load_embedding_config()
    client = embed_config.get_client()
    if not client:
        return []
    vector = _cached_query_embedding(embed_config, client, query)
    if not vector:
        return []

    simple_labels = _simple_ref_labels(ref)
    if simple_labels is None:
        try:
            allowed = _candidate_key_set(workspace, ref)
        except Exception:
            allowed = None
        labels_and: set[str] = set()
        labels_or: set[str] = set()
    else:
        allowed = None
        labels_and, labels_or = simple_labels

    project, _ = parse_urn(ref or "")
    docs = []
    seen = set()
    for candidate_project in _query_projects_for_search(workspace, project):
        for index_name in _vector_indexes(workspace, candidate_project, labels_and | labels_or):
            try:
                rows = workspace.cypher(
                    f"CALL db.index.vector.queryNodes('{index_name}', $k, $vector) "
                    "YIELD node, score "
                    "RETURN node AS n, score",
                    params={"k": int(fetch_k), "vector": vector},
                    project=candidate_project,
                )
            except Exception:
                continue
            for row in rows:
                score = float(row.get("score") or 0.0)
                if score < embed_config.min_similarity:
                    continue
                node = row.get("n") or {}
                if candidate_project:
                    node = dict(node)
                    node.setdefault("__project", candidate_project)
                key = _node_key(node)
                if key in seen:
                    continue
                if allowed is not None and key not in allowed:
                    continue
                seen.add(key)
                labels = node.get("labels", [])
                if simple_labels is not None and not _labels_pass(labels, labels_and, labels_or):
                    continue
                node_project = node.get("__project")
                node = normalize_knowledge_meta(node_project, labels, node)
                name = node.get("name", "")
                if not name:
                    continue
                info = get_info(labels, node)
                docs.append((score, name, node, info, labels, node_project))
    docs.sort(key=lambda x: (-x[0], _knowledge_priority(x[4]), x[1].lower()))
    return docs


def _index_label(index_name: str) -> str:
    return index_name[len(VECTOR_INDEX_PREFIX) + 1:] if index_name.startswith(f"{VECTOR_INDEX_PREFIX}_") else ""


def _vector_indexes(workspace, project: str | None, labels: set[str] | None = None) -> list[str]:
    cache_key = (id(workspace), project)
    with _VECTOR_INDEX_CACHE_LOCK:
        cached = _VECTOR_INDEX_CACHE.get(cache_key)
    if cached is not None:
        indexes = cached
    else:
        try:
            rows = workspace.cypher(
                "SHOW INDEXES YIELD name, type "
                f"WHERE type = 'VECTOR' AND name STARTS WITH '{VECTOR_INDEX_PREFIX}_' "
                "RETURN name",
                project=project,
            )
        except Exception:
            return []
        indexes = sorted({row.get("name") for row in rows if row.get("name")})
        with _VECTOR_INDEX_CACHE_LOCK:
            if len(_VECTOR_INDEX_CACHE) > 128:
                _VECTOR_INDEX_CACHE.clear()
            _VECTOR_INDEX_CACHE[cache_key] = indexes
    labels = set(labels or [])
    if labels:
        direct = [name for name in indexes if _index_label(name) in labels]
        if direct:
            return direct
        if "example" in labels and f"{VECTOR_INDEX_PREFIX}_knowledge" in indexes:
            return [f"{VECTOR_INDEX_PREFIX}_knowledge"]
    return indexes


def _query_projects_for_search(workspace, project: str | None) -> list[str | None]:
    if project:
        return [project]
    active = list(getattr(workspace, "active_projects", []) or [])
    return active or [None]


def _semantic_display_ref(workspace, ref: str, node_project: str | None, node_meta: dict, labels: list[str]) -> str:
    project, segments = parse_urn(ref or "")
    node_segs = _node_segments(segments)
    if not node_segs:
        node_segs = [{"labels_and": [], "labels_or": []}]

    # Semantic search previously inherited the *shape* of the query ref.  A
    # broad `*:col` therefore returned `id:col`, even when dozens of tables had
    # an id column, contradicting the public find -> meta contract.  Always use
    # the node's stable structural display path and only inherit terminal label
    # formatting from the requested scope.
    structural = display_ref_for_node(workspace, node_project, node_meta)
    parts = [part.split(":", 1)[0] for part in structural.split("/") if part]
    terminal_labels = _labels_for_output(node_segs[-1], labels)
    if parts:
        parts[-1] = _format_ref_segment(parts[-1], terminal_labels)
        local_ref = "/".join(parts)
    else:
        local_ref = _format_ref_segment(node_meta.get("name", ""), terminal_labels)

    active_projects = list(getattr(workspace, "active_projects", []) or [])
    route_project = project or (node_project if len(active_projects) > 1 else None)
    if route_project:
        return f"{route_project}::{local_ref}"
    return local_ref


def _bm25_search(workspace, query: str, ref: str = "",
                 k1: float = 1.5, b: float = 0.75) -> List[tuple]:
    """BM25 search over names, AI summaries, and official annotations."""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    docs = []
    candidates = _candidate_nodes(workspace, ref)
    if candidates is None:
        rows = _execute_projects(workspace, "MATCH (n) RETURN n", None)
        candidates = [row.get("n", {}) for row in rows]

    for n in candidates:
        node_project = n.get("__project")
        n = normalize_knowledge_meta(node_project, n.get("labels"), n)
        name = n.get("name", "")
        if not name:
            continue

        doc_text = " ".join(str(n.get(field) or "") for field in BM25_TEXT_FIELDS)
        doc_text = f"{doc_text} {_structured_search_text(n)}"
        if not doc_text.strip():
            continue

        tokens = _tokenize(doc_text)
        if not tokens:
            continue

        labels = n.get("labels", [])
        info = get_info(labels, n)

        docs.append((name, n, tokens, info, labels, node_project))

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


def search_entities_command(
    workspace,
    ref: str,
    query: str,
    offset: int = 0,
    limit: Optional[int] = None,
    current_cwd: str = ""
) -> str:
    """语义检索实体的 summary and official annotations."""
    try:
        ref = normalize_project_slash_ref(workspace, ref)
    except ValueError as exc:
        return f"Error: {exc}"
    page_conf = TOOL_PAGINATION["find"]
    if limit is None:
        limit = page_conf.default_limit
    limit = min(limit, page_conf.max_limit)

    # Fetch one stable semantic window for every page.  Previously fetch_k
    # grew with offset, so page 1 could claim "100 results" while page 20 of
    # the same query claimed "199 results" and the ranked population changed
    # between calls.
    fetch_k = page_conf.max_limit
    results = _vector_search(workspace, query, ref, fetch_k=fetch_k)
    if not results:
        results = _bm25_search(workspace, query, ref)

    if not results:
        return "No objects found"

    total = len(results)
    page = results[offset:offset + limit]

    if not page:
        return f"No results at offset {offset}. Total results: {total}"

    lines = []
    for score, ref_name, node_meta, info, labels, node_project in page:
        entity_ref = display_ref_for_node(workspace, node_project, node_meta)
        lines.append(f"{entity_ref}\t{info}")
    output = '\n'.join(lines)

    end = offset + len(page)
    window_label = f"至少 {total}" if total >= page_conf.max_limit else str(total)
    if end < total:
        output += (
            f"\n(当前语义检索窗口共 {window_label} 条，显示第 {offset + 1}-{end} 条。"
            f"使用 offset={end} 查看后续结果)"
        )
    else:
        output += (
            f"\n(当前语义检索窗口共 {window_label} 条，显示第 {offset + 1}-{end} 条，"
            "已到窗口最后一页)"
        )

    return output
