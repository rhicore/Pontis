"""DB Column TopK Generator - 数据库列TopK值生成器

职责：
- 匹配所有 *.db/*.table/*.col 节点
- 创建 .topk/ 文件夹
- 写入 _raw 文件（JSON格式的TopK值）

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

    for node in storage.find_nodes("*.db/*.table/*.*.col"):
        try:
            _generate_for_column(node, storage, k)
        except Exception as e:
            logger.warning(f"Failed to generate topk for {node.name}: {e}")


def _generate_for_column(node: NodeRef, storage: VFSStorage, k: int) -> bool:
    """为单个列创建.topk/文件夹"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    # 检查是否已存在
    topk_node_name = ".topk"
    topk_rel_path = os.path.join(node.rel_path, topk_node_name)
    topk_node = NodeRef(topk_rel_path, node.pontis_root)

    if storage.exists(topk_node):
        return False

    # 解析路径获取信息
    # 路径格式: [...]/[db_name].db/[table_name].table/[col_name].[type].col
    path_parts = node.rel_path.split(os.sep)
    if len(path_parts) < 3:
        return False

    # 找到.db节点位置
    db_idx = -1
    for i, part in enumerate(path_parts):
        if part.endswith('.db'):
            db_idx = i
            break

    if db_idx == -1 or db_idx + 2 >= len(path_parts):
        return False

    db_rel_path = os.sep.join(path_parts[:db_idx+1])
    table_name = path_parts[db_idx + 1].replace(".table", "").replace(".view", "")
    col_parts = path_parts[db_idx + 2].replace(".col", "").split(".")
    col_name = col_parts[0]

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

    # 创建.topk/文件夹
    storage.ensure_dir(topk_node.full_path)

    # 写入_raw文件（JSON格式）
    storage.write_raw(topk_node, topk)

    # 写入_meta.yml（仅保留必要的count和created_at）
    topk_meta = {
        "count": len(topk),
        "created_at": __import__('datetime').datetime.now().isoformat(),
    }
    storage.write_meta(topk_node, topk_meta)

    logger.info(f"  TopK created: {node.rel_path}/.topk ({len(topk)} items)")
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
