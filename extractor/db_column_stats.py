"""Column Statistics Generator - 列统计生成器

职责：
- 匹配所有 *.db 下的 *.*.*.col 节点（扁平结构：[表名].[列名].[类型].col）
- 读取父DB的source_path
- 计算统计数据并追加到_meta.yml

独立执行：
    python -m extractor.db_column_stats ./my_data
"""
import os
import logging
from typing import Optional
from extractor.utils import VFSStorage, NodeRef, Config, load_config

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有.col节点生成统计信息"""
    logger.info("=== Generating column statistics ===")

    # 扁平结构: *.db/*.*.*.col (e.g., "users.id.INT.col")
    for node in storage.find_nodes("*.db/*.*.*.col"):
        try:
            _generate_for_column(node, storage)
        except Exception as e:
            logger.warning(f"Failed to generate stats for {node.name}: {e}")


def _generate_for_column(node: NodeRef, storage: VFSStorage) -> bool:
    """为单个列生成统计"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    # 跳过已处理的
    if "cardinality" in meta:
        return False

    # 解析路径获取信息
    # 路径格式: [...]/[db_name].db/[table_name].[col_name].[type].col
    path_parts = node.rel_path.split(os.sep)
    if len(path_parts) < 2:
        return False

    # 找到.db节点位置
    db_idx = -1
    for i, part in enumerate(path_parts):
        if part.endswith('.db'):
            db_idx = i
            break

    if db_idx == -1 or db_idx + 1 >= len(path_parts):
        return False

    db_rel_path = os.sep.join(path_parts[:db_idx+1])

    # 解析列节点名: [表名].[列名].[类型].col
    col_node_name = path_parts[db_idx + 1].replace(".col", "")
    col_parts = col_node_name.split(".")
    if len(col_parts) < 3:
        return False

    table_name = col_parts[0]
    col_name = col_parts[1]
    data_type = col_parts[2] if len(col_parts) > 2 else "TEXT"

    # 获取DB源路径
    db_node = NodeRef(db_rel_path, node.pontis_root)
    db_meta = storage.read_meta(db_node)
    if not db_meta:
        return False

    rel_path = db_meta.get("path")
    db_path = storage.resolve_path(rel_path) if rel_path else None
    if not db_path or not os.path.exists(db_path):
        return False

    # 计算统计
    stats = _calculate_stats(db_path, table_name, col_name, data_type)
    if not stats:
        return False

    # 追加到meta（不覆盖原有字段）
    meta.update(stats)
    storage.write_meta(node, meta)
    logger.info(f"  Stats generated: {node.rel_path} (cardinality={stats.get('cardinality')})")
    return True


def _calculate_stats(db_path: str, table: str, column: str, data_type: str) -> Optional[dict]:
    """从数据库计算统计"""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        stats = {}

        # Row count
        cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
        total_rows = cursor.fetchone()[0]

        if total_rows == 0:
            conn.close()
            return {"cardinality": 0, "null_count": 0}

        # Cardinality
        cursor.execute(f'SELECT COUNT(DISTINCT "{column}") FROM "{table}" WHERE "{column}" IS NOT NULL')
        stats["cardinality"] = cursor.fetchone()[0]

        # Null count
        cursor.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IS NULL')
        null_count = cursor.fetchone()[0]
        stats["null_count"] = null_count
        stats["null_percentage"] = round((null_count / total_rows) * 100, 2)

        # Type-specific
        if data_type in ["INT", "INTEGER", "REAL", "FLOAT"]:
            cursor.execute(f'SELECT MIN("{column}"), MAX("{column}"), AVG("{column}") FROM "{table}" WHERE "{column}" IS NOT NULL')
            row = cursor.fetchone()
            if row:
                stats["min_value"] = row[0]
                stats["max_value"] = row[1]
                stats["mean_value"] = round(row[2], 4) if row[2] else None

        elif data_type in ["TEXT", "VARCHAR", "CHAR"]:
            cursor.execute(f'SELECT MIN(LENGTH("{column}")), MAX(LENGTH("{column}")), AVG(LENGTH("{column}")) FROM "{table}" WHERE "{column}" IS NOT NULL')
            row = cursor.fetchone()
            if row:
                stats["min_length"] = row[0]
                stats["max_length"] = row[1]
                stats["avg_length"] = round(row[2], 2) if row[2] else None

        conn.close()
        return stats

    except Exception as e:
        logger.debug(f"Could not calculate stats: {e}")
        return None


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate column statistics")
    parser.add_argument('target', help='Directory with .pontis')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    target_path = os.path.abspath(args.target)
    pontis_path = os.path.join(target_path, ".pontis")

    if not os.path.exists(pontis_path):
        print(f"Error: No .pontis found at {pontis_path}", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    storage = VFSStorage(pontis_path)
    generate(storage)
    print("Done.")


if __name__ == '__main__':
    main()
