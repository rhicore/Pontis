"""CSV Column Stats Generator - CSV列统计生成器

职责：
- 匹配所有 *.csv 和 *.tsv 节点下的 *.col 节点（扁平结构：[文件名].[列名].TEXT.col）
- 读取CSV文件计算列统计
- 追加到_meta.yml

独立执行：
    python -m extractor.csv_column_stats ./my_data
"""
import os
import logging
from typing import Optional, Dict, Any
from extractor.utils import VFSStorage, NodeRef, load_config

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有CSV/TSV文件的列生成统计"""
    logger.info("=== Generating CSV column statistics ===")

    # Entity structure: *.csv/_entity/*.*.*.col
    for node in storage.find_nodes("*.csv/_entity/*.*.*.col"):
        try:
            _generate_for_column(node, storage, delimiter=',')
        except Exception as e:
            logger.warning(f"Failed to generate stats for {node.name}: {e}")

    for node in storage.find_nodes("*.tsv/_entity/*.*.*.col"):
        try:
            _generate_for_column(node, storage, delimiter='\t')
        except Exception as e:
            logger.warning(f"Failed to generate stats for {node.name}: {e}")


def _generate_for_column(node: NodeRef, storage: VFSStorage, delimiter: str) -> bool:
    """为单个CSV列生成统计"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    # 跳过已处理的
    if "cardinality" in meta:
        return False

    # 解析路径获取信息
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
    # 路径: [csv].csv/_entity/[csv].[col_name].TEXT.col
    if csv_idx + 1 < len(path_parts) and path_parts[csv_idx + 1] == '_entity':
        col_node_name = path_parts[csv_idx + 2]  # e.g., "employees.name.TEXT.col"
    else:
        col_node_name = path_parts[csv_idx + 1]
    col_name = col_node_name.split('.')[1]  # 提取列名

    # 获取CSV源路径
    csv_node = NodeRef(csv_rel_path, node.pontis_root)
    csv_meta = storage.read_meta(csv_node)
    if not csv_meta:
        return False

    rel_path = csv_meta.get("path")
    csv_path = storage.resolve_path(rel_path) if rel_path else None
    if not csv_path or not os.path.exists(csv_path):
        return False

    # 计算统计
    stats = _calculate_stats(csv_path, col_name, delimiter)
    if stats is None:
        return False

    # 追加到meta
    meta.update(stats)
    storage.write_meta(node, meta)
    logger.info(f"  Stats generated: {node.rel_path}")
    return True


def _calculate_stats(csv_path: str, column: str, delimiter: str) -> Optional[Dict[str, Any]]:
    """计算CSV列统计"""
    try:
        import csv

        with open(csv_path, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f, delimiter=delimiter)

            all_values = []
            null_count = 0

            for row in reader:
                value = row.get(column)
                if value is None or value == '':
                    null_count += 1
                else:
                    all_values.append(value)

        if not all_values and null_count == 0:
            return None

        total = len(all_values) + null_count
        unique_values = set(all_values)

        stats = {
            "cardinality": len(unique_values),
            "null_count": null_count,
            "null_percentage": round((null_count / total) * 100, 2) if total > 0 else 0,
        }

        # 尝试数值统计
        numeric_values = []
        for v in all_values:
            try:
                numeric_values.append(float(v))
            except (ValueError, TypeError):
                pass

        if numeric_values:
            stats["min"] = min(numeric_values)
            stats["max"] = max(numeric_values)
            stats["mean"] = round(sum(numeric_values) / len(numeric_values), 4)

        return stats

    except Exception as e:
        logger.debug(f"Could not calculate stats: {e}")
        return None


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate CSV column statistics")
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
