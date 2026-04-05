"""DB Table Semantic Generator - 数据库表语义生成器 (AI)

职责：
- 匹配 *.db/*.table 节点
- 读取表名、列信息
- 使用LLM生成表语义描述
- 追加到_meta.yml

独立执行：
    python -m extractor.db_table_semantic ./my_data
"""
import os
import logging
from typing import List
from extractor.utils import VFSStorage, NodeRef, get_llm

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有.table节点生成语义描述"""
    logger.info("=== Generating table semantics ===")

    llm = get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping semantic generation")
        return

    for node in storage.find_nodes("*.db/*.table"):
        try:
            _generate_for_table(node, storage, llm)
        except Exception as e:
            logger.warning(f"Failed to generate semantic for {node.name}: {e}")


def _generate_for_table(node: NodeRef, storage: VFSStorage, llm) -> bool:
    """为单个表生成语义描述"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    # 跳过已处理的
    if "semantic_summary" in meta:
        return False

    table_name = node.name.replace(".table", "")

    # 获取列信息
    columns = _get_column_info(node, storage)

    # 构建prompt
    prompt = _build_prompt(table_name, columns)

    # 调用LLM
    try:
        summary = llm.complete(prompt, max_tokens=150)
        if summary:
            meta["semantic_summary"] = summary.strip()
            storage.write_meta(node, meta)
            logger.info(f"  Semantic generated: {node.rel_path}")
            return True
    except Exception as e:
        logger.debug(f"LLM generation failed: {e}")

    return False


def _get_column_info(table_node: NodeRef, storage: VFSStorage) -> List[dict]:
    """获取表的列信息"""
    columns = []

    # 查找所有列节点
    col_pattern = os.path.join(table_node.rel_path, "*.col")
    for col_node in storage.find_nodes(col_pattern):
        col_meta = storage.read_meta(col_node)
        if col_meta:
            col_name = col_node.name.replace(".col", "").split(".")[0]
            col_type = col_node.name.replace(".col", "").split(".")[1] if len(col_node.name.replace(".col", "").split(".")) > 1 else "TEXT"
            columns.append({
                "name": col_name,
                "type": col_type,
                "summary": col_meta.get("semantic_summary", "")
            })

    return columns


def _build_prompt(table: str, columns: List[dict]) -> str:
    """构建LLM prompt"""
    prompt = f"""Analyze this database table and provide a semantic summary.

Table Name: {table}

Columns:
"""

    for col in columns[:20]:  # 限制列数避免prompt过长
        summary = f" - {col['summary']}" if col.get('summary') else ""
        prompt += f"- {col['name']} ({col['type']}){summary}\n"

    prompt += """
Provide:
1. A brief description of what this table represents (20-50 words)
2. The primary purpose or entity it stores

Be concise and specific.
"""

    return prompt


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate table semantic summaries")
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
