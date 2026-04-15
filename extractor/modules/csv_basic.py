"""CSV/TSV Basic Generator - 表格文件发现与实体展开

职责：
1. 通过 store.find_nodes() 发现所有 CSV/TSV 文件（含虚节点）
2. 为未索引的文件创建节点（含 _inode）
3. 展开列实体
"""
import os
import logging
from datetime import datetime
from storage import Store

logger = logging.getLogger(__name__)


def _infer_type(sample_rows: list, col_idx: int) -> str:
    """从前 N 行推断列类型：INT / FLOAT / TEXT"""
    non_empty = []
    for row in sample_rows:
        if col_idx < len(row):
            val = row[col_idx].strip()
            if val:
                non_empty.append(val)

    if not non_empty:
        return "TEXT"

    all_int = True
    all_float = True

    for val in non_empty:
        if all_int:
            try:
                int(val)
            except ValueError:
                all_int = False
        if all_float:
            try:
                float(val)
            except ValueError:
                all_float = False

        if not all_int and not all_float:
            break

    if all_int:
        return "INT"
    if all_float:
        return "FLOAT"
    return "TEXT"


def _process_table(rel_path: str, store: Store, delimiter: str = ',') -> None:
    """处理单个表格文件：创建文件节点 + 展开列实体"""
    abs_path = os.path.join(store.project_path, rel_path)
    if not os.path.exists(abs_path):
        return

    stat = os.stat(abs_path)

    # 创建文件节点
    meta = {
        "path": rel_path,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "created_at": datetime.now().isoformat(),
        "delimiter": "," if delimiter == ',' else "\\t",
    }
    store.create_node(rel_path, meta=meta)
    logger.info(f"  Created file node: {rel_path}")

    # 读取表头并推断类型
    import csv
    with open(abs_path, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.reader(f, delimiter=delimiter)
        headers = next(reader, None)
        if not headers:
            return
        sample_rows = []
        for i, row in enumerate(reader):
            if i >= 100:
                break
            sample_rows.append(row)

    stem = os.path.splitext(os.path.basename(rel_path))[0]
    for col_idx, col_name in enumerate(headers):
        safe_col = col_name.replace("/", "_").replace("\\", "_").replace(".", "_")
        col_type = _infer_type(sample_rows, col_idx)
        col_entity_name = f"{stem}.{safe_col}.{col_type}.col"
        store.create_node(f"{rel_path}::{col_entity_name}",
                          meta={"created_at": datetime.now().isoformat()})

    logger.info(f"  Entity: {rel_path} ({len(headers)} columns)")


def generate(store: Store) -> None:
    """发现所有 CSV/TSV 文件，创建文件节点并展开列实体"""
    logger.info("=== Generating CSV/TSV entities ===")

    count = 0
    for pattern in ["**/*.csv", "**/*.tsv"]:
        delimiter = '\t' if pattern.endswith('.tsv') else ','
        for path in store.find_nodes(pattern):
            if store.node_exists(path):
                continue
            try:
                _process_table(path, store, delimiter=delimiter)
                count += 1
            except Exception as e:
                logger.warning(f"Failed to process {path}: {e}")

    logger.info(f"  Processed {count} new CSV/TSV files")
