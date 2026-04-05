"""CSV Info Generator - CSV文件信息生成器

职责：
- 匹配 *.csv/*.tsv 节点
- 添加文件级元信息（行数、列数、文件大小等）

独立执行：
    python -m extractor.csv_info ./my_data
"""
import os
import logging
from extractor.utils import VFSStorage, NodeRef

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有CSV/TSV节点生成信息"""
    logger.info("=== Generating CSV info ===")

    for node in storage.find_nodes("*.csv"):
        try:
            _generate_for_csv(node, storage, delimiter=',')
        except Exception as e:
            logger.warning(f"Failed to generate info for {node.name}: {e}")

    for node in storage.find_nodes("*.tsv"):
        try:
            _generate_for_csv(node, storage, delimiter='\t')
        except Exception as e:
            logger.warning(f"Failed to generate info for {node.name}: {e}")


def _generate_for_csv(node: NodeRef, storage: VFSStorage, delimiter: str) -> bool:
    """为单个CSV生成信息"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    if "row_count" in meta:
        return False

    rel_path = meta.get("path")
    csv_path = storage.resolve_path(rel_path) if rel_path else None
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
        meta.update({
            "row_count": row_count,
            "column_count": column_count,
            "file_size": file_size,
            "delimiter": "," if delimiter == ',' else "\\t",
        })
        storage.write_meta(node, meta)

        logger.info(f"  CSV info: {node.rel_path} ({row_count} rows, {column_count} cols)")
        return True

    except Exception as e:
        logger.debug(f"Could not get CSV info: {e}")
        return False


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate CSV info")
    parser.add_argument('target', help='Directory with .pontis')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    target_path = os.path.abspath(args.target)
    pontis_path = os.path.join(target_path, ".pontis")

    if not os.path.exists(pontis_path):
        print(f"Error: No .pontis found at {pontis_path}", file=sys.stderr)
        sys.exit(1)

    storage = VFSStorage(pontis_path)
    generate(storage)
    print("Done.")


if __name__ == '__main__':
    main()
