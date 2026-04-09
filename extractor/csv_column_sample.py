"""CSV Column Sample Generator - CSV列采样生成器

职责：
- 匹配所有 *.csv/*.tsv 下的 *.col 节点（扁平结构：[文件名].[列名].TEXT.col）
- 将sample数据直接放入列节点的_meta.yml根级别

独立执行：
    python -m extractor.csv_column_sample ./my_data
"""
import os
import logging

from typing import Optional, List, Any
from extractor.utils import VFSStorage, NodeRef

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage, sample_size: int = 10) -> None:
    """为所有CSV/TSV列生成样本"""
    logger.info("=== Generating CSV column samples ===")

    # Entity structure: *.csv/_entity/*.*.*.col
    for node in storage.find_nodes("*.csv/_entity/*.*.*.col"):
        try:
            _generate_for_column(node, storage, ',', sample_size)
        except Exception as e:
            logger.warning(f"Failed to generate sample for {node.name}: {e}")

    for node in storage.find_nodes("*.tsv/_entity/*.*.*.col"):
        try:
            _generate_for_column(node, storage, '\t', sample_size)
        except Exception as e:
            logger.warning(f"Failed to generate sample for {node.name}: {e}")


def _generate_for_column(node: NodeRef, storage: VFSStorage, delimiter: str, sample_size: int) -> bool:
    """为单个列生成sample数据并存入meta根级别"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    # 检查是否已处理
    if "sample" in meta:
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

    # 解析列名：支持 _entity/ 子目录结构
    if csv_idx + 1 < len(path_parts) and path_parts[csv_idx + 1] == '_entity':
        col_node_name = path_parts[csv_idx + 2]
    else:
        col_node_name = path_parts[csv_idx + 1]
    col_name = col_node_name.split('.')[1]

    csv_node = NodeRef(csv_rel_path, node.pontis_root)
    csv_meta = storage.read_meta(csv_node)
    if not csv_meta:
        return False

    rel_path = csv_meta.get("path")
    csv_path = storage.resolve_path(rel_path) if rel_path else None
    if not csv_path or not os.path.exists(csv_path):
        return False

    samples = _get_samples(csv_path, col_name, delimiter, sample_size)
    if samples is None:
        return False

    # 将sample数据直接放入meta根级别
    meta["sample"] = samples
    storage.write_meta(node, meta)

    logger.info(f"  Sample added: {node.rel_path} ({len(samples)} items)")
    return True


def _get_samples(csv_path: str, column: str, delimiter: str, sample_size: int) -> Optional[List[Any]]:
    """从CSV获取样本"""
    try:
        import csv

        with open(csv_path, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f, delimiter=delimiter)

            seen = set()
            samples = []

            for row in reader:
                value = row.get(column)
                if value and value not in seen and len(samples) < sample_size:
                    samples.append(value)
                    seen.add(value)
                if len(samples) >= sample_size:
                    break

        return samples

    except Exception as e:
        logger.debug(f"Could not get samples: {e}")
        return None


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate CSV column samples")
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
