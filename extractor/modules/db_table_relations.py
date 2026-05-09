"""DB Table Relations Generator - 数据库表关系生成器

职责：
- 匹配 *.db 下的表节点
- 分析该表的外键和命名约定关系
- 创建 fk 实体（无类型后缀，labels=["fk"]）

独立执行：
    python -m extractor.db_table_relations ./my_data
"""
import logging
from typing import List, Dict
from storage.workspace import Workspace
from extractor.modules.utils.refs import db_column_ref, db_table_ref
from extractor.modules.utils.refs import get_entity_meta

logger = logging.getLogger(__name__)



def generate(workspace: Workspace) -> None:
    """为所有表节点分析关系"""
    logger.info("=== Generating table relations ===")

    for ext_suffix in [".db", ".sqlite", ".sqlite3", ".duckdb"]:
        db_rows = workspace.cypher(f"MATCH (n) WHERE n.name ENDS WITH '{ext_suffix}' RETURN n")
        for db_row in db_rows:
            db_ref = db_row["n"]["name"]
            tbl_rows = workspace.cypher(f'MATCH (d {{name: "{db_ref}"}})--(t:table) RETURN t')
            for tbl_row in tbl_rows:
                table_ref = tbl_row["t"]["name"]
                try:
                    _generate_for_table(table_ref, db_ref, workspace)
                except Exception as e:
                    logger.warning(f"Failed to generate relations for {table_ref}: {e}")


def _generate_for_table(table_ref: str, db_ref: str, workspace: Workspace) -> bool:
    """为单个表分析关系，创建 fk 实体"""
    table_node_ref = db_table_ref(db_ref, table_ref)
    meta = get_entity_meta(workspace, table_node_ref)
    if not meta:
        return False

    # table_ref 是完整节点名（如 formula_1.db--circuits），sqlite 需要裸表名
    raw_table = table_ref.split("--")[-1] if "--" in table_ref else table_ref
    db_meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": db_ref})
    db_meta = db_meta_rows[0].get("n") if db_meta_rows else None
    db_rel = db_meta.get("path", db_ref) if db_meta else db_ref
    if not workspace.data_exists(db_rel):
        return False

    columns = _get_table_columns(db_rel, raw_table, workspace)
    if not columns:
        return False

    fk_relations = _find_foreign_keys(db_rel, raw_table, workspace)
    naming_relations = _find_naming_relations(db_rel, raw_table, columns, workspace)

    all_relations = fk_relations + naming_relations

    created_count = 0
    for rel in all_relations:
        if _create_relation_entity(table_ref, db_ref, table_ref, rel, workspace):
            created_count += 1

    if created_count > 0:
        logger.info(f"  Relations: {table_ref} ({created_count} relations)")
    return True


def _get_table_columns(db_rel: str, table_name: str, workspace: Workspace) -> List[Dict]:
    """获取表的列信息"""
    try:
        with workspace.open_db(db_rel) as conn:
            cursor = conn.cursor()
            cursor.execute(f'PRAGMA table_info("{table_name}")')
            columns = [
                {"name": col[1], "type": col[2], "pk": col[5]}
                for col in cursor.fetchall()
            ]
            return columns
    except Exception as e:
        logger.debug(f"Could not get columns: {e}")
        return []


def _find_foreign_keys(db_rel: str, table_name: str, workspace: Workspace) -> List[Dict]:
    """查找表的显式外键"""
    relations = []
    try:
        with workspace.open_db(db_rel) as conn:
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
    except Exception as e:
        logger.debug(f"Could not find FKs: {e}")

    return relations


def _find_naming_relations(db_rel: str, table_name: str, columns: List[Dict], workspace: Workspace) -> List[Dict]:
    """通过命名约定查找关系 (e.g., user_id -> users.id)"""
    relations = []

    try:
        with workspace.open_db(db_rel) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            all_tables = [row[0] for row in cursor.fetchall()]

            table_pks = {}
            for t in all_tables:
                cursor.execute(f'PRAGMA table_info("{t}")')
                cols = cursor.fetchall()
                pk_cols = [c[1] for c in cols if c[5] == 1]
                table_pks[t] = pk_cols[0] if pk_cols else "rowid"

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
                            relation: Dict, workspace: Workspace) -> bool:
    """创建 fk 实体（无类型后缀）"""
    try:
        from_col = relation["from_column"]
        to_table = relation["to_table"]
        to_col = relation["to_column"]

        safe_from_col = from_col.replace("/", "_").replace("\\", "_")
        safe_to_table = to_table.replace("/", "_").replace("\\", "_")
        safe_to_col = to_col.replace("/", "_").replace("\\", "_")

        raw_from_table = from_table.split("--")[-1] if "--" in from_table else from_table
        fkname = f"{raw_from_table}.{safe_from_col}->{safe_to_table}.{safe_to_col}"

        if workspace.cypher(f'MATCH (n {{name: "{fkname}"}}) RETURN n'):
            return False

        ts = __import__('datetime').datetime.now().isoformat()
        workspace.cypher(f'CREATE (f:fk {{name: "{fkname}", created_at: "{ts}"}})')

        # 边: from_table → fk, to_table → fk
        store = workspace._get_store()
        from_table_ref = db_table_ref(db_ref, raw_from_table)
        to_table_ref = db_table_ref(db_ref, safe_to_table)
        store._add_edges([{"a": from_table_ref, "b": fkname}, {"a": to_table_ref, "b": fkname}])

        # 查找列实体并连接
        from_col_ref = db_column_ref(db_ref, raw_from_table, safe_from_col)
        to_col_ref = db_column_ref(db_ref, safe_to_table, safe_to_col)

        if store._resolve_to_id(from_col_ref):
            store._add_edges([{"a": from_col_ref, "b": fkname}])
        if store._resolve_to_id(to_col_ref):
            store._add_edges([{"a": to_col_ref, "b": fkname}])

        return True

    except Exception as e:
        logger.debug(f"Could not create relation: {e}")
        return False
