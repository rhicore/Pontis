"""PDF Semantic Generator - PDF语义生成器 (AI)

职责：
- 匹配 *.pdf 节点
- 读取元信息和文本预览
- 使用LLM生成语义描述
- 追加到_meta.yml

独立执行：
    python -m extractor.pdf_semantic ./my_data
"""
import os
import logging
from extractor.utils import VFSStorage, NodeRef, get_llm

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有PDF文件生成语义描述"""
    logger.info("=== Generating PDF semantics ===")

    llm = get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping PDF semantic generation")
        return

    for node in storage.find_nodes("*.pdf"):
        try:
            _generate_for_pdf(node, storage, llm)
        except Exception as e:
            logger.warning(f"Failed to generate semantic for {node.name}: {e}")


def _generate_for_pdf(node: NodeRef, storage: VFSStorage, llm) -> bool:
    """为单个PDF文件生成语义描述"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    if "semantic_summary" in meta:
        return False

    page_count = meta.get("page_count", 0)
    title = meta.get("title", "")
    author = meta.get("author", "")
    sample_text = meta.get("sample_text", "")

    # 构建prompt
    prompt = f"""Analyze this PDF document and provide a brief semantic summary (10-30 words).

Document Info:
- Pages: {page_count}
- Title: {title or "N/A"}
- Author: {author or "N/A"}

Text Preview (first 3 pages):
```
{sample_text[:1500]}
```

Describe what this PDF document likely contains (e.g., "Research paper on machine learning", "Product specification sheet").
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

    parser = argparse.ArgumentParser(description="Generate PDF semantic summaries")
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
