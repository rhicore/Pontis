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
from extractor.utils import VFSStorage, NodeRef, get_llm
from extractor.ai_utils import generate_detail_and_brief

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有 .db 文件节点生成 AI 总结"""
    logger.info("=== AI: DB file summary ===")

    llm = get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping AI summary")
        return

    for node in storage.find_nodes("*.db"):
        try:
            _generate_for_db(node, storage, llm)
        except Exception as e:
            logger.warning(f"Failed for {node.name}: {e}")


def _generate_for_db(node: NodeRef, storage: VFSStorage, llm) -> bool:
    meta = storage.read_meta(node)
    if not meta:
        return False

    if "detail" in meta and "brief" in meta:
        return False

    db_name = node.name.replace(".db", "")
    tables = _get_table_info(node, storage)
    views = _get_view_info(node, storage)

    if not tables and not views:
        logger.debug(f"  No tables/views found for {node.name}")
        return False

    prompt = _build_prompt(db_name, tables, views, meta)

    try:
        detail, brief = generate_detail_and_brief(llm, prompt, max_tokens=300)
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


def _get_table_info(db_node: NodeRef, storage: VFSStorage) -> List[dict]:
    """读取数据库下所有表的元数据"""
    tables = []
    for table_node in storage.find_nodes(os.path.join(db_node.rel_path, "_entity", "*.table")):
        table_meta = storage.read_meta(table_node)
        if table_meta:
            table_name = table_node.name.replace(".table", "")
            # 读取列信息
            columns = []
            col_pattern = os.path.join(db_node.rel_path, "_entity", f"{table_name}.*.*.col")
            for col_node in storage.find_nodes(col_pattern):
                col_meta = storage.read_meta(col_node)
                if col_meta:
                    col_parts = col_node.name.replace(".col", "").split(".")
                    columns.append({
                        "name": col_parts[1] if len(col_parts) >= 2 else col_node.name,
                        "type": col_parts[2] if len(col_parts) >= 3 else "?",
                        "detail": col_meta.get("detail", ""),
                    })

            tables.append({
                "name": table_name,
                "row_count": table_meta.get("row_count"),
                "column_count": table_meta.get("column_count"),
                "primary_key": table_meta.get("primary_key"),
                "detail": table_meta.get("detail", ""),
                "columns": columns,
            })
    return tables


def _get_view_info(db_node: NodeRef, storage: VFSStorage) -> List[dict]:
    """读取数据库下所有视图的元数据"""
    views = []
    for view_node in storage.find_nodes(os.path.join(db_node.rel_path, "_entity", "*.view")):
        view_meta = storage.read_meta(view_node)
        if view_meta:
            views.append({
                "name": view_node.name.replace(".view", ""),
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


def main():
    import argparse, sys
    parser = argparse.ArgumentParser(description="AI DB file summary")
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
