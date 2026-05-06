"""CSV Column Sample Generator - CSV列采样生成器

职责：
- 匹配所有 *.csv/*.tsv 下的列节点
- 将sample数据直接放入列节点的_meta.yml根级别

独立执行：
    python -m extractor.csv_column_sample ./my_data
"""
import os
import logging

from typing import Optional, List, Any
from storage import Store

logger = logging.getLogger(__name__)


def generate(store: Store, sample_size: int = 10) -> None:
    """为所有CSV/TSV列生成样本"""
    logger.info("=== Generating CSV column samples ===")

    for csv_ref in store.find_nodes("*.csv"):
        for col_ref in store.find_nodes(f"{csv_ref}::*:col"):
            try:
                _generate_for_column(col_ref, csv_ref, store, ',', sample_size)
            except Exception as e:
                logger.warning(f"Failed to generate sample for {col_ref}: {e}")

    for tsv_ref in store.find_nodes("*.tsv"):
        for col_ref in store.find_nodes(f"{tsv_ref}::*:col"):
            try:
                _generate_for_column(col_ref, tsv_ref, store, '\t', sample_size)
            except Exception as e:
                logger.warning(f"Failed to generate sample for {col_ref}: {e}")


def _generate_for_column(col_ref: str, csv_ref: str, store: Store,
                         delimiter: str, sample_size: int) -> bool:
    """为单个列生成sample数据并存入meta根级别"""
    meta = store.get_meta(col_ref)
    if not meta:
        return False

    if "sample" in meta:
        return False

    col_name = col_ref
    csv_meta = store.get_meta(csv_ref) or {}
    csv_rel_path = csv_meta.get("path", csv_ref)
    csv_path = os.path.join(store.project_path, csv_rel_path)
    if not csv_path or not os.path.exists(csv_path):
        return False

    samples = _get_samples(csv_path, col_name, delimiter, sample_size)
    if samples is None:
        return False

    store.set_meta(col_ref, {"sample": samples})
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
