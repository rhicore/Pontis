"""CSV/TSV Basic Generator - 表格文件实体展开器

职责：
- 匹配 *.csv 和 *.tsv 节点
- 读取表头
- 在 _entity/ 下创建 .col/ 子节点

独立执行：
    python -m extractor.csv_basic ./my_data
"""
import os
import logging
from extractor.utils import VFSStorage, NodeRef

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有 CSV/TSV 节点展开列实体"""
    logger.info("=== Generating CSV/TSV entities ===")

    for node in storage.find_nodes("*.csv"):
        try:
            _expand_table(node, storage, delimiter=',')
        except Exception as e:
            logger.warning(f"Failed to expand CSV {node.name}: {e}")

    for node in storage.find_nodes("*.tsv"):
        try:
            _expand_table(node, storage, delimiter='\t')
        except Exception as e:
            logger.warning(f"Failed to expand TSV {node.name}: {e}")


def _expand_table(node: NodeRef, storage: VFSStorage, delimiter: str = ',') -> None:
    """展开 CSV/TSV 为列实体

    结构：
    _entity/
        [文件名].[列名].TEXT.col/
    """
    meta = storage.read_meta(node)
    rel_path = meta.get("path") if meta else None
    file_path = storage.resolve_path(rel_path) if rel_path else None
    if not file_path or not os.path.exists(file_path):
        return

    import csv
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.reader(f, delimiter=delimiter)
        headers = next(reader, None)

    if not headers:
        return

    entity_rel = os.path.join(node.rel_path, "_entity")
    entity_node = NodeRef(entity_rel, storage.pontis_root)
    storage.ensure_dir(entity_node.full_path)

    stem = node.stem
    for col_name in headers:
        safe_col = col_name.replace("/", "_").replace("\\", "_").replace(".", "_")
        col_rel = os.path.join(entity_rel, f"{stem}.{safe_col}.TEXT.col")
        col_node = NodeRef(col_rel, storage.pontis_root)
        storage.ensure_dir(col_node.full_path)
        storage.write_meta(col_node, {"created_at": __import__('datetime').datetime.now().isoformat()})

    logger.info(f"  Entity: {node.name}/_entity/ ({len(headers)} columns)")


def main():
    import argparse, sys
    parser = argparse.ArgumentParser(description="Generate CSV/TSV entities")
    parser.add_argument('target', help='Directory with .pontis')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    pontis_path = os.path.join(os.path.abspath(args.target), ".pontis")
    if not os.path.exists(pontis_path):
        print(f"Error: No .pontis found at {pontis_path}", file=sys.stderr)
        sys.exit(1)
    generate(VFSStorage(pontis_path))
    print("Done.")


if __name__ == '__main__':
    main()
