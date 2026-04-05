"""DB Column Sample Generator - 数据库列采样生成器

职责：
- 匹配所有 *.db/*.table/*.col 节点
- 创建 .sample/ 文件夹
- 写入 _raw 文件（JSON格式的采样值）

独立执行：
    python -m extractor.db_column_sample ./my_data
"""
import os
import logging
from typing import Optional, List, Any
from extractor.utils import VFSStorage, NodeRef, load_config

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage, sample_size: int = 10) -> None:
    """为所有DB列生成样本"""
    logger.info("=== Generating DB column samples ===")

    for node in storage.find_nodes("*.db/*.table/*.*.col"):
        try:
            _generate_for_column(node, storage, sample_size)
        except Exception as e:
            logger.warning(f"Failed to generate sample for {node.name}: {e}")


def _generate_for_column(node: NodeRef, storage: VFSStorage, sample_size: int) -> bool:
    """为单个列创建.sample/文件夹"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    # 检查是否已存在
    sample_node_name = ".sample"
    sample_rel_path = os.path.join(node.rel_path, sample_node_name)
    sample_node = NodeRef(sample_rel_path, node.pontis_root)

    if storage.exists(sample_node):
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

    # 生成样本
    samples = _get_samples(db_path, table_name, col_name, sample_size)
    if samples is None:
        return False

    # 创建.sample/文件夹
    storage.ensure_dir(sample_node.full_path)

    # 写入_raw文件（JSON格式）
    storage.write_raw(sample_node, samples)

    # 写入_meta.yml（仅保留必要的count和created_at）
    sample_meta = {
        "count": len(samples),
        "created_at": __import__('datetime').datetime.now().isoformat(),
    }
    storage.write_meta(sample_node, sample_meta)

    logger.info(f"  Sample created: {node.rel_path}/.sample ({len(samples)} items)")
    return True


def _get_samples(db_path: str, table: str, column: str, sample_size: int) -> Optional[List[Any]]:
    """从数据库获取样本"""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(f'''
            SELECT DISTINCT "{column}"
            FROM "{table}"
            WHERE "{column}" IS NOT NULL
            LIMIT {sample_size}
        ''')

        rows = cursor.fetchall()
        conn.close()

        samples = []
        for row in rows:
            value = row[0]
            if isinstance(value, bytes):
                samples.append(f"<BLOB:{len(value)}bytes>")
            else:
                samples.append(value)

        return samples

    except Exception as e:
        logger.debug(f"Could not get samples: {e}")
        return None


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate DB column samples")
    parser.add_argument('target', help='Directory with .pontis')
    parser.add_argument('-n', '--size', type=int, default=10, help='Sample size (default: 10)')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    target_path = os.path.abspath(args.target)
    pontis_path = os.path.join(target_path, ".pontis")

    if not os.path.exists(pontis_path):
        print(f"Error: No .pontis found at {pontis_path}", file=sys.stderr)
        sys.exit(1)

    storage = VFSStorage(pontis_path)
    generate(storage, args.size)
    print("Done.")


if __name__ == '__main__':
    main()
