"""DB Table Relations Generator - 数据库表关系生成器

职责：
- 匹配 *.db 下的表节点
- 分析该表的外键和命名约定关系
- 创建 fk 实体（无类型后缀，labels=["fk"]）

独立执行：
    python -m extractor.db_table_relations ./my_data
"""
import os
import logging
from typing import List, Dict
from storage import Store

logger = logging.getLogger(__name__)

DB_EXTENSIONS = ["*.db", "*.sqlite", "*.sqlite3", "*.duckdb"]


def generate(store: Store) -> None:
    """为所有表节点分析关系"""
    logger.info("=== Generating table relations ===")

    for ext in DB_EXTENSIONS:
        for db_ref in store.find_nodes(ext):
            for table_ref in store.find_nodes(f"{db_ref}--*:table"):
                try:
                    _generate_for_table(table_ref, db_ref, store)
                except Exception as e:
                    logger.warning(f"Failed to generate relations for {table_ref}: {e}")


def _generate_for_table(table_ref: str, db_ref: str, store: Store) -> bool:
    """为单个表分析关系，创建 fk 实体"""
    meta = store.get_meta(table_ref)
    if not meta:
        return False

    # table_ref 是完整节点名（如 formula_1.db--circuits），sqlite 需要裸表名
    raw_table = table_ref.split("--")[-1] if "--" in table_ref else table_ref
    db_meta = store.get_meta(db_ref)
    db_rel = db_meta.get("path", db_ref) if db_meta else db_ref
    db_path = os.path.join(store.project_path, db_rel)
    if not db_path or not os.path.isfile(db_path):
        return False

    columns = _get_table_columns(db_path, raw_table)
    if not columns:
        return False

    fk_relations = _find_foreign_keys(db_path, raw_table)
    naming_relations = _find_naming_relations(db_path, raw_table, columns)

    all_relations = fk_relations + naming_relations

    created_count = 0
    for rel in all_relations:
        if _create_relation_entity(table_ref, db_ref, table_ref, rel, store):
            created_count += 1

    if created_count > 0:
        logger.info(f"  Relations: {table_ref} ({created_count} relations)")
    return True


def _get_table_columns(db_path: str, table_name: str) -> List[Dict]:
    """获取表的列信息"""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        columns = [
            {"name": col[1], "type": col[2], "pk": col[5]}
            for col in cursor.fetchall()
        ]
        conn.close()
        return columns
    except Exception as e:
        logger.debug(f"Could not get columns: {e}")
        return []


def _find_foreign_keys(db_path: str, table_name: str) -> List[Dict]:
    """查找表的显式外键"""
    relations = []
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        all_tables = [row[0] for row in cursor.fetchall()]
        table_pks = {}
        for t in all_tables:
            cursor.execute(f'PRAGMA table_info("{t}")')
            pk_cols = [c[1] for c in cursor.fetchall() if c[5] == 1]
            table_pks[t] = pk_cols[0] if pk_cols else "rowid"

        cursor.execute(f'PRAGMA foreign_key_list("{table_name}")')
        fks = cursor.fetchall()

        for fk in fks:
            to_col = fk[4] if fk[4] else table_pks.get(fk[2], "rowid")
            relations.append({
                "type": "foreign_key",
                "from_column": fk[3],
                "to_table": fk[2],
                "to_column": to_col,
                "confidence": 1.0
            })
        conn.close()
    except Exception as e:
        logger.debug(f"Could not find FKs: {e}")

    return relations


def _find_naming_relations(db_path: str, table_name: str, columns: List[Dict]) -> List[Dict]:
    """通过命名约定查找关系 (e.g., user_id -> users.id)"""
    relations = []

    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        all_tables = [row[0] for row in cursor.fetchall()]

        table_pks = {}
        for t in all_tables:
            cursor.execute(f'PRAGMA table_info("{t}")')
            cols = cursor.fetchall()
            pk_cols = [c[1] for c in cols if c[5] == 1]
            table_pks[t] = pk_cols[0] if pk_cols else "rowid"

        conn.close()

        for col in columns:
            col_name = col["name"]
            if col.get("pk"):
                continue

            for ref_table in all_tables:
                if ref_table == table_name:
                    continue

                pk_col = table_pks.get(ref_table, "id")

                expected = f"{ref_table.rstrip('s')}s_id"
                expected_alt = f"{ref_table.rstrip('s')}_id"

                if col_name.lower() == expected.lower() or col_name.lower() == expected_alt.lower():
                    relations.append({
                        "type": "naming_convention",
                        "from_column": col_name,
                        "to_table": ref_table,
                        "to_column": pk_col,
                        "confidence": 0.7
                    })
                    break

    except Exception as e:
        logger.debug(f"Could not find naming relations: {e}")

    return relations


def _create_relation_entity(table_ref: str, db_ref: str, from_table: str,
                            relation: Dict, store: Store) -> bool:
    """创建 fk 实体（无类型后缀）"""
    try:
        from_col = relation["from_column"]
        to_table = relation["to_table"]
        to_col = relation["to_column"]

        safe_from_col = from_col.replace("/", "_").replace("\\", "_")
        safe_to_table = to_table.replace("/", "_").replace("\\", "_")
        safe_to_col = to_col.replace("/", "_").replace("\\", "_")

        raw_from_table = from_table.split("--")[-1] if "--" in from_table else from_table
        fk_entity_name = f"{raw_from_table}.{safe_from_col}->{safe_to_table}.{safe_to_col}"

        if store.node_exists(fk_entity_name):
            return False

        rel_type = "显式外键" if relation["type"] == "foreign_key" else "命名约定推断"
        confidence = relation.get("confidence", 0.5)

        fk_meta = {
            "detail": f"{from_table} 表的 {from_col} 列引用 {to_table} 表的 {to_col} 列。"
                      f"来源：{rel_type}（置信度 {confidence}）。",
            "created_at": __import__('datetime').datetime.now().isoformat(),
        }

        store.create_node(fk_entity_name, meta=fk_meta, labels=["fk"])

        to_table_ref = f"{db_ref}--{safe_to_table}"
        edges = [
            {"a": from_table, "b": fk_entity_name},
            {"a": to_table_ref, "b": fk_entity_name},
        ]

        # 查找列实体并连接（from_table 已经是完整节点名 db--table）
        from_col_ref = f"{from_table}--{safe_from_col}"
        to_col_ref = f"{to_table_ref}--{safe_to_col}"
        if store.node_exists(from_col_ref):
            edges.append({"a": from_col_ref, "b": fk_entity_name})
        if store.node_exists(to_col_ref):
            edges.append({"a": to_col_ref, "b": fk_entity_name})

        store.add_edges(edges)
        return True

    except Exception as e:
        logger.debug(f"Could not create relation: {e}")
        return False
