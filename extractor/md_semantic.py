"""Markdown Semantic Generator - Markdown语义生成器 (AI)

职责：
- 匹配所有 *.md 节点
- 读取文件内容特征
- 使用LLM生成语义描述
- 追加到_meta.yml

独立执行：
    python -m extractor.gen_markdown_semantic ./my_data
"""
import os
import logging
from extractor.utils import VFSStorage, NodeRef, get_llm

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有Markdown文件生成语义描述"""
    logger.info("=== Generating Markdown semantics ===")

    llm = get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping Markdown semantic generation")
        return

    for node in storage.find_nodes("*.md"):
        try:
            _generate_for_markdown(node, storage, llm)
        except Exception as e:
            logger.warning(f"Failed to generate semantic for {node.name}: {e}")


def _generate_for_markdown(node: NodeRef, storage: VFSStorage, llm) -> bool:
    """为单个Markdown文件生成语义描述"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    if "semantic_summary" in meta:
        return False

    line_count = meta.get("line_count", 0)
    char_count = meta.get("char_count", 0)
    source_path = meta.get("source_path", "")

    # 提取标题和预览
    first_heading = ""
    content_preview = ""
    try:
        abs_path = storage.resolve_path(rel_path) if rel_path else None
        if abs_path and os.path.exists(abs_path):
            with open(abs_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                lines = content.split('\n')
                # 找第一个标题
                for line in lines:
                    if line.startswith('#'):
                        first_heading = line.lstrip('#').strip()
                        break
                # 内容预览（前500字符）
                content_preview = content[:500]
    except:
        pass

    # 构建prompt
    prompt = f"""Analyze this Markdown document and provide a brief semantic summary (10-30 words).

Document Stats: {line_count} lines, {char_count} characters
"""
    if first_heading:
        prompt += f"First Heading: {first_heading}\n"

    if content_preview:
        prompt += f"""
Content Preview:
```
{content_preview}
```
"""

    prompt += """
Describe what this Markdown document likely contains (e.g., "API documentation with usage examples", "Project README with setup instructions").
Be concise and specific.
"""

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

    parser = argparse.ArgumentParser(description="Generate Markdown semantic summaries")
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
