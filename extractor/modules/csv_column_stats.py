"""CSV Column Stats Generator - CSV列统计生成器

职责：
- 匹配所有 *.csv 和 *.tsv 节点下的 *.col 节点
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

    for ref in store.find_nodes("*.csv::*.*.*.col"):
        try:
            _generate_for_column(ref, store, delimiter=',')
        except Exception as e:
            logger.warning(f"Failed to generate stats for {ref}: {e}")

    for ref in store.find_nodes("*.tsv::*.*.*.col"):
        try:
            _generate_for_column(ref, store, delimiter='\t')
        except Exception as e:
            logger.warning(f"Failed to generate stats for {ref}: {e}")


def _generate_for_column(ref: str, store: Store,
                         delimiter: str) -> bool:
    """为单个CSV列生成统计"""
    path, entity_name = ref.split("::", 1)
    meta = store.get_meta(ref)
    if not meta:
        return False

    # 跳过已处理的
    if "cardinality" in meta:
        return False

    # 解析实体名: [文件名].[列名].[类型].col
    col_name = entity_name.split('.')[1]

    # 获取CSV源路径
    file_meta = store.get_meta(path)
    if not file_meta:
        return False

    rel_path = file_meta.get("path")
    csv_path = os.path.join(store.project_path, rel_path) if rel_path else None
    if not csv_path or not os.path.exists(csv_path):
        return False

    # 计算统计
    stats = _calculate_stats(csv_path, col_name, delimiter)
    if stats is None:
        return False

    store.set_meta(ref, stats)
    logger.info(f"  Stats generated: {ref}")
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

        # 尝试数值统计
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
