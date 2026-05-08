"""AI DB Summary - 数据库文件级 AI 总结生成器

职责：
- 匹配所有 *.db 节点
- 读取该数据库所有表、视图等高级别实体的元数据（含 detail）
- 使用 LLM 生成数据库整体的 detail 和 brief
- 追加到 _meta.yml

独立执行：
    python -m extractor.ai_db_summary ./my_data
"""
import os
import logging
from typing import List
from storage.workspace import Workspace
from extractor.modules.utils.loader import load_config
from extractor.modules.utils.ai_utils import generate_detail_and_brief

logger = logging.getLogger(__name__)



def generate(workspace: Workspace) -> None:
    """为所有 .db 文件节点生成 AI 总结"""
    logger.info("=== AI: DB file summary ===")

    llm = load_config().get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping AI summary")
        return

    for ext_suffix in [".db", ".sqlite", ".sqlite3", ".duckdb"]:
        db_rows = workspace.cypher(f"MATCH (n) WHERE n.name ENDS WITH '{ext_suffix}' RETURN n")
        for db_row in db_rows:
            db_ref = db_row["n"]["name"]
            try:
                _generate_for_db(db_ref, workspace, llm)
            except Exception as e:
                logger.warning(f"Failed for {db_ref}: {e}")


def _generate_for_db(db_ref: str, workspace: Workspace, llm) -> bool:
    meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": db_ref})
    meta = meta_rows[0].get("n") if meta_rows else None
    if not meta:
        return False

    if "detail" in meta and "brief" in meta:
        return False

    db_name = os.path.splitext(os.path.basename(db_ref))[0]
    tables = _get_table_info(db_ref, workspace)
    views = _get_view_info(db_ref, workspace)

    if not tables and not views:
        logger.debug(f"  No tables/views found for {db_ref}")
        return False

    prompt = _build_prompt(db_name, tables, views, meta)

    try:
        detail, brief = generate_detail_and_brief(llm, prompt)
        updates = {}
        if detail:
            updates["detail"] = detail
        if brief:
            updates["brief"] = brief

        if updates:
            workspace.cypher('MATCH (n {name: $name}) SET n += $props', params={"name": db_ref, "props": updates})
            logger.info(f"  AI summary: {db_ref}")
            return True
    except Exception as e:
        logger.debug(f"LLM failed: {e}")

    return False


def _get_table_info(db_ref: str, workspace: Workspace) -> List[dict]:
    """读取数据库下所有表的元数据"""
    tables = []
    tbl_rows = workspace.cypher(f'MATCH (d {{name: "{db_ref}"}})--(t:table) RETURN t')
    for tbl_row in tbl_rows:
        table_ref = tbl_row["t"]["name"]
        table_meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": table_ref})
        table_meta = table_meta_rows[0].get("n") if table_meta_rows else None
        if table_meta:
            columns = []
            col_rows = workspace.cypher(f'MATCH (d {{name: "{db_ref}"}})--(t {{name: "{table_ref}"}})--(c:col) RETURN c')
            for col_row in col_rows:
                col_ref = col_row["c"]["name"]
                col_meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": col_ref})
                col_meta = col_meta_rows[0].get("n") if col_meta_rows else None
                if col_meta:
                    columns.append({
                        "name": col_ref,
                        "type": col_meta.get("col_type", "?"),
                        "detail": col_meta.get("detail", ""),
                    })

            tables.append({
                "name": table_ref,
                "row_count": table_meta.get("row_count"),
                "column_count": table_meta.get("column_count"),
                "primary_key": table_meta.get("primary_key"),
                "detail": table_meta.get("detail", ""),
                "columns": columns,
            })
    return tables


def _get_view_info(db_ref: str, workspace: Workspace) -> List[dict]:
    """读取数据库下所有视图的元数据"""
    views = []
    view_rows = workspace.cypher(f'MATCH (d {{name: "{db_ref}"}})--(v:view) RETURN v')
    for view_row in view_rows:
        view_ref = view_row["v"]["name"]
        view_meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": view_ref})
        view_meta = view_meta_rows[0].get("n") if view_meta_rows else None
        if view_meta:
            views.append({
                "name": view_ref,
                "row_count": view_meta.get("row_count"),
                "column_count": view_meta.get("column_count"),
                "detail": view_meta.get("detail", ""),
            })
    return views


def _build_prompt(db_name: str, tables: List[dict], views: List[dict],
                  db_meta: dict) -> str:
    prompt = f"""Analyze this database and generate TWO summaries.

Database: {db_name}
Tables: {db_meta.get('table_count', len(tables))}
Views: {db_meta.get('view_count', len(views))}
File Size: {db_meta.get('file_size', '?')} bytes

"""

    prompt += "Tables:\n"
    for t in tables:
        row_info = f", {t['row_count']} rows" if t.get('row_count') is not None else ""
        pk_info = f", PK: {t['primary_key']}" if t.get('primary_key') else ""
        prompt += f"- {t['name']} ({t.get('column_count', '?')} cols{row_info}{pk_info})\n"
        if t.get("detail"):
            prompt += f"  Summary: {t['detail']}\n"
        for col in t.get("columns", []):
            col_detail = f" — {col['detail']}" if col.get("detail") else ""
            prompt += f"  - {col['name']} ({col['type']}){col_detail}\n"

    if views:
        prompt += "\nViews:\n"
        for v in views:
            prompt += f"- {v['name']} ({v.get('column_count', '?')} cols)\n"
            if v.get("detail"):
                prompt += f"  Summary: {v['detail']}\n"

    prompt += """
Generate a comprehensive description of this database. Be as detailed as possible — cover the database's overall purpose, what domain it serves, how tables relate to each other, key entities and their roles, data quality observations, and any notable patterns.

IMPORTANT rules:
- Do NOT mention specific counts (exact row counts, exact table counts, exact cardinality). These numbers change frequently and make the summary outdated quickly. Use qualitative language instead (e.g., "dozens of records", "a small set of categories").
- Focus on the structure, purpose, and semantics of the data — things that remain stable over time.
- Output ONLY plain text, no labels, no markdown formatting.\
"""
    return prompt
