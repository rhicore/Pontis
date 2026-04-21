"""DB Column Sketch Overlap — 基于 KMV sketch 的近似列重叠检测

单次流式扫描完成所有列的签名构建，然后快速比较。
适用于百万行级 DB，产出与 db_column_overlap 兼容。

替代 db_column_overlap（精确但 O(t²×c²×n)）。

算法：
  Phase 1  流式扫描每张表，为每列构建 KMV 签名（k 个最小哈希值）
  Phase 2  Table context 剪枝（共享非停用词 token 的表对才比较）
  Phase 3  两两比较 KMV 签名 → 近似 Jaccard
  Phase 4  创建 .overlap 实体（格式与原模块一致）

独立执行:
    python -m extractor.db_column_sketch_overlap ./my_data
"""
import os
import re
import heapq
import logging
from typing import Dict, List, Set
from collections import defaultdict
from itertools import combinations

from storage import Store

logger = logging.getLogger(__name__)

_FETCH_SIZE = 10000
_KMV_K = 256              # KMV 签名大小
_JACCARD_THRESHOLD = 0.01  # Jaccard 下限
_MAX_OVERLAPS_PER_PAIR = 3

DB_EXTENSIONS = ["*.db", "*.sqlite", "*.sqlite3", "*.duckdb"]

# 停用词（同 db_column_overlap）
STRUCTURE_STOPWORDS = {'id', 'ids', 'key', 'pk', 'fk', 'code', 'uuid', 'guid', 'index'}
COMMON_NOUNS = {'name', 'title', 'description', 'date', 'time', 'value', 'type', 'status'}
NLP_STOPWORDS = {'of', 'the', 'and', 'in', 'on', 'at', 'to', 'from', 'a', 'an'}
ALL_STOPWORDS = STRUCTURE_STOPWORDS | COMMON_NOUNS | NLP_STOPWORDS


# ==================== Public API ====================

def generate(store: Store, **_kwargs) -> None:
    """为所有数据库检测列值重叠（sketch 版）。"""
    logger.info("=== Generating sketch column overlaps ===")

    for ext in DB_EXTENSIONS:
        for path in store.find_nodes(ext):
            try:
                _generate_for_database(path, store)
            except Exception as e:
                logger.warning(f"Failed for {path}: {e}")


# ==================== Per-Database ====================

def _generate_for_database(db_ref: str, store: Store) -> None:
    db_meta = store.get_meta(db_ref)
    if not db_meta:
        return
    db_path = os.path.join(store.project_path, db_meta.get("path", ""))
    if not db_path:
        return

    col_refs = list(store.find_nodes(f"{db_ref}::*.*.*.col"))
    if len(col_refs) < 2:
        logger.info(f"  Skipping {db_ref}: only {len(col_refs)} columns")
        return

    # Phase 1: 构建列信息 + table context
    columns_info = _build_columns_info(col_refs, store)
    if not columns_info:
        return
    table_contexts = _build_table_contexts(columns_info)

    # 按 table 分组
    table_columns: Dict[str, list] = defaultdict(list)
    for c in columns_info:
        table_columns[c['table']].append(c)

    # Phase 2: 流式构建 KMV 签名
    kmv_sigs: Dict[tuple, list] = {}
    for table_name, cols in table_columns.items():
        _build_table_kmv(db_path, table_name, cols, kmv_sigs)

    # Phase 3: 两两比较
    table_pairs = list(combinations(table_contexts.keys(), 2))
    created = 0
    for t1, t2 in table_pairs:
        if table_contexts[t1].isdisjoint(table_contexts[t2]):
            continue
        overlaps = _detect_overlaps(
            table_columns[t1], table_columns[t2], kmv_sigs
        )
        for ov in overlaps:
            if _create_overlap_entity(db_ref, ov, store):
                created += 1

    if created > 0:
        logger.info(f"  Sketch overlaps: {db_ref} ({created} relations)")


