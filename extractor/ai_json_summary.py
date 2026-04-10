"""AI JSON Summary - JSON 文件 AI 总结生成器

职责：
- 匹配所有 *.json 节点
- 读取结构信息
- 使用 LLM 生成 detail 和 brief
- 追加到 _meta.yml

独立执行：
    python -m extractor.ai_json_summary ./my_data
"""
import os
import logging
from extractor.utils import VFSStorage, NodeRef, get_llm
from extractor.ai_utils import generate_detail_and_brief

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    logger.info("=== AI: JSON summary ===")

    llm = get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping AI summary")
        return

    for node in storage.find_nodes("*.json"):
        try:
            _generate_for_json(node, storage, llm)
        except Exception as e:
            logger.warning(f"Failed for {node.name}: {e}")


def _generate_for_json(node: NodeRef, storage: VFSStorage, llm) -> bool:
    meta = storage.read_meta(node)
    if not meta:
        return False

    if "detail" in meta and "brief" in meta:
        return False

    structure_type = meta.get("structure_type", "unknown")
    top_keys = meta.get("top_level_keys", [])
    key_count = meta.get("key_count", 0)
    array_length = meta.get("array_length", 0)

    prompt = f"""Analyze this JSON file and generate TWO summaries.

Structure Type: {structure_type}
"""
    if top_keys:
        keys_str = ", ".join(top_keys[:10])
        prompt += f"Top Level Keys ({key_count} total): {keys_str}\n"
    if array_length:
        prompt += f"Array Length: {array_length} items\n"

    prompt += """
Generate a comprehensive description of what this JSON file represents. Be as detailed as possible — describe the data structure, purpose, each key's role, data patterns, and anything notable.

IMPORTANT rules:
- Do NOT mention specific counts (exact key counts, exact array lengths, exact line counts). These numbers change frequently and make the summary outdated quickly. Use qualitative language instead (e.g., "a handful of top-level keys", "a large array of records").
- Focus on the structure, purpose, and semantics of the data — things that remain stable over time.
- Output ONLY plain text, no labels, no markdown formatting.\
"""

    try:
        detail, brief = generate_detail_and_brief(llm, prompt, max_tokens=100)
        if detail:
            meta["detail"] = detail
        if brief:
            meta["brief"] = brief

        if detail or brief:
            storage.write_meta(node, meta)
            logger.info(f"  AI summary: {node.rel_path}")
            return True
    except Exception as e:
        logger.debug(f"LLM failed: {e}")

    return False


def main():
    import argparse, sys
    parser = argparse.ArgumentParser(description="AI JSON summary")
    parser.add_argument('target', help='Directory with .pontis')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    pontis_path = os.path.join(os.path.abspath(args.target), ".pontis")
    if not os.path.exists(pontis_path):
        print(f"Error: No .pontis found", file=sys.stderr)
        sys.exit(1)
    generate(VFSStorage(pontis_path))
    print("Done.")


if __name__ == '__main__':
    main()
