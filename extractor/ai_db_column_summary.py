"""AI DB Column Summary - 数据库列 AI 总结生成器

职责：
- 匹配所有 *.db/_entity/*.*.*.col 节点
- 读取列名、样本、统计信息
- 使用 LLM 生成 detail 和 brief
- 追加到 _meta.yml

独立执行：
    python -m extractor.ai_db_column_summary ./my_data
"""
import os
import logging
from typing import Optional
from extractor.utils import VFSStorage, NodeRef, get_llm
from extractor.ai_utils import generate_detail_and_brief

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    logger.info("=== AI: DB column summary ===")

    llm = get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping AI summary")
        return

    for node in storage.find_nodes("*.db/_entity/*.*.*.col"):
        try:
            _generate_for_column(node, storage, llm)
        except Exception as e:
            logger.warning(f"Failed for {node.name}: {e}")


def _generate_for_column(node: NodeRef, storage: VFSStorage, llm) -> bool:
    meta = storage.read_meta(node)
    if not meta:
        return False

    if "detail" in meta and "brief" in meta:
        return False

    path_parts = node.rel_path.split(os.sep)
    if len(path_parts) < 2:
        return False

    db_idx = -1
    for i, part in enumerate(path_parts):
        if part.endswith('.db'):
            db_idx = i
            break

    if db_idx == -1 or db_idx + 1 >= len(path_parts):
        return False

    # 解析列节点名: 支持 _entity/ 子目录结构
    if db_idx + 1 < len(path_parts) and path_parts[db_idx + 1] == '_entity':
        col_node_name = path_parts[db_idx + 2].replace(".col", "")
    else:
        col_node_name = path_parts[db_idx + 1].replace(".col", "")

    col_parts = col_node_name.split(".")
    if len(col_parts) < 3:
        return False

    table_name = col_parts[0]
    col_name = col_parts[1]
    data_type = col_parts[2]

    samples = meta.get("sample", [])
    topk = meta.get("topk", [])
    cardinality = meta.get("cardinality")

    prompt = _build_prompt(table_name, col_name, data_type, samples, cardinality, topk)

    try:
        detail, brief = generate_detail_and_brief(llm, prompt, max_tokens=120)
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


def _build_prompt(table: str, column: str, data_type: str, samples: list,
                  cardinality: Optional[int], topk: list) -> str:
    prompt = f"""Analyze this database column and generate TWO summaries.

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
Generate a comprehensive description of what this column represents. Be as detailed as possible — cover the column's purpose, value patterns, data quality issues, notable distributions, and any anomalies.

IMPORTANT rules:
- Do NOT mention specific counts (exact cardinality, exact null counts or percentages). These numbers change frequently and make the summary outdated quickly. Use qualitative language instead (e.g., "low cardinality", "high null rate", "most values are unique").
- Focus on the column's purpose, value semantics, and patterns — things that remain stable over time.
- Output ONLY plain text, no labels, no markdown formatting.\
"""
    return prompt


def main():
    import argparse, sys
    parser = argparse.ArgumentParser(description="AI DB column summary")
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
