"""CSV Semantic Generator - CSV语义生成器 (AI)

职责：
- 匹配 *.csv/*.tsv 节点和 *.csv/*.tsv 下的扁平列节点
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

    # 再生成列级语义（扁平结构: *.csv/*.*.*.col）
    for node in storage.find_nodes("*.csv/*.*.*.col"):
        try:
            _generate_for_column(node, storage, llm)
        except Exception as e:
            logger.warning(f"Failed to generate semantic for {node.name}: {e}")

    for node in storage.find_nodes("*.tsv/*.*.*.col"):
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

    # 获取列名（从扁平结构的列节点：[文件名].[列名].TEXT.col）
    columns = []
    csv_stem = node.stem  # CSV文件名（不含扩展名）
    # 查找该CSV下的所有列节点
    col_pattern = os.path.join(node.rel_path, f"{csv_stem}.*.*.col")
    for col_node in storage.find_nodes(col_pattern):
        # 解析列节点名: [文件名].[列名].TEXT.col
        col_parts = col_node.name.replace(".col", "").split(".")
        if len(col_parts) >= 2:
            col_name = col_parts[1]  # 第二部分是列名
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

    # 解析列节点名: [文件名].[列名].TEXT.col
    col_parts = node.name.replace(".col", "").split(".")
    if len(col_parts) < 3:
        return False

    col_name = col_parts[1]  # 第二部分是列名
    data_type = col_parts[2]  # 第三部分是数据类型

    # 获取样本和topk（从meta根级别）
    samples = meta.get("sample", [])
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
