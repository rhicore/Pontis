"""Column Semantic Generator - 列语义生成器 (AI)

职责：
- 匹配所有 *.db 下的 *.*.*.col 节点（扁平结构：[表名].[列名].[类型].col）
- 读取列名、样本、统计数据
- 使用LLM生成语义描述
- 追加到_meta.yml

独立执行：
    python -m extractor.db_column_semantic ./my_data
"""
import os
import logging
from typing import Optional
from extractor.utils import VFSStorage, NodeRef, get_llm, load_config

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有.col节点生成语义描述"""
    logger.info("=== Generating column semantics ===")

    llm = get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping semantic generation")
        return

    # 扁平结构: *.db/_entity/*.*.*.col (e.g., "users.id.INT.col")
    for node in storage.find_nodes("*.db/_entity/*.*.*.col"):
        try:
            _generate_for_column(node, storage, llm)
        except Exception as e:
            logger.warning(f"Failed to generate semantic for {node.name}: {e}")


def _generate_for_column(node: NodeRef, storage: VFSStorage, llm) -> bool:
    """为单个列生成语义描述"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    # 跳过已处理的
    if "semantic_summary" in meta:
        return False

    # 解析路径获取信息
    # 路径格式: [...]/[db_name].db/[table_name].[col_name].[type].col
    path_parts = node.rel_path.split(os.sep)
    if len(path_parts) < 2:
        return False

    # 找到.db节点位置
    db_idx = -1
    for i, part in enumerate(path_parts):
        if part.endswith('.db'):
            db_idx = i
            break

    if db_idx == -1 or db_idx + 1 >= len(path_parts):
        return False

    # 解析列节点名: [表名].[列名].[类型].col
    col_node_name = path_parts[db_idx + 1].replace(".col", "")
    col_parts = col_node_name.split(".")
    if len(col_parts) < 3:
        return False

    table_name = col_parts[0]
    col_name = col_parts[1]
    data_type = col_parts[2]

    # 收集上下文信息 - 从meta根级别读取
    samples = meta.get("sample", [])
    topk = meta.get("topk", [])
    cardinality = meta.get("cardinality")

    # 构建prompt
    prompt = _build_prompt(table_name, col_name, data_type, samples, cardinality, topk)

    # 调用LLM
    try:
        summary = llm.complete(prompt, max_tokens=100)
        if summary:
            meta["semantic_summary"] = summary.strip()
            storage.write_meta(node, meta)
            logger.info(f"  Semantic generated: {node.rel_path}")
            return True
    except Exception as e:
        logger.debug(f"LLM generation failed: {e}")

    return False


def _build_prompt(table: str, column: str, data_type: str, samples: list, cardinality: Optional[int], topk: list) -> str:
    """构建LLM prompt"""
    prompt = f"""Analyze this database column and provide a brief semantic summary (10-30 words).

Table: {table}
Column: {column}
Data Type: {data_type}
"""

    if cardinality is not None:
        prompt += f"Unique Values: {cardinality}\n"

    if samples:
        samples_str = ", ".join(str(s) for s in samples[:5])
        prompt += f"Sample Values: {samples_str}\n"

    if topk:
        topk_str = ", ".join(str(t.get("value")) for t in topk[:3])
        prompt += f"Most Common: {topk_str}\n"

    prompt += """
Describe what this column represents in simple terms. Be concise.
Example: "User email address used for login and notifications"
"""

    return prompt


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate column semantic summaries")
    parser.add_argument('target', help='Directory with .pontis')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    target_path = os.path.abspath(args.target)
    pontis_path = os.path.join(target_path, ".pontis")

    if not os.path.exists(pontis_path):
        print(f"Error: No .pontis found at {pontis_path}", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    storage = VFSStorage(pontis_path)
    generate(storage)
    print("Done.")


if __name__ == '__main__':
    main()