# ==================== Column Info ====================

def _build_columns_info(col_refs: list, store: Store) -> List[Dict]:
    """构建列信息列表。"""
    columns_info = []
    for ref in col_refs:
        _, entity_name = ref.split("::", 1)
        col_base = entity_name.replace(".col", "")
        parts = col_base.split(".")
        if len(parts) < 3:
            continue

        table_name = parts[0]
        column_name = parts[1]
        data_type = parts[2]

        meta = store.get_meta(ref)
        cardinality = meta.get("cardinality", 0) if meta else 0

        columns_info.append({
            'table': table_name,
            'column': column_name,
            'data_type': data_type,
            'cardinality': cardinality,
            'raw_tokens': _tokenize(column_name, strict=False),
            'strict_tokens': _tokenize(column_name, strict=True),
        })

    return columns_info


def _tokenize(text: str, strict: bool = False) -> Set[str]:
    tokens = re.findall(r'[a-zA-Z][a-z]*|[0-9]+', text.lower())
    if strict:
        tokens = [t for t in tokens if t not in ALL_STOPWORDS]
    return set(tokens)


def _build_table_contexts(columns_info: List[Dict]) -> Dict[str, Set[str]]:
    table_cols = defaultdict(list)
    for col in columns_info:
        table_cols[col['table']].append(col)

    contexts = {}
    for table_name, cols in table_cols.items():
        table_tokens = _tokenize(table_name, strict=True)
        col_tokens = set()
        for col in cols:
            col_tokens.update(col['strict_tokens'])
        contexts[table_name] = table_tokens | col_tokens
    return contexts


# ==================== KMV Sketch ====================

def _build_table_kmv(db_path: str, table_name: str, cols: List[Dict],
                     signatures: Dict[tuple, list]) -> None:
    """流式扫描一张表，为所有列构建 KMV 签名。"""
    import sqlite3
    from extractor.modules._lsh_index import _hash_value

    col_names = [c['column'] for c in cols]
    heaps: Dict[str, list] = {c: [] for c in col_names}

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cols_sql = ", ".join(f'"{c}"' for c in col_names)
        cursor = conn.execute(f'SELECT {cols_sql} FROM "{table_name}"')

        while True:
            rows = cursor.fetchmany(_FETCH_SIZE)
            if not rows:
                break
            for row in rows:
                for i, val in enumerate(row):
                    if val is None:
                        continue
                    h = _hash_value(val)
                    heap = heaps[col_names[i]]
                    if len(heap) < _KMV_K:
                        heapq.heappush(heap, -h)
                    elif h < -heap[0]:
                        heapq.heapreplace(heap, -h)

        conn.close()
    except Exception as e:
        logger.debug(f"KMV scan failed for {table_name}: {e}")
        return

    # 转为 sorted list
    for c in cols:
        sig = sorted(-x for x in heaps[c['column']])
        signatures[(table_name, c['column'])] = sig


# ==================== Overlap Detection ====================

