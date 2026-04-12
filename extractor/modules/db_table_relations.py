"""DB Table Relations Generator - 数据库表关系生成器

职责：
- 匹配 *.db/_entity/*.table 节点
- 分析该表的外键和命名约定关系
- 在 _entity/ 下创建 [表名].[列名]__to__[目标表名].[目标列名].fk 实体

独立执行：
    python -m extractor.db_table_relations ./my_data
"""
import os
import logging
from typing import List, Dict
from storage import Store

logger = logging.getLogger(__name__)


def generate(store: Store) -> None:
    """为所有表节点分析关系"""
    logger.info("=== Generating table relations ===")

    for ref in store.find_nodes("*.db::*.table"):
        try:
            _generate_for_table(ref, store)
        except Exception as e:
            logger.warning(f"Failed to generate relations for {ref}: {e}")


def _generate_for_table(ref: str, store: Store) -> bool:
    """为单个表分析关系，在_entity/下创建.fk实体"""
    path, entity_name = ref.split("::", 1)
    meta = store.get_meta(ref)
    if not meta:
        return False

    table_name = entity_name.replace(".table", "")

    # 获取DB路径
    db_path = os.path.join(store.project_path, store.get_meta(path).get("path", ""))
    if not db_path:
        return False

    # 获取该表的列信息
    columns = _get_table_columns(db_path, table_name)
    if not columns:
        return False

    # 查找外键关系
    fk_relations = _find_foreign_keys(db_path, table_name)

    # 查找命名约定关系
    naming_relations = _find_naming_relations(db_path, table_name, columns)

    # 合并所有关系
    all_relations = fk_relations + naming_relations

    # 为每个关系创建.fk实体
    created_count = 0
    for rel in all_relations:
        if _create_relation_entity(path, table_name, rel, store):
            created_count += 1

    if created_count > 0:
        logger.info(f"  Relations: {path}::{entity_name} ({created_count} relations)")
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
        cursor.execute(f'PRAGMA foreign_key_list("{table_name}")')
        fks = cursor.fetchall()

        for fk in fks:
            relations.append({
                "type": "foreign_key",
                "from_column": fk[3],
                "to_table": fk[2],
                "to_column": fk[4] if fk[4] else "id",
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

        # 构建表名到主键的映射
        table_pks = {}
        for t in all_tables:
            cursor.execute(f'PRAGMA table_info("{t}")')
            cols = cursor.fetchall()
            pk_cols = [c[1] for c in cols if c[5] == 1]
            table_pks[t] = pk_cols[0] if pk_cols else "id"

        conn.close()

        # 检查每一列
        for col in columns:
            col_name = col["name"]

            # 跳过主键
            if col.get("pk"):
                continue

            # 模式: table_id (e.g., user_id)
            for ref_table in all_tables:
                if ref_table == table_name:
                    continue

                pk_col = table_pks.get(ref_table, "id")

                # users -> user_id
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


def _create_relation_entity(path: str, from_table: str, relation: Dict,
                            store: Store) -> bool:
    """在_entity/下为关系创建.fk实体"""
    try:
        from_col = relation["from_column"]
        to_table = relation["to_table"]
        to_col = relation["to_column"]

        # 构建关系实体名: [表名].[列名]__to__[目标表名].[目标列名].fk
        safe_from_col = from_col.replace("/", "_").replace("\\", "_")
        safe_to_table = to_table.replace("/", "_").replace("\\", "_")
        safe_to_col = to_col.replace("/", "_").replace("\\", "_")

        fk_entity_name = f"{from_table}.{safe_from_col}__to__{safe_to_table}.{safe_to_col}.fk"

        # 检查是否已存在
        if store.node_exists(f"{path}::{fk_entity_name}"):
            return False

        # 创建关系meta
        fk_meta = {
            "relation_type": relation["type"],
            "from_table": from_table,
            "from_column": from_col,
            "to_table": to_table,
            "to_column": to_col,
            "confidence": relation.get("confidence", 0.5),
            "created_at": __import__('datetime').datetime.now().isoformat(),
        }

        store.create_node(f"{path}::{fk_entity_name}", meta=fk_meta)

        # 添加边: table → fk
        store.add_edges([{
            "from": f"{path}::{from_table}.table",
            "type": "foreign_keys",
            "to": f"{path}::{fk_entity_name}",
        }])

        return True

    except Exception as e:
        logger.debug(f"Could not create relation: {e}")
        return False
