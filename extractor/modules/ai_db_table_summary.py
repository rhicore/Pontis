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
from storage import Store
from extractor.modules.utils.config import load_config
from extractor.modules.utils.ai_utils import generate_detail_and_brief

logger = logging.getLogger(__name__)

DB_EXTENSIONS = ["*.db", "*.sqlite", "*.sqlite3", "*.duckdb"]


def generate(store: Store) -> None:
    """为所有 .table 节点生成 AI 总结"""
    logger.info("=== AI: DB table summary ===")

    llm = load_config().get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping AI summary")
        return

    for ext in DB_EXTENSIONS:
        for ref in store.find_nodes(f"{ext}::*.table"):
            try:
                _generate_for_table(ref, store, llm)
            except Exception as e:
                logger.warning(f"Failed for {ref}: {e}")


def _generate_for_table(ref: str, store: Store, llm) -> bool:
    path, entity_name = ref.split("::", 1)
    meta = store.get_meta(ref)
    if not meta:
        return False

    if "detail" in meta and "brief" in meta:
        return False

    table_name = entity_name.replace(".table", "")
    columns = _get_column_info(path, table_name, store)
    prompt = _build_prompt(table_name, columns)

    try:
        detail, brief = generate_detail_and_brief(llm, prompt, max_tokens=200)
        updates = {}
        if detail:
            updates["detail"] = detail
        if brief:
            updates["brief"] = brief

        if updates:
            store.set_meta(ref, updates)
            logger.info(f"  AI summary: {ref}")
            return True
    except Exception as e:
        logger.debug(f"LLM failed: {e}")

    return False


def _get_column_info(db_path: str, table_name: str, store: Store) -> List[dict]:
    columns = []
    for col_ref in store.find_nodes(f"{db_path}::{table_name}.*.*.col"):
        col_meta = store.get_meta(col_ref)
        if col_meta:
            _, col_name = col_ref.split("::", 1)
            col_parts = col_name.replace(".col", "").split(".")
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
