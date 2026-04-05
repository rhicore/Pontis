"""DB Table Relations Generator - 数据库表关系生成器

职责：
- 匹配 *.db/*.table 节点
- 分析该表的外键和命名约定关系
- 在表下创建 [col]__to__[target_table].[target_col].rel/ 文件夹

独立执行：
    python -m extractor.db_table_relations ./my_data
"""
import os
import logging
from typing import List, Dict
from extractor.utils import VFSStorage, NodeRef

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有表节点分析关系"""
    logger.info("=== Generating table relations ===")

    for node in storage.find_nodes("*.db/*.table"):
        try:
            _generate_for_table(node, storage)
        except Exception as e:
            logger.warning(f"Failed to generate relations for {node.name}: {e}")


def _generate_for_table(node: NodeRef, storage: VFSStorage) -> bool:
    """为单个表分析关系，在表下创建.rel文件夹"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    # 解析路径
    path_parts = node.rel_path.split(os.sep)
    if len(path_parts) < 2:
        return False

    db_rel_path = path_parts[0]
    table_name = meta.get("source_table", node.name.replace(".table", ""))

    # 获取DB路径
    db_node = NodeRef(db_rel_path, node.pontis_root)
    db_meta = storage.read_meta(db_node)
    if not db_meta:
        return False

    rel_path = db_meta.get("path")
    db_path = storage.resolve_path(rel_path) if rel_path else None
    if not db_path or not os.path.exists(db_path):
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

    # 在表下为每个关系创建.rel文件夹
    created_count = 0
    for rel in all_relations:
        if _create_relation_folder(node, rel, storage):
            created_count += 1

    if created_count > 0:
        logger.info(f"  Relations: {node.rel_path} ({created_count} relations)")
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
            # fk: (id, seq, table, from, to, on_update, on_delete, match)
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

    # 获取数据库中所有表
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


def _create_relation_folder(table_node: NodeRef, relation: Dict, storage: VFSStorage) -> bool:
    """在表下为关系创建.rel文件夹"""
    try:
        from_table = table_node.name.replace(".table", "")
        from_col = relation["from_column"]
        to_table = relation["to_table"]
        to_col = relation["to_column"]

        # 构建关系文件夹名: [col]__to__[target_table].[target_col].rel
        safe_col = from_col.replace("/", "_").replace("\\", "_")
        safe_target = f"{to_table}.{to_col}".replace("/", "_").replace("\\", "_")
        rel_folder_name = f"{safe_col}__to__{safe_target}.rel"

        rel_rel_path = os.path.join(table_node.rel_path, rel_folder_name)
        rel_node = NodeRef(rel_rel_path, table_node.pontis_root)

        # 检查是否已存在
        if storage.exists(rel_node):
            return False

        # 创建关系meta
        rel_meta = {
            "relation_type": relation["type"],
            "from_table": from_table,
            "from_column": from_col,
            "to_table": to_table,
            "to_column": to_col,
            "confidence": relation.get("confidence", 0.5)
        }

        storage.ensure_dir(rel_node.full_path)
        storage.write_meta(rel_node, rel_meta)

        return True

    except Exception as e:
        logger.debug(f"Could not create relation: {e}")
        return False


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate DB table relations")
    parser.add_argument('target', help='Directory with .pontis')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    target_path = os.path.abspath(args.target)
    pontis_path = os.path.join(target_path, ".pontis")

    if not os.path.exists(pontis_path):
        print(f"Error: No .pontis found at {pontis_path}", file=sys.stderr)
        sys.exit(1)

    storage = VFSStorage(pontis_path)
    generate(storage)
    print("Done.")


if __name__ == '__main__':
    main()
