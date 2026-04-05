"""Text Chunk Generator - 文本分片生成器

职责：
- 匹配所有 *.txt 节点
- 读取文本内容，按段落分片
- 为每个分片创建 .chunk/ 文件夹
- 写入 _raw 文件（JSON格式的文本内容）

独立执行：
    python -m extractor.txt_chunk ./my_data
"""
import os
import logging
from typing import List
from extractor.utils import VFSStorage, NodeRef

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有Text文件生成chunk"""
    logger.info("=== Generating Text chunks ===")

    for node in storage.find_nodes("*.txt"):
        try:
            _generate_for_text(node, storage)
        except Exception as e:
            logger.warning(f"Failed to generate chunks for {node.name}: {e}")


def _generate_for_text(txt_node: NodeRef, storage: VFSStorage) -> bool:
    """为单个文本文件创建chunks"""
    meta = storage.read_meta(txt_node)
    if not meta:
        return False

    rel_path = meta.get("path")
    txt_path = storage.resolve_path(rel_path) if rel_path else None
    if not txt_path or not os.path.exists(txt_path):
        return False

    # 读取文本内容
    try:
        with open(txt_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        logger.debug(f"Could not read text file: {e}")
        return False

    # 按段落分割（空行分隔）
    paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]

    if not paragraphs:
        return False

    # 为每个段落创建chunk
    created = 0
    for i, para in enumerate(paragraphs):
        chunk_name = f"paragraph_{i+1}.chunk"
        chunk_rel_path = os.path.join(txt_node.rel_path, chunk_name)
        chunk_node = NodeRef(chunk_rel_path, txt_node.pontis_root)

        # 跳过已存在的
        if storage.exists(chunk_node):
            continue

        # 创建chunk文件夹
        storage.ensure_dir(chunk_node.full_path)

        # 写入文本内容到_raw（JSON格式）
        storage.write_raw(chunk_node, {"content": para})

        # 写入meta（仅保留必要的字段）
        chunk_meta = {
            "chunk_index": i,
            "char_count": len(para),
            "created_at": __import__('datetime').datetime.now().isoformat(),
        }
        storage.write_meta(chunk_node, chunk_meta)
        created += 1

    logger.info(f"  Created {created} chunks: {txt_node.name}")
    return True


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate Text chunks")
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
