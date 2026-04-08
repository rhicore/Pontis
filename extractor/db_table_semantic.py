"""DB Table Semantic Generator - 数据库表语义生成器 (AI)

职责：
- 匹配 *.db/_entity/*.table 节点
- 读取表名、列信息（从扁平结构的列节点）
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

    for node in storage.find_nodes("*.db/_entity/*.table"):
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

    # 获取列信息（从扁平结构的列节点）
    columns = _get_column_info(node, table_name, storage)

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


def _get_column_info(table_node: NodeRef, table_name: str, storage: VFSStorage) -> List[dict]:
    """获取表的列信息（从扁平结构：*.db/[表名].[列名].[类型].col）"""
    columns = []

    # 找到.db节点路径
    path_parts = table_node.rel_path.split(os.sep)
    if len(path_parts) < 2:
        return columns

    db_idx = -1
    for i, part in enumerate(path_parts):
        if part.endswith('.db'):
            db_idx = i
            break

    if db_idx == -1:
        return columns

    db_rel_path = os.sep.join(path_parts[:db_idx+1])

    # 查找所有属于该表的列节点（扁平结构：[表名].[列名].[类型].col）
    # 列节点名格式: [table_name].[col_name].[type].col
    col_pattern = os.path.join(db_rel_path, f"{table_name}.*.*.col")
    for col_node in storage.find_nodes(col_pattern):
        col_meta = storage.read_meta(col_node)
        if col_meta:
            # 解析列节点名: [表名].[列名].[类型].col
            col_node_name = col_node.name.replace(".col", "")
            col_parts = col_node_name.split(".")
            if len(col_parts) >= 3:
                col_name = col_parts[1]
                col_type = col_parts[2]
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
