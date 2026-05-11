"""CSV Column Sample Generator - CSV列采样生成器

职责：
- 匹配所有 *.csv/*.tsv 下的列节点
- 将sample数据直接放入列节点的_meta.yml根级别

独立执行：
    python -m extractor.csv_column_sample ./my_data
"""
import logging

from typing import Optional, List, Any
from storage.workspace import Workspace
from extractor.modules.utils.src import file_exists, get_file_path

logger = logging.getLogger(__name__)


def generate(workspace: Workspace, sample_size: int = 10) -> None:
    """为所有CSV/TSV列生成样本"""
    logger.info("=== Generating CSV column samples ===")

    for ext, delim in [('.csv', ','), ('.tsv', '\t')]:
        csv_rows = workspace.cypher(f"MATCH (n) WHERE n.name ENDS WITH '{ext}' RETURN n")
        for csv_row in csv_rows:
            csv_ref = csv_row["n"]["name"]
            col_rows = workspace.cypher(f'MATCH (f {{name: "{csv_ref}"}})--(c:col) RETURN c')
            for col_row in col_rows:
                col_ref = col_row["c"]["name"]
                try:
                    _generate_for_column(col_ref, csv_ref, workspace, delim, sample_size)
                except Exception as e:
                    logger.warning(f"Failed to generate sample for {col_ref}: {e}")


def _generate_for_column(col_ref: str, csv_ref: str, workspace: Workspace,
                         delimiter: str, sample_size: int) -> bool:
    """为单个列生成sample数据并存入meta根级别"""
    meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": col_ref})
    meta = meta_rows[0].get("n") if meta_rows else None
    if not meta:
        return False

    if "sample" in meta:
        return False

    col_name = col_ref
    csv_meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": csv_ref})
    csv_meta = csv_meta_rows[0].get("n") if csv_meta_rows else None or {}
    csv_rel_path = csv_meta.get("path", csv_ref)
    csv_path = get_file_path(workspace, csv_rel_path)
    if not csv_path or not file_exists(workspace, csv_rel_path):
        return False

    samples = _get_samples(csv_path, col_name, delimiter, sample_size)
    if samples is None:
        return False

    workspace.cypher('MATCH (n {name: $name}) SET n += $props', params={"name": col_ref, "props": {"sample": samples}})
    logger.info(f"  Sample added: {col_ref} ({len(samples)} items)")
    return True


def _get_samples(csv_path: str, column: str, delimiter: str, sample_size: int) -> Optional[List[Any]]:
    """从CSV获取样本"""
    try:
        import csv

        with open(csv_path, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f, delimiter=delimiter)

            seen = set()
            samples = []

            for row in reader:
                value = row.get(column)
                if value and value not in seen and len(samples) < sample_size:
                    samples.append(value)
                    seen.add(value)
                if len(samples) >= sample_size:
                    break

        return samples

    except Exception as e:
        logger.debug(f"Could not get samples: {e}")
        return None
