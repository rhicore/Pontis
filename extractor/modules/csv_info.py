"""CSV Info Generator - CSV文件信息生成器

职责：
- 匹配 *.csv/*.tsv 节点
- 添加文件级元信息（行数、列数、文件大小等）

独立执行：
    python -m extractor.csv_info ./my_data
"""
import os
import logging
from storage import Store

logger = logging.getLogger(__name__)


def generate(store: Store) -> None:
    """为所有CSV/TSV节点生成信息"""
    logger.info("=== Generating CSV info ===")

    for path in store.find_nodes("*.csv"):
        try:
            _generate_for_csv(path, store, delimiter=',')
        except Exception as e:
            logger.warning(f"Failed to generate info for {path}: {e}")

    for path in store.find_nodes("*.tsv"):
        try:
            _generate_for_csv(path, store, delimiter='\t')
        except Exception as e:
            logger.warning(f"Failed to generate info for {path}: {e}")


def _generate_for_csv(path: str, store: Store, delimiter: str) -> bool:
    """为单个CSV生成信息"""
    meta = store.get_meta(path)
    if not meta:
        return False

    if "row_count" in meta:
        return False

    rel_path = meta.get("path")
    csv_path = os.path.join(store.project_path, rel_path) if rel_path else None
    if not csv_path or not os.path.exists(csv_path):
        return False

    try:
        import csv

        with open(csv_path, 'r', encoding='utf-8', errors='ignore') as f:
            # 读取表头
            reader = csv.reader(f, delimiter=delimiter)
            headers = next(reader, None)
            column_count = len(headers) if headers else 0

            # 统计行数
            row_count = sum(1 for _ in reader)

        # 文件大小
        file_size = os.path.getsize(csv_path)

        # 更新meta
        store.set_meta(path, {
            "row_count": row_count,
            "column_count": column_count,
            "file_size": file_size,
            "delimiter": "," if delimiter == ',' else "\\t",
        })

        logger.info(f"  CSV info: {path} ({row_count} rows, {column_count} cols)")
        return True

    except Exception as e:
        logger.debug(f"Could not get CSV info: {e}")
        return False
