"""CSV Column Stats Generator - CSV列统计生成器

职责：
- 匹配所有 *.csv 和 *.tsv 节点下的列节点
- 读取CSV文件计算列统计
- 追加到_meta.yml

独立执行：
    python -m extractor.csv_column_stats ./my_data
"""
import os
import logging
from typing import Optional, Dict, Any
from storage import Store

logger = logging.getLogger(__name__)


def generate(store: Store) -> None:
    """为所有CSV/TSV文件的列生成统计"""
    logger.info("=== Generating CSV column statistics ===")

    for csv_ref in store.find_nodes("*.csv"):
        for col_ref in store.find_nodes(f"{csv_ref}::*:col"):
            try:
                _generate_for_column(col_ref, csv_ref, store, delimiter=',')
            except Exception as e:
                logger.warning(f"Failed to generate stats for {col_ref}: {e}")

    for tsv_ref in store.find_nodes("*.tsv"):
        for col_ref in store.find_nodes(f"{tsv_ref}::*:col"):
            try:
                _generate_for_column(col_ref, tsv_ref, store, delimiter='\t')
            except Exception as e:
                logger.warning(f"Failed to generate stats for {col_ref}: {e}")


def _generate_for_column(col_ref: str, csv_ref: str, store: Store,
                         delimiter: str) -> bool:
    """为单个CSV列生成统计"""
    meta = store.get_meta(col_ref)
    if not meta:
        return False

    if "cardinality" in meta:
        return False

    col_name = col_ref
    csv_meta = store.get_meta(csv_ref) or {}
    csv_rel_path = csv_meta.get("path", csv_ref)
    csv_path = os.path.join(store.project_path, csv_rel_path)
    if not csv_path or not os.path.exists(csv_path):
        return False

    stats = _calculate_stats(csv_path, col_name, delimiter)
    if stats is None:
        return False

    store.set_meta(col_ref, stats)
    logger.info(f"  Stats generated: {col_ref}")
    return True


def _calculate_stats(csv_path: str, column: str, delimiter: str) -> Optional[Dict[str, Any]]:
    """计算CSV列统计"""
    try:
        import csv

        with open(csv_path, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f, delimiter=delimiter)

            all_values = []
            null_count = 0

            for row in reader:
                value = row.get(column)
                if value is None or value == '':
                    null_count += 1
                else:
                    all_values.append(value)

        if not all_values and null_count == 0:
            return None

        total = len(all_values) + null_count
        unique_values = set(all_values)

        stats = {
            "cardinality": len(unique_values),
            "null_count": null_count,
            "null_percentage": round((null_count / total) * 100, 2) if total > 0 else 0,
        }

        numeric_values = []
        for v in all_values:
            try:
                numeric_values.append(float(v))
            except (ValueError, TypeError):
                pass

        if numeric_values:
            stats["min"] = min(numeric_values)
            stats["max"] = max(numeric_values)
            stats["mean"] = round(sum(numeric_values) / len(numeric_values), 4)

        return stats

    except Exception as e:
        logger.debug(f"Could not calculate stats: {e}")
        return None
