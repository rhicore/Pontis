"""JSON Semantic Generator - JSON语义生成器 (AI)

职责：
- 匹配所有 *.json 节点
- 读取顶层结构信息
- 使用LLM生成语义描述
- 追加到_meta.yml

独立执行：
    python -m extractor.gen_json_semantic ./my_data
"""
import os
import logging
from extractor.utils import VFSStorage, NodeRef, get_llm

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有JSON文件生成语义描述"""
    logger.info("=== Generating JSON semantics ===")

    llm = get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping JSON semantic generation")
        return

    for node in storage.find_nodes("*.json"):
        try:
            _generate_for_json(node, storage, llm)
        except Exception as e:
            logger.warning(f"Failed to generate semantic for {node.name}: {e}")


def _generate_for_json(node: NodeRef, storage: VFSStorage, llm) -> bool:
    """为单个JSON文件生成语义描述"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    # 跳过已处理的
    if "semantic_summary" in meta:
        return False

    structure_type = meta.get("structure_type", "unknown")
    top_keys = meta.get("top_level_keys", [])
    key_count = meta.get("key_count", 0)
    array_length = meta.get("array_length", 0)

    # 构建prompt
    prompt = f"""Analyze this JSON file and provide a brief semantic summary (10-30 words).

Structure Type: {structure_type}
"""
    if top_keys:
        keys_str = ", ".join(top_keys[:10])
        prompt += f"Top Level Keys ({key_count} total): {keys_str}\n"

    if array_length:
        prompt += f"Array Length: {array_length} items\n"

    prompt += """
Describe what this JSON file likely represents (e.g., "Configuration file for API endpoints", "User profile data export").
Be concise and specific.
"""

    # 调用LLM
    try:
        summary = llm.complete(prompt, max_tokens=50)
        if summary:
            meta["semantic_summary"] = summary.strip()
            storage.write_meta(node, meta)
            logger.info(f"  Semantic generated: {node.rel_path}")
            return True
    except Exception as e:
        logger.debug(f"LLM generation failed: {e}")

    return False


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate JSON semantic summaries")
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
