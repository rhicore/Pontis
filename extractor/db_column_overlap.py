"""DB Column Overlap Generator - 数据库列值重叠检测生成器

职责：
- 匹配 *.db 下的所有 *.*.*.col 节点
- 使用Jaccard相似度检测列值重叠（硬性规则）
- 在.db目录下创建 [表名].[列名]__to__[表名].[列名].overlap 文件

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
from typing import List, Dict, Set, Tuple, Optional
from collections import defaultdict
from itertools import combinations
from extractor.utils import VFSStorage, NodeRef, Config, load_config

logger = logging.getLogger(__name__)

# 停用词分类（与文档一致）
STRUCTURE_STOPWORDS = {'id', 'ids', 'key', 'pk', 'fk', 'code', 'uuid', 'guid', 'index'}
COMMON_NOUNS = {'name', 'title', 'description', 'date', 'time', 'value', 'type', 'status'}
NLP_STOPWORDS = {'of', 'the', 'and', 'in', 'on', 'at', 'to', 'from', 'a', 'an'}
ALL_STOPWORDS = STRUCTURE_STOPWORDS | COMMON_NOUNS | NLP_STOPWORDS


def generate(storage: VFSStorage, config: Optional[Config] = None) -> None:
    """为所有数据库检测列值重叠"""
    logger.info("=== Generating column overlaps ===")

    # 按.db分组处理
    db_nodes = storage.find_nodes("*.db")

    for db_node in db_nodes:
        try:
            _generate_for_database(db_node, storage)
        except Exception as e:
            logger.warning(f"Failed to generate overlaps for {db_node.name}: {e}")


def _generate_for_database(db_node: NodeRef, storage: VFSStorage) -> bool:
    """为单个数据库检测列值重叠"""
    # 获取DB源路径
    db_meta = storage.read_meta(db_node)
    if not db_meta:
        return False

    rel_path = db_meta.get("path")
    db_path = storage.resolve_path(rel_path) if rel_path else None
    if not db_path or not os.path.exists(db_path):
        return False

    # 获取所有列节点
    col_nodes = _get_column_nodes(db_node, storage)
    if len(col_nodes) < 2:
        logger.info(f"  Skipping {db_node.name}: only {len(col_nodes)} columns")
        return False

    # 构建列信息
    columns_info = _build_columns_info(db_path, col_nodes, storage)
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

        # 创建.overlap文件
        for overlap in overlaps:
            if _create_overlap_file(db_node, overlap, storage):
                created_count += 1

    if created_count > 0:
        logger.info(f"  Overlaps: {db_node.name} ({created_count} relations)")
    return True


def _get_column_nodes(db_node: NodeRef, storage: VFSStorage) -> List[NodeRef]:
    """获取.db下的所有列节点（新结构在 _entity/ 下）"""
    pattern = os.path.join(db_node.rel_path, "_entity", "*.*.*.col")
    return storage.find_nodes(pattern)


def _build_columns_info(db_path: str, col_nodes: List[NodeRef], storage: VFSStorage) -> List[Dict]:
    """构建列信息列表"""
    columns_info = []

    for node in col_nodes:
        # 解析节点名: [表名].[列名].[类型].col
        col_name = node.name.replace(".col", "")
        parts = col_name.split(".")
        if len(parts) < 3:
            continue

        table_name = parts[0]
        column_name = parts[1]
        data_type = parts[2]

        # 获取统计信息
        meta = storage.read_meta(node)
        cardinality = meta.get("cardinality", 0) if meta else 0

        # 计算列名tokens
        raw_tokens = _tokenize(column_name, strict=False)
        strict_tokens = _tokenize(column_name, strict=True)

        columns_info.append({
            'node': node,
            'table': table_name,
            'column': column_name,
            'data_type': data_type,
            'cardinality': cardinality,
            'raw_tokens': raw_tokens,
            'strict_tokens': strict_tokens,
        })

    return columns_info


def _tokenize(text: str, strict: bool = False) -> Set[str]:
    """
    分词分析器
    - raw_tokens: 仅分词+小写，保留所有词（用于列名比对）
    - strict_tokens: 剔除停用词（用于Context计算）
    """
    # 分词：下划线、驼峰、连字符分割
    tokens = re.findall(r'[a-zA-Z][a-z]*|[0-9]+', text.lower())

    if strict:
        tokens = [t for t in tokens if t not in ALL_STOPWORDS]

    return set(tokens)


def _build_table_contexts(columns_info: List[Dict]) -> Dict[str, Set[str]]:
    """
    构建表级Context
    Context = 表名有效词 + 所有列名有效词
    """
    table_cols = defaultdict(list)
    for col in columns_info:
        table_cols[col['table']].append(col)

    contexts = {}
    for table_name, cols in table_cols.items():
        # 表名tokens
        table_tokens = _tokenize(table_name, strict=True)

        # 所有列名strict_tokens
        col_tokens = set()
        for col in cols:
            col_tokens.update(col['strict_tokens'])

        contexts[table_name] = table_tokens | col_tokens

    return contexts


def _detect_column_overlaps(db_path: str, cols1: List[Dict], cols2: List[Dict]) -> List[Dict]:
    """
    检测两表列之间的重叠
    返回排序后的Top-K结果（每对表最多3条）
    """
    overlaps = []

    for col1 in cols1:
        for col2 in cols2:
            # Step 2: Value Overlap Check
            overlap_result = _calculate_overlap(db_path, col1, col2)
            if not overlap_result or overlap_result['card_overlap'] == 0:
                continue

            # Step 3: Column Name Check
            raw_tokens_1 = col1['raw_tokens']
            raw_tokens_2 = col2['raw_tokens']
            shared_tokens = raw_tokens_1 & raw_tokens_2

            if shared_tokens:
                match_type = "STRONG_MATCH"
                reason = f"Context shared | Col tokens shared: {list(shared_tokens)}"
            else:
                match_type = "WEAK_MATCH"
                reason = "Context shared | No shared column tokens"

            # Step 4: 构建结果
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

    # Step 4: 排序与Top-K（每对表保留最多3条）
    # 排序规则: (match_type优先级, card_overlap)
    def sort_key(x):
        match_priority = 0 if x['match_type'] == "STRONG_MATCH" else 1
        return (match_priority, -x['stats']['card_overlap'])

    overlaps.sort(key=sort_key)
    return overlaps[:3]


def _calculate_overlap(db_path: str, col1: Dict, col2: Dict) -> Optional[Dict]:
    """
    计算两列的值重叠情况
    使用集合操作优化性能
    """
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)

        # 获取列1的所有值（去重）
        cursor = conn.cursor()
        cursor.execute(
            f'SELECT DISTINCT "{col1["column"]}" FROM "{col1["table"]}" '
            f'WHERE "{col1["column"]}" IS NOT NULL'
        )
        values1 = {row[0] for row in cursor.fetchall()}

        # 获取列2的所有值（去重）
        cursor.execute(
            f'SELECT DISTINCT "{col2["column"]}" FROM "{col2["table"]}" '
            f'WHERE "{col2["column"]}" IS NOT NULL'
        )
        values2 = {row[0] for row in cursor.fetchall()}

        conn.close()

        # 快速判断：无交集
        if values1.isdisjoint(values2):
            return None

        # 计算重叠指标
        intersection = values1 & values2
        union = values1 | values2

        card_overlap = len(intersection)
        card_1 = len(values1)
        card_2 = len(values2)

        jaccard = card_overlap / len(union) if union else 0.0

        # 计算覆盖率
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


def _create_overlap_file(db_node: NodeRef, overlap: Dict, storage: VFSStorage) -> bool:
    """在.db目录下为重叠关系创建.overlap文件"""
    try:
        from_table = overlap['from_table']
        from_column = overlap['from_column']
        to_table = overlap['to_table']
        to_column = overlap['to_column']

        # 构建文件名: [表名].[列名]__to__[表名].[列名].overlap
        safe_from_col = from_column.replace("/", "_").replace("\\", "_")
        safe_to_col = to_column.replace("/", "_").replace("\\", "_")

        overlap_filename = f"{from_table}.{safe_from_col}__to__{to_table}.{safe_to_col}.overlap"
        # 新结构：放在 _entity/ 文件夹下
        overlap_rel_path = os.path.join(db_node.rel_path, "_entity", overlap_filename)
        overlap_node = NodeRef(overlap_rel_path, db_node.pontis_root)

        # 检查是否已存在
        if storage.exists(overlap_node):
            return False

        # 创建meta
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

        storage.ensure_dir(overlap_node.full_path)
        storage.write_meta(overlap_node, overlap_meta)

        # 添加边: from_col → overlap, to_col → overlap
        from_col_type = overlap.get('from_type', 'TEXT')
        to_col_type = overlap.get('to_type', 'TEXT')
        safe_from_col2 = from_column.replace("/", "_").replace("\\", "_")
        safe_to_col2 = to_column.replace("/", "_").replace("\\", "_")

        storage.add_edges([
            {
                "from": f"{db_node.name}::{from_table}.{safe_from_col2}.{from_col_type}.col",
                "type": "overlaps",
                "to": f"{db_node.name}::{overlap_filename}",
            },
            {
                "from": f"{db_node.name}::{to_table}.{safe_to_col2}.{to_col_type}.col",
                "type": "overlaps",
                "to": f"{db_node.name}::{overlap_filename}",
            },
        ])

        return True

    except Exception as e:
        logger.debug(f"Could not create overlap file: {e}")
        return False


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate DB column overlaps")
    parser.add_argument('target', help='Directory with .pontis')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    target_path = os.path.abspath(args.target)
    pontis_path = os.path.join(target_path, ".pontis")

    if not os.path.exists(pontis_path):
        print(f"Error: No .pontis found at {pontis_path}", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    storage = VFSStorage(pontis_path)
    generate(storage, config)
    print("Done.")


if __name__ == '__main__':
    main()
