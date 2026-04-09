"""DB Column TopK Generator - 数据库列TopK值生成器

职责：
- 匹配所有 *.db 下的 *.*.*.col 节点（扁平结构：[表名].[列名].[类型].col）
- 将topk数据直接放入列节点的_meta.yml根级别

独立执行：
    python -m extractor.db_column_topk ./my_data
"""
import os
import logging
from typing import Optional, List, Dict, Any
from extractor.utils import VFSStorage, NodeRef, load_config

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage, k: int = 5) -> None:
    """为所有DB列生成TopK值"""
    logger.info("=== Generating DB column TopK values ===")

    # 扁平结构: *.db/_entity/*.*.*.col (e.g., "users.id.INT.col")
    for node in storage.find_nodes("*.db/_entity/*.*.*.col"):
        try:
            _generate_for_column(node, storage, k)
        except Exception as e:
            logger.warning(f"Failed to generate topk for {node.name}: {e}")


def _generate_for_column(node: NodeRef, storage: VFSStorage, k: int) -> bool:
    """为单个列生成topk数据并存入meta根级别"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    # 检查是否已处理
    if "topk" in meta:
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

    # 解析列节点名: 支持 _entity/ 子目录结构
    if db_idx + 1 < len(path_parts) and path_parts[db_idx + 1] == '_entity':
        col_node_name = path_parts[db_idx + 2].replace(".col", "")
    else:
        col_node_name = path_parts[db_idx + 1].replace(".col", "")
    col_parts = col_node_name.split(".")
    if len(col_parts) < 3:
        return False

    table_name = col_parts[0]
    col_name = col_parts[1]

    # 获取DB源路径
    db_node = NodeRef(db_rel_path, node.pontis_root)
    db_meta = storage.read_meta(db_node)
    if not db_meta:
        return False

    rel_path = db_meta.get("path")
    db_path = storage.resolve_path(rel_path) if rel_path else None
    if not db_path or not os.path.exists(db_path):
        return False

    # 计算TopK
    topk = _calculate_topk(db_path, table_name, col_name, k)
    if topk is None:
        return False

    # 将topk数据直接放入meta根级别
    meta["topk"] = topk
    storage.write_meta(node, meta)

    logger.info(f"  TopK added: {node.rel_path} ({len(topk)} items)")
    return True


def _calculate_topk(db_path: str, table: str, column: str, k: int) -> Optional[List[Dict[str, Any]]]:
    """计算最常见的K个值"""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
        total_rows = cursor.fetchone()[0]

        if total_rows == 0:
            conn.close()
            return []

        cursor.execute(f'''
            SELECT "{column}", COUNT(*) as cnt
            FROM "{table}"
            WHERE "{column}" IS NOT NULL
            GROUP BY "{column}"
            ORDER BY cnt DESC
            LIMIT {k}
        ''')

        rows = cursor.fetchall()
        conn.close()

        topk = []
        for value, count in rows:
            if isinstance(value, bytes):
                value = f"<BLOB:{len(value)}bytes>"

            topk.append({
                "value": value,
                "count": count,
                "percentage": round((count / total_rows) * 100, 2)
            })

        return topk

    except Exception as e:
        logger.debug(f"Could not calculate topk: {e}")
        return None


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate DB column TopK values")
    parser.add_argument('target', help='Directory with .pontis')
    parser.add_argument('-k', type=int, default=5, help='Number of top values (default: 5)')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    target_path = os.path.abspath(args.target)
    pontis_path = os.path.join(target_path, ".pontis")

    if not os.path.exists(pontis_path):
        print(f"Error: No .pontis found at {pontis_path}", file=sys.stderr)
        sys.exit(1)

    storage = VFSStorage(pontis_path)
    generate(storage, args.k)
    print("Done.")


if __name__ == '__main__':
    main()
