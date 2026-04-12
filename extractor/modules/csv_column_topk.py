"""CSV Column TopK Generator - CSV列TopK值生成器

职责：
- 匹配所有 *.csv/*.tsv 下的 *.col 节点
- 将topk数据直接放入列节点的_meta.yml根级别

独立执行：
    python -m extractor.csv_column_topk ./my_data
"""
import os
import logging

from typing import Optional, List, Dict, Any
from collections import Counter
from storage import Store

logger = logging.getLogger(__name__)


def generate(store: Store, k: int = 5) -> None:
    """为所有CSV/TSV列生成TopK值"""
    logger.info("=== Generating CSV column TopK values ===")

    for ref in store.find_nodes("*.csv::*.*.*.col"):
        try:
            _generate_for_column(ref, store, ',', k)
        except Exception as e:
            logger.warning(f"Failed to generate topk for {ref}: {e}")

    for ref in store.find_nodes("*.tsv::*.*.*.col"):
        try:
            _generate_for_column(ref, store, '\t', k)
        except Exception as e:
            logger.warning(f"Failed to generate topk for {ref}: {e}")


def _generate_for_column(ref: str, store: Store,
                         delimiter: str, k: int) -> bool:
    """为单个列生成topk数据并存入meta根级别"""
    path, entity_name = ref.split("::", 1)
    meta = store.get_meta(ref)
    if not meta:
        return False

    # 检查是否已处理
    if "topk" in meta:
        return False

    col_name = entity_name.split('.')[1]

    file_meta = store.get_meta(path)
    if not file_meta:
        return False

    rel_path = file_meta.get("path")
    csv_path = os.path.join(store.project_path, rel_path) if rel_path else None
    if not csv_path or not os.path.exists(csv_path):
        return False

    topk = _calculate_topk(csv_path, col_name, delimiter, k)
    if topk is None:
        return False

    store.set_meta(ref, {"topk": topk})
    logger.info(f"  TopK added: {ref} ({len(topk)} items)")
    return True


def _calculate_topk(csv_path: str, column: str, delimiter: str, k: int) -> Optional[List[Dict[str, Any]]]:
    """计算最常见的K个值"""
    try:
        import csv

        with open(csv_path, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f, delimiter=delimiter)

            values = []
            for row in reader:
                v = row.get(column)
                if v:
                    values.append(v)

        if not values:
            return []

        counter = Counter(values)
        total = len(values)

        topk = []
        for value, count in counter.most_common(k):
            topk.append({
                "value": value,
                "count": count,
                "percentage": round((count / total) * 100, 2)
            })

        return topk

    except Exception as e:
        logger.debug(f"Could not calculate topk: {e}")
        return None
