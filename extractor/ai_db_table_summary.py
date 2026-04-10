"""AI DB Table Summary - 数据库表 AI 总结生成器

职责：
- 匹配所有 *.db/_entity/*.table 节点
- 读取表名、列信息
- 使用 LLM 生成 detail（详细总结）和 brief（简要概括）
- 追加到 _meta.yml

独立执行：
    python -m extractor.ai_db_table_summary ./my_data
"""
import os
import logging
from typing import List
from extractor.utils import VFSStorage, NodeRef, get_llm
from extractor.ai_utils import generate_detail_and_brief

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有 .table 节点生成 AI 总结"""
    logger.info("=== AI: DB table summary ===")

    llm = get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping AI summary")
        return

    for node in storage.find_nodes("*.db/_entity/*.table"):
        try:
            _generate_for_table(node, storage, llm)
        except Exception as e:
            logger.warning(f"Failed for {node.name}: {e}")


def _generate_for_table(node: NodeRef, storage: VFSStorage, llm) -> bool:
    meta = storage.read_meta(node)
    if not meta:
        return False

    if "detail" in meta and "brief" in meta:
        return False

    table_name = node.name.replace(".table", "")
    columns = _get_column_info(node, table_name, storage)
    prompt = _build_prompt(table_name, columns)

    try:
        detail, brief = generate_detail_and_brief(llm, prompt, max_tokens=200)
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


def _get_column_info(table_node: NodeRef, table_name: str, storage: VFSStorage) -> List[dict]:
    columns = []
    path_parts = table_node.rel_path.split(os.sep)
    db_idx = -1
    for i, part in enumerate(path_parts):
        if part.endswith('.db'):
            db_idx = i
            break
    if db_idx == -1:
        return columns

    db_rel_path = os.sep.join(path_parts[:db_idx+1])
    col_pattern = os.path.join(db_rel_path, "_entity", f"{table_name}.*.*.col")
    for col_node in storage.find_nodes(col_pattern):
        col_meta = storage.read_meta(col_node)
        if col_meta:
            col_node_name = col_node.name.replace(".col", "")
            col_parts = col_node_name.split(".")
            if len(col_parts) >= 3:
                columns.append({
                    "name": col_parts[1],
                    "type": col_parts[2],
                    "detail": col_meta.get("detail", ""),
                })
    return columns


def _build_prompt(table: str, columns: List[dict]) -> str:
    prompt = f"""Analyze this database table and generate TWO summaries.

Table Name: {table}

Columns:
"""
    for col in columns[:20]:
        detail = f" — {col['detail']}" if col.get('detail') else ""
        prompt += f"- {col['name']} ({col['type']}){detail}\n"

    prompt += f"""
Generate a comprehensive description of what this table represents. Be as detailed as possible — cover the table's purpose, each column's role, key relationships, data patterns, anomalies, and anything notable.

IMPORTANT rules:
- Do NOT mention specific counts (exact row counts, exact column counts, exact cardinality). These numbers change frequently and make the summary outdated quickly. Use qualitative language instead (e.g., "dozens of records", "a small set of categories").
- Focus on the structure, purpose, and semantics of the data — things that remain stable over time.
- Output ONLY plain text, no labels, no markdown formatting.\
"""
    return prompt


def main():
    import argparse, sys
    parser = argparse.ArgumentParser(description="AI DB table summary")
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
