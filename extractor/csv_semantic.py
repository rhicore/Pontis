"""CSV Semantic Generator - CSV语义生成器 (AI)

职责：
- 匹配 *.csv/*.tsv 节点和 *.csv/*.tsv/*.col 节点
- 使用LLM生成CSV文件和列的语义描述
- 追加到_meta.yml

独立执行：
    python -m extractor.csv_semantic ./my_data
"""
import os
import logging
from extractor.utils import VFSStorage, NodeRef, get_llm

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有CSV/TSV文件和列生成语义描述"""
    logger.info("=== Generating CSV semantics ===")

    llm = get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping CSV semantic generation")
        return

    # 先生成文件级语义
    for node in storage.find_nodes("*.csv"):
        try:
            _generate_for_csv(node, storage, llm)
        except Exception as e:
            logger.warning(f"Failed to generate semantic for {node.name}: {e}")

    for node in storage.find_nodes("*.tsv"):
        try:
            _generate_for_csv(node, storage, llm)
        except Exception as e:
            logger.warning(f"Failed to generate semantic for {node.name}: {e}")

    # 再生成列级语义
    for node in storage.find_nodes("*.csv/*.*.col"):
        try:
            _generate_for_column(node, storage, llm)
        except Exception as e:
            logger.warning(f"Failed to generate semantic for {node.name}: {e}")

    for node in storage.find_nodes("*.tsv/*.*.col"):
        try:
            _generate_for_column(node, storage, llm)
        except Exception as e:
            logger.warning(f"Failed to generate semantic for {node.name}: {e}")


def _generate_for_csv(node: NodeRef, storage: VFSStorage, llm) -> bool:
    """为CSV文件生成语义描述"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    if "semantic_summary" in meta:
        return False

    column_count = meta.get("column_count", 0)
    row_count = meta.get("row_count", 0)

    # 获取列名
    columns = []
    for child in storage.list_children(node):
        if child.name.endswith(".col"):
            col_meta = storage.read_meta(child)
            if col_meta:
                col_name = col_meta.get("source_column", child.name.split(".")[0])
                columns.append(col_name)

    prompt = f"""Analyze this CSV/TSV file and provide a brief semantic summary (10-30 words).

Stats: {row_count} rows, {column_count} columns
Columns: {', '.join(columns[:10])}

Describe what this data likely represents (e.g., "Customer transaction records", "Product inventory list").
Be concise and specific.
"""

    try:
        summary = llm.complete(prompt, max_tokens=50)
        if summary:
            meta["semantic_summary"] = summary.strip()
            storage.write_meta(node, meta)
            logger.info(f"  CSV semantic: {node.rel_path}")
            return True
    except Exception as e:
        logger.debug(f"LLM generation failed: {e}")

    return False


def _generate_for_column(node: NodeRef, storage: VFSStorage, llm) -> bool:
    """为CSV列生成语义描述"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    if "semantic_summary" in meta:
        return False

    col_name = node.name.split(".")[0]
    data_type = node.name.split(".")[1] if len(node.name.split(".")) > 1 else "TEXT"

    # 获取样本
    samples = meta.get("samples", [])
    topk = meta.get("topk", [])

    prompt = f"""Analyze this CSV column and provide a brief semantic summary (5-15 words).

Column Name: {col_name}
Data Type: {data_type}
"""
    if samples:
        prompt += f"Samples: {', '.join(str(s) for s in samples[:5])}\n"
    if topk:
        prompt += f"Common Values: {', '.join(str(t.get('value')) for t in topk[:3])}\n"

    prompt += """
Describe what this column represents (e.g., "Customer email address", "Product price in USD").
"""

    try:
        summary = llm.complete(prompt, max_tokens=30)
        if summary:
            meta["semantic_summary"] = summary.strip()
            storage.write_meta(node, meta)
            logger.info(f"  Column semantic: {node.rel_path}")
            return True
    except Exception as e:
        logger.debug(f"LLM generation failed: {e}")

    return False


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate CSV semantic summaries")
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
