"""DB Column Overlap Generator - 数据库列值重叠检测生成器

职责：
- 匹配 *.db 下的所有 *.*.*.col 节点
- 使用Jaccard相似度检测列值重叠（硬性规则）
- 在_entity/ 下创建 [表名].[列名]__to__[表名].[列名].overlap 实体

检测流程（漏斗筛选模型）：
1. Context Check - 表级语义过滤（共享非停用词）
2. Value Overlap Check - 值交集硬约束
3. Column Name Check - 列名分类（STRONG_MATCH/WEAK_MATCH）
4. 详细指标计算 - Jaccard相似度、覆盖率等

独立执行：
    python -m extractor.db_column_overlap ./my_data
"""
import os
import re
import logging
from typing import List, Dict, Set, Optional
from collections import defaultdict
from itertools import combinations
from storage import Store

logger = logging.getLogger(__name__)

# 停用词分类
STRUCTURE_STOPWORDS = {'id', 'ids', 'key', 'pk', 'fk', 'code', 'uuid', 'guid', 'index'}
COMMON_NOUNS = {'name', 'title', 'description', 'date', 'time', 'value', 'type', 'status'}
NLP_STOPWORDS = {'of', 'the', 'and', 'in', 'on', 'at', 'to', 'from', 'a', 'an'}
ALL_STOPWORDS = STRUCTURE_STOPWORDS | COMMON_NOUNS | NLP_STOPWORDS

DB_EXTENSIONS = ["*.db", "*.sqlite", "*.sqlite3", "*.duckdb"]


def generate(store: Store, config=None) -> None:
    """为所有数据库检测列值重叠"""
    logger.info("=== Generating column overlaps ===")

    for ext in DB_EXTENSIONS:
        for path in store.find_nodes(ext):
            try:
                _generate_for_database(path, store)
            except Exception as e:
                logger.warning(f"Failed to generate overlaps for {path}: {e}")


def _generate_for_database(path: str, store: Store) -> bool:
    """为单个数据库检测列值重叠"""
    db_path = os.path.join(store.project_path, store.get_meta(path).get("path", ""))
    if not db_path:
        return False

    # 获取所有列节点
    col_refs = list(store.find_nodes(f"{path}::*.*.*.col"))
    if len(col_refs) < 2:
        logger.info(f"  Skipping {path}: only {len(col_refs)} columns")
        return False

    # 构建列信息
    columns_info = _build_columns_info(col_refs, store)
    if not columns_info:
        return False

    # Step 1: Context计算与表级过滤
    table_contexts = _build_table_contexts(columns_info)

    # Step 2-4: 列对检测
    created_count = 0
    table_pairs = list(combinations(table_contexts.keys(), 2))

    for table1, table2 in table_pairs:
        # Context Check - 快速剪枝
        if table_contexts[table1].isdisjoint(table_contexts[table2]):
            continue

        # 获取两表的列
        cols1 = [c for c in columns_info if c['table'] == table1]
        cols2 = [c for c in columns_info if c['table'] == table2]

        # 检测列对重叠
        overlaps = _detect_column_overlaps(db_path, cols1, cols2)

        # 创建.overlap实体
        for overlap in overlaps:
            if _create_overlap_entity(path, overlap, store):
                created_count += 1

    if created_count > 0:
        logger.info(f"  Overlaps: {path} ({created_count} relations)")
    return True


def _build_columns_info(col_refs: list, store: Store) -> List[Dict]:
    """构建列信息列表"""
    columns_info = []

    for ref in col_refs:
        path, entity_name = ref.split("::", 1)
        # 解析实体名: [表名].[列名].[类型].col
        col_base = entity_name.replace(".col", "")
        parts = col_base.split(".")
        if len(parts) < 3:
            continue

        table_name = parts[0]
        column_name = parts[1]
        data_type = parts[2]

        # 获取统计信息
        meta = store.get_meta(ref)
        cardinality = meta.get("cardinality", 0) if meta else 0

        # 计算列名tokens
        raw_tokens = _tokenize(column_name, strict=False)
        strict_tokens = _tokenize(column_name, strict=True)

        columns_info.append({
            'path': path,
            'entity_name': entity_name,
            'table': table_name,
            'column': column_name,
            'data_type': data_type,
            'cardinality': cardinality,
            'raw_tokens': raw_tokens,
            'strict_tokens': strict_tokens,
        })

    return columns_info


def _tokenize(text: str, strict: bool = False) -> Set[str]:
    """分词分析器"""
    tokens = re.findall(r'[a-zA-Z][a-z]*|[0-9]+', text.lower())

    if strict:
        tokens = [t for t in tokens if t not in ALL_STOPWORDS]

    return set(tokens)


def _build_table_contexts(columns_info: List[Dict]) -> Dict[str, Set[str]]:
    """构建表级Context"""
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


