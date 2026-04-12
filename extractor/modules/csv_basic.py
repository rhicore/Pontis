"""CSV/TSV Basic Generator - 表格文件实体展开器

职责：
- 匹配 *.csv 和 *.tsv 节点
- 读取表头
- 在 _entity/ 下创建 .col/ 子节点

独立执行：
    python -m extractor.csv_basic ./my_data
"""
import os
import logging
from storage import Store

logger = logging.getLogger(__name__)


def generate(store: Store) -> None:
    """为所有 CSV/TSV 节点展开列实体"""
    logger.info("=== Generating CSV/TSV entities ===")

    for path in store.find_nodes("*.csv"):
        try:
            _expand_table(path, store, delimiter=',')
        except Exception as e:
            logger.warning(f"Failed to expand CSV {path}: {e}")

    for path in store.find_nodes("*.tsv"):
        try:
            _expand_table(path, store, delimiter='\t')
        except Exception as e:
            logger.warning(f"Failed to expand TSV {path}: {e}")


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


def _expand_table(path: str, store: Store, delimiter: str = ',') -> None:
    """展开 CSV/TSV 为列实体

    结构：
    _entity/
        [文件名].[列名].[推断类型].col/
    """
    meta = store.get_meta(path)
    rel_path = meta.get("path") if meta else None
    file_path = os.path.join(store.project_path, rel_path) if rel_path else None
    if not file_path or not os.path.exists(file_path):
        return

    import csv
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.reader(f, delimiter=delimiter)
        headers = next(reader, None)
        if not headers:
            return
        # 采样前 100 行用于类型推断
        sample_rows = []
        for i, row in enumerate(reader):
            if i >= 100:
                break
            sample_rows.append(row)

    stem = os.path.splitext(os.path.basename(path))[0]
    for col_idx, col_name in enumerate(headers):
        safe_col = col_name.replace("/", "_").replace("\\", "_").replace(".", "_")
        col_type = _infer_type(sample_rows, col_idx)
        col_entity_name = f"{stem}.{safe_col}.{col_type}.col"
        store.create_node(f"{path}::{col_entity_name}",
                            meta={"created_at": __import__('datetime').datetime.now().isoformat()})

    logger.info(f"  Entity: {path}/_entity/ ({len(headers)} columns)")
