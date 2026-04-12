"""CSV Column Sample Generator - CSV列采样生成器

职责：
- 匹配所有 *.csv/*.tsv 下的 *.col 节点
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

    for ref in store.find_nodes("*.csv::*.*.*.col"):
        try:
            _generate_for_column(ref, store, ',', sample_size)
        except Exception as e:
            logger.warning(f"Failed to generate sample for {ref}: {e}")

    for ref in store.find_nodes("*.tsv::*.*.*.col"):
        try:
            _generate_for_column(ref, store, '\t', sample_size)
        except Exception as e:
            logger.warning(f"Failed to generate sample for {ref}: {e}")


def _generate_for_column(ref: str, store: Store,
                         delimiter: str, sample_size: int) -> bool:
    """为单个列生成sample数据并存入meta根级别"""
    path, entity_name = ref.split("::", 1)
    meta = store.get_meta(ref)
    if not meta:
        return False

    # 检查是否已处理
    if "sample" in meta:
        return False

    # 解析实体名
    col_name = entity_name.split('.')[1]

    file_meta = store.get_meta(path)
    if not file_meta:
        return False

    rel_path = file_meta.get("path")
    csv_path = os.path.join(store.project_path, rel_path) if rel_path else None
    if not csv_path or not os.path.exists(csv_path):
        return False

    samples = _get_samples(csv_path, col_name, delimiter, sample_size)
    if samples is None:
        return False

    store.set_meta(ref, {"sample": samples})
    logger.info(f"  Sample added: {ref} ({len(samples)} items)")
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