def _detect_column_overlaps(db_path: str, cols1: List[Dict], cols2: List[Dict]) -> List[Dict]:
    """检测两表列之间的重叠"""
    overlaps = []

    for col1 in cols1:
        for col2 in cols2:
            overlap_result = _calculate_overlap(db_path, col1, col2)
            if not overlap_result or overlap_result['card_overlap'] == 0:
                continue

            raw_tokens_1 = col1['raw_tokens']
            raw_tokens_2 = col2['raw_tokens']
            shared_tokens = raw_tokens_1 & raw_tokens_2

            if shared_tokens:
                match_type = "STRONG_MATCH"
                reason = f"Context shared | Col tokens shared: {list(shared_tokens)}"
            else:
                match_type = "WEAK_MATCH"
                reason = "Context shared | No shared column tokens"

            overlap_info = {
                'from_table': col1['table'],
                'from_column': col1['column'],
                'from_type': col1['data_type'],
                'to_table': col2['table'],
                'to_column': col2['column'],
                'to_type': col2['data_type'],
                'match_type': match_type,
                'reason': reason,
                'stats': {
                    'card_overlap': overlap_result['card_overlap'],
                    'jaccard': overlap_result['jaccard'],
                    'cardinality_A': col1['cardinality'],
                    'cardinality_B': col2['cardinality'],
                    'coverage_A_in_B': overlap_result['coverage_A_in_B'],
                    'coverage_B_in_A': overlap_result['coverage_B_in_A'],
                }
            }
            overlaps.append(overlap_info)

    def sort_key(x):
        match_priority = 0 if x['match_type'] == "STRONG_MATCH" else 1
        return (match_priority, -x['stats']['card_overlap'])

    overlaps.sort(key=sort_key)
    return overlaps[:3]


def _calculate_overlap(db_path: str, col1: Dict, col2: Dict) -> Optional[Dict]:
    """计算两列的值重叠情况"""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)

        cursor = conn.cursor()
        cursor.execute(
            f'SELECT DISTINCT "{col1["column"]}" FROM "{col1["table"]}" '
            f'WHERE "{col1["column"]}" IS NOT NULL'
        )
        values1 = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            f'SELECT DISTINCT "{col2["column"]}" FROM "{col2["table"]}" '
            f'WHERE "{col2["column"]}" IS NOT NULL'
        )
        values2 = {row[0] for row in cursor.fetchall()}

        conn.close()

        if values1.isdisjoint(values2):
            return None

        intersection = values1 & values2
        union = values1 | values2

        card_overlap = len(intersection)
        card_1 = len(values1)
        card_2 = len(values2)

        jaccard = card_overlap / len(union) if union else 0.0
        coverage_1_in_2 = card_overlap / card_1 if card_1 > 0 else 0.0
        coverage_2_in_1 = card_overlap / card_2 if card_2 > 0 else 0.0

        return {
            'card_overlap': card_overlap,
            'jaccard': round(jaccard, 4),
            'coverage_A_in_B': round(coverage_1_in_2, 4),
            'coverage_B_in_A': round(coverage_2_in_1, 4),
        }

    except Exception as e:
        logger.debug(f"Could not calculate overlap: {e}")
        return None


def _create_overlap_entity(path: str, overlap: Dict, store: Store) -> bool:
    """在_entity/下为重叠关系创建.overlap实体"""
    try:
        from_table = overlap['from_table']
        from_column = overlap['from_column']
        to_table = overlap['to_table']
        to_column = overlap['to_column']

        safe_from_col = from_column.replace("/", "_").replace("\\", "_")
        safe_to_col = to_column.replace("/", "_").replace("\\", "_")

        overlap_entity_name = f"{from_table}.{safe_from_col}__to__{to_table}.{safe_to_col}.overlap"
        reverse_entity_name = f"{to_table}.{safe_to_col}__to__{from_table}.{safe_from_col}.overlap"

        if store.node_exists(f"{path}::{overlap_entity_name}") or store.node_exists(f"{path}::{reverse_entity_name}"):
            return False

        overlap_meta = {
            "relation_type": "column_overlap",
            "from_table": from_table,
            "from_column": from_column,
            "from_type": overlap['from_type'],
            "to_table": to_table,
            "to_column": to_column,
            "to_type": overlap['to_type'],
            "match_type": overlap['match_type'],
            "reason": overlap['reason'],
            "stats": overlap['stats'],
            "created_at": __import__('datetime').datetime.now().isoformat(),
        }

        store.create_node(f"{path}::{overlap_entity_name}", meta=overlap_meta)

        # 添加边: from_col → overlap, to_col → overlap
        from_col_type = overlap.get('from_type', 'TEXT')
        to_col_type = overlap.get('to_type', 'TEXT')
        safe_from_col2 = from_column.replace("/", "_").replace("\\", "_")
        safe_to_col2 = to_column.replace("/", "_").replace("\\", "_")

        store.add_edges([
            {
                "a": f"{path}::{from_table}.{safe_from_col2}.{from_col_type}.col",
                "b": f"{path}::{overlap_entity_name}",
            },
            {
                "a": f"{path}::{to_table}.{safe_to_col2}.{to_col_type}.col",
                "b": f"{path}::{overlap_entity_name}",
            },
        ])

        return True

    except Exception as e:
        logger.debug(f"Could not create overlap file: {e}")
        return False
