"""AI DB Table Summary - 数据库表 AI 总结生成器

职责：
- 匹配所有 *.db 下的表节点
- 读取表名、列信息
- 使用 LLM 生成 detail（详细总结）和 brief（简要概括）
- 追加到 _meta.yml

独立执行：
    python -m extractor.ai_db_table_summary ./my_data
"""
import logging
from typing import List
from storage.workspace import Workspace
from extractor.modules.utils.loader import load_config
from extractor.modules.utils.ai_utils import generate_detail_and_brief
from extractor.modules.utils.refs import db_column_ref, db_table_ref, get_entity_meta, set_entity_meta

logger = logging.getLogger(__name__)



def generate(workspace: Workspace) -> None:
    """为所有表节点生成 AI 总结"""
    logger.info("=== AI: DB table summary ===")

    llm = load_config().get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping AI summary")
        return

    for ext_suffix in [".db", ".sqlite", ".sqlite3", ".duckdb"]:
        db_rows = workspace.cypher(
            "MATCH (n) WHERE n.name ENDS WITH $suffix RETURN n",
            params={"suffix": ext_suffix},
        )
        for db_row in db_rows:
            db_ref = db_row["n"]["name"]
            tbl_rows = workspace.cypher(
                "MATCH (d {name: $db_ref})--(t:table) RETURN t",
                params={"db_ref": db_ref},
            )
            for tbl_row in tbl_rows:
                table_ref = tbl_row["t"]["name"]
                try:
                    _generate_for_table(table_ref, db_ref, workspace, llm)
                except Exception as e:
                    logger.warning(f"Failed for {table_ref}: {e}")


def _generate_for_table(table_ref: str, db_ref: str, workspace: Workspace, llm) -> bool:
    table_node_ref = db_table_ref(db_ref, table_ref)
    meta = get_entity_meta(workspace, table_node_ref)
    if not meta:
        return False

    if "detail" in meta and "brief" in meta:
        return False

    table_name = table_ref
    columns = _get_column_info(db_ref, table_ref, workspace)
    prompt = _build_prompt(table_name, columns)

    try:
        detail, brief = generate_detail_and_brief(llm, prompt)
        updates = {}
        if detail:
            updates["detail"] = detail
        if brief:
            updates["brief"] = brief

        if updates:
            set_entity_meta(workspace, table_node_ref, updates)
            logger.info(f"  AI summary: {table_ref}")
            return True
    except Exception as e:
        logger.debug(f"LLM failed: {e}")

    return False


def _get_column_info(db_ref: str, table_ref: str, workspace: Workspace) -> List[dict]:
    columns = []
    col_rows = workspace.cypher(
        "MATCH (d {name: $db_ref})--(t {name: $table_ref})--(c:col) RETURN c",
        params={"db_ref": db_ref, "table_ref": table_ref},
    )
    for col_row in col_rows:
        col_name = col_row["c"]["name"]
        col_ref = db_column_ref(db_ref, table_ref, col_name)
        col_meta = get_entity_meta(workspace, col_ref)
        if col_meta:
            columns.append({
                "name": col_meta.get("name", col_name),
                "type": col_meta.get("col_type", "?"),
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
