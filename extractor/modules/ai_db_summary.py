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
from storage import Store
from extractor.utils import get_llm
from extractor.ai_utils import generate_detail_and_brief

logger = logging.getLogger(__name__)


def generate(store: Store) -> None:
    """为所有 .db 文件节点生成 AI 总结"""
    logger.info("=== AI: DB file summary ===")

    llm = get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping AI summary")
        return

    for path in store.find_nodes("*.db"):
        try:
            _generate_for_db(path, store, llm)
        except Exception as e:
            logger.warning(f"Failed for {path}: {e}")


def _generate_for_db(path: str, store: Store, llm) -> bool:
    meta = store.get_meta(path)
    if not meta:
        return False

    if "detail" in meta and "brief" in meta:
        return False

    db_name = os.path.splitext(os.path.basename(path))[0]
    tables = _get_table_info(path, store)
    views = _get_view_info(path, store)

    if not tables and not views:
        logger.debug(f"  No tables/views found for {path}")
        return False

    prompt = _build_prompt(db_name, tables, views, meta)

    try:
        detail, brief = generate_detail_and_brief(llm, prompt, max_tokens=300)
        updates = {}
        if detail:
            updates["detail"] = detail
        if brief:
            updates["brief"] = brief

        if updates:
            store.set_meta(path, updates)
            logger.info(f"  AI summary: {path}")
            return True
    except Exception as e:
        logger.debug(f"LLM failed: {e}")

    return False


def _get_table_info(db_path: str, store: Store) -> List[dict]:
    """读取数据库下所有表的元数据"""
    tables = []
    for table_ref in store.find_nodes(f"{db_path}::*.table"):
        table_meta = store.get_meta(table_ref)
        if table_meta:
            _, table_name = table_ref.split("::", 1)
            table_name_clean = table_name.replace(".table", "")
            # 读取列信息
            columns = []
            for col_ref in store.find_nodes(f"{db_path}::{table_name_clean}.*.*.col"):
                col_meta = store.get_meta(col_ref)
                if col_meta:
                    _, col_name = col_ref.split("::", 1)
                    col_parts = col_name.replace(".col", "").split(".")
                    columns.append({
                        "name": col_parts[1] if len(col_parts) >= 2 else col_name,
                        "type": col_parts[2] if len(col_parts) >= 3 else "?",
                        "detail": col_meta.get("detail", ""),
                    })

            tables.append({
                "name": table_name_clean,
                "row_count": table_meta.get("row_count"),
                "column_count": table_meta.get("column_count"),
                "primary_key": table_meta.get("primary_key"),
                "detail": table_meta.get("detail", ""),
                "columns": columns,
            })
    return tables


def _get_view_info(db_path: str, store: Store) -> List[dict]:
    """读取数据库下所有视图的元数据"""
    views = []
    for view_ref in store.find_nodes(f"{db_path}::*.view"):
        view_meta = store.get_meta(view_ref)
        if view_meta:
            _, view_name = view_ref.split("::", 1)
            views.append({
                "name": view_name.replace(".view", ""),
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
