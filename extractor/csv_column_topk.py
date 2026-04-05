"""CSV Column TopK Generator - CSV列TopK值生成器

职责：
- 匹配所有 *.csv/*.tsv 下的 *.col 节点
- 创建 .topk/ 文件夹
- 写入 _bin 文件

独立执行：
    python -m extractor.gen_csv_topk ./my_data
"""
import os
import logging

from typing import Optional, List, Dict, Any
from collections import Counter
from extractor.utils import VFSStorage, NodeRef

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage, k: int = 5) -> None:
    """为所有CSV/TSV列生成TopK值"""
    logger.info("=== Generating CSV column TopK values ===")

    for node in storage.find_nodes("*.csv/*.*.col"):
        try:
            _generate_for_column(node, storage, ',', k)
        except Exception as e:
            logger.warning(f"Failed to generate topk for {node.name}: {e}")

    for node in storage.find_nodes("*.tsv/*.*.col"):
        try:
            _generate_for_column(node, storage, '\t', k)
        except Exception as e:
            logger.warning(f"Failed to generate topk for {node.name}: {e}")


def _generate_for_column(node: NodeRef, storage: VFSStorage, delimiter: str, k: int) -> bool:
    """为单个列创建.topk/文件夹"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    topk_rel_path = os.path.join(node.rel_path, ".topk")
    topk_node = NodeRef(topk_rel_path, node.pontis_root)

    if storage.exists(topk_node):
        return False

    path_parts = node.rel_path.split(os.sep)
    if len(path_parts) < 2:
        return False

    # 找到.csv节点位置（支持嵌套路径）
    csv_idx = -1
    for i, part in enumerate(path_parts):
        if part.endswith('.csv') or part.endswith('.tsv'):
            csv_idx = i
            break

    if csv_idx == -1:
        return False

    csv_rel_path = os.sep.join(path_parts[:csv_idx+1])
    col_name = node.name.split('.')[0]

    csv_node = NodeRef(csv_rel_path, node.pontis_root)
    csv_meta = storage.read_meta(csv_node)
    if not csv_meta:
        return False

    rel_path = csv_meta.get("path")
    csv_path = storage.resolve_path(rel_path) if rel_path else None
    if not csv_path or not os.path.exists(csv_path):
        return False

    topk = _calculate_topk(csv_path, col_name, delimiter, k)
    if topk is None:
        return False

    # 创建.topk/文件夹
    storage.ensure_dir(topk_node.full_path)
    storage.write_raw(topk_node, topk)

    topk_meta = {
        
        
        "count": len(topk),
        "created_at": __import__('datetime').datetime.now().isoformat(),
    }
    storage.write_meta(topk_node, topk_meta)

    logger.info(f"  TopK created: {node.rel_path}/.topk ({len(topk)} items)")
    return True


def _calculate_topk(csv_path: str, column: str, delimiter: str, k: int) -> Optional[List[Dict[str, Any]]]:
    """计算最常见的K个值"""
    try:
        import csv

        with open(csv_path, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f, delimiter=delimiter)

            values = []
            for row in reader:
                v = row.get(column)
                if v:
                    values.append(v)

        if not values:
            return []

        counter = Counter(values)
        total = len(values)

        topk = []
        for value, count in counter.most_common(k):
            topk.append({
                "value": value,
                "count": count,
                "percentage": round((count / total) * 100, 2)
            })

        return topk

    except Exception as e:
        logger.debug(f"Could not calculate topk: {e}")
        return None


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate CSV column TopK values")
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
