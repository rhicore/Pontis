"""Text Basic Generator - 文本文件实体展开器

职责：
- 匹配 *.txt 节点
- 创建 _entity/ 目录（预留）

独立执行：
    python -m extractor.text_basic ./my_data
"""
import os
import logging
from extractor.utils import VFSStorage, NodeRef

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有文本文件节点创建 _entity 目录"""
    logger.info("=== Generating Text entities ===")

    for node in storage.find_nodes("*.txt"):
        try:
            entity_rel = os.path.join(node.rel_path, "_entity")
            entity_node = NodeRef(entity_rel, storage.pontis_root)
            storage.ensure_dir(entity_node.full_path)
            logger.info(f"  Entity: {node.rel_path}")
        except Exception as e:
            logger.warning(f"Failed to create text entity for {node.name}: {e}")


def main():
    import argparse, sys
    parser = argparse.ArgumentParser(description="Generate text entities")
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
