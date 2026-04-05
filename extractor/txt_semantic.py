"""Text Semantic Generator - 文本语义生成器 (AI)

职责：
- 匹配 *.txt 节点
- 读取内容特征
- 使用LLM生成语义描述
- 追加到_meta.yml

独立执行：
    python -m extractor.txt_semantic ./my_data
"""
import os
import logging
from extractor.utils import VFSStorage, NodeRef, get_llm

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有Text文件生成语义描述"""
    logger.info("=== Generating Text semantics ===")

    llm = get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping Text semantic generation")
        return

    for node in storage.find_nodes("*.txt"):
        try:
            _generate_for_txt(node, storage, llm)
        except Exception as e:
            logger.warning(f"Failed to generate semantic for {node.name}: {e}")


def _generate_for_txt(node: NodeRef, storage: VFSStorage, llm) -> bool:
    """为单个文本文件生成语义描述"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    if "semantic_summary" in meta:
        return False

    line_count = meta.get("line_count", 0)
    paragraph_count = meta.get("paragraph_count", 0)
    source_path = meta.get("source_path", "")

    # 读取内容预览
    content_preview = ""
    try:
        abs_path = storage.resolve_path(rel_path) if rel_path else None
        if abs_path and os.path.exists(abs_path):
            with open(abs_path, 'r', encoding='utf-8', errors='ignore') as f:
                content_preview = f.read(2000)  # 前2000字符
    except:
        pass

    # 构建prompt
    prompt = f"""Analyze this text document and provide a brief semantic summary (10-30 words).

Document Stats: {line_count} lines, {paragraph_count} paragraphs

Content Preview:
```
{content_preview[:1000]}
```

Describe what this text document likely contains (e.g., "Log file with error messages", "Meeting notes and action items").
Be concise and specific.
"""

    try:
        summary = llm.complete(prompt, max_tokens=50)
        if summary:
            meta["semantic_summary"] = summary.strip()
            storage.write_meta(node, meta)
            logger.info(f"  Semantic: {node.rel_path}")
            return True
    except Exception as e:
        logger.debug(f"LLM generation failed: {e}")

    return False


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate Text semantic summaries")
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
