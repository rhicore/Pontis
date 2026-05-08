"""CSV Column Stats Generator - CSV列统计生成器

职责：
- 匹配所有 *.csv 和 *.tsv 节点下的列节点
- 读取CSV文件计算列统计
- 追加到_meta.yml

独立执行：
    python -m extractor.csv_column_stats ./my_data
"""
import logging
from typing import Optional, Dict, Any
from storage.workspace import Workspace

logger = logging.getLogger(__name__)


def generate(workspace: Workspace) -> None:
    """为所有CSV/TSV文件的列生成统计"""
    logger.info("=== Generating CSV column statistics ===")

    for ext, delim in [('.csv', ','), ('.tsv', '\t')]:
        csv_rows = workspace.cypher(f"MATCH (n) WHERE n.name ENDS WITH '{ext}' RETURN n")
        for csv_row in csv_rows:
            csv_ref = csv_row["n"]["name"]
            col_rows = workspace.cypher(f'MATCH (f {{name: "{csv_ref}"}})--(c:col) RETURN c')
            for col_row in col_rows:
                col_ref = col_row["c"]["name"]
                try:
                    _generate_for_column(col_ref, csv_ref, workspace, delimiter=delim)
                except Exception as e:
                    logger.warning(f"Failed to generate stats for {col_ref}: {e}")


def _generate_for_column(col_ref: str, csv_ref: str, workspace: Workspace,
                         delimiter: str) -> bool:
    """为单个CSV列生成统计"""
    meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": col_ref})
    meta = meta_rows[0].get("n") if meta_rows else None
    if not meta:
        return False

    if "cardinality" in meta:
        return False

    col_name = col_ref
    csv_meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": csv_ref})
    csv_meta = csv_meta_rows[0].get("n") if csv_meta_rows else None or {}
    csv_rel_path = csv_meta.get("path", csv_ref)
    csv_path = workspace.resolve_data_path(csv_rel_path)
    if not csv_path or not workspace.data_exists(csv_rel_path):
        return False

    stats = _calculate_stats(csv_path, col_name, delimiter)
    if stats is None:
        return False

    workspace.cypher('MATCH (n {name: $name}) SET n += $props', params={"name": col_ref, "props": stats})
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