def _detect_overlaps(cols1: List[Dict], cols2: List[Dict],
                     signatures: Dict[tuple, list]) -> List[Dict]:
    """比较两表所有列对的 KMV 签名，返回 top-3 overlaps。"""
    results = []

    for c1 in cols1:
        sig1 = signatures.get((c1['table'], c1['column']))
        if not sig1:
            continue
        for c2 in cols2:
            sig2 = signatures.get((c2['table'], c2['column']))
            if not sig2:
                continue

            jaccard = _kmv_jaccard(sig1, sig2)
            if jaccard < _JACCARD_THRESHOLD:
                continue

            shared = c1['raw_tokens'] & c2['raw_tokens']
            match_type = "STRONG_MATCH" if shared else "WEAK_MATCH"
            reason = (f"Context shared | Col tokens shared: {list(shared)}"
                      if shared else "Context shared | No shared column tokens")

            card_a = c1['cardinality']
            card_b = c2['cardinality']
            card_overlap = _estimate_intersection(jaccard, card_a, card_b)

            results.append({
                'from_table': c1['table'],
                'from_column': c1['column'],
                'from_type': c1['data_type'],
                'to_table': c2['table'],
                'to_column': c2['column'],
                'to_type': c2['data_type'],
                'match_type': match_type,
                'reason': reason,
                'stats': {
                    'card_overlap': card_overlap,
                    'jaccard': round(jaccard, 4),
                    'cardinality_A': card_a,
                    'cardinality_B': card_b,
                    'coverage_A_in_B': round(card_overlap / card_a, 4) if card_a else 0,
                    'coverage_B_in_A': round(card_overlap / card_b, 4) if card_b else 0,
                },
            })

    # 排序: STRONG_MATCH 优先，card_overlap 降序
    results.sort(key=lambda x: (
        0 if x['match_type'] == "STRONG_MATCH" else 1,
        -x['stats']['card_overlap'],
    ))
    return results[:_MAX_OVERLAPS_PER_PAIR]


def _kmv_jaccard(sig1: list, sig2: list) -> float:
    """两个 KMV 签名的 Jaccard 估计（双指针）。"""
    i = j = count = 0
    n1, n2 = len(sig1), len(sig2)
    while i < n1 and j < n2:
        if sig1[i] == sig2[j]:
            count += 1
            i += 1
            j += 1
        elif sig1[i] < sig2[j]:
            i += 1
        else:
            j += 1
    union = n1 + n2 - count
    return count / union if union else 0.0


def _estimate_intersection(jaccard: float, card_a: int, card_b: int) -> int:
    """从 Jaccard 和 cardinality 估计交集大小。

    J = |A∩B| / |A∪B|, |A∪B| = |A| + |B| - |A∩B|
    → |A∩B| = J × (|A| + |B|) / (1 + J)
    """
    if card_a <= 0 or card_b <= 0:
        return 0
    return round(jaccard * (card_a + card_b) / (1 + jaccard))


# ==================== Entity Creation ====================

def _create_overlap_entity(db_ref: str, overlap: Dict, store: Store) -> bool:
    """创建 .overlap 实体（格式与 db_column_overlap 兼容）。"""
    from_table = overlap['from_table']
    from_column = overlap['from_column']
    to_table = overlap['to_table']
    to_column = overlap['to_column']

    safe_from = from_column.replace("/", "_").replace("\\", "_")
    safe_to = to_column.replace("/", "_").replace("\\", "_")

    entity_name = f"{from_table}.{safe_from}__to__{to_table}.{safe_to}.overlap"
    full_ref = f"{db_ref}::{entity_name}"

    if store.node_exists(full_ref):
        return False

    try:
        # brief/detail 含匹配信息，stats 含数值统计
        match_type = overlap['match_type']
        reason = overlap['reason']
        stats = overlap['stats']
        coverage_a = stats.get('coverage_A_in_B', 0)
        coverage_b = stats.get('coverage_B_in_A', 0)

        store.create_node(full_ref, meta={
            "stats": stats,
            "brief": f"J={stats['jaccard']} cov={coverage_a}/{coverage_b} {match_type}",
            "detail": f"{match_type}。{reason}。"
                      f"Jaccard={stats['jaccard']}，估计交集={stats['card_overlap']}。"
                      f"{from_table} 覆盖率 {coverage_a}，"
                      f"{to_table} 覆盖率 {coverage_b}。",
            "created_at": __import__('datetime').datetime.now().isoformat(),
        }, edges=[
            {
                "a": f"{db_ref}::{from_table}.{safe_from}.{overlap['from_type']}.col",
                "b": full_ref,
            },
            {
                "a": f"{db_ref}::{to_table}.{safe_to}.{overlap['to_type']}.col",
                "b": full_ref,
            },
        ])
        return True
    except Exception as e:
        logger.debug(f"Could not create overlap entity: {e}")
        return False
