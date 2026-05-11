"""CSV/TSV Basic Generator - 表格实体展开

职责：
1. 通过 Cypher 发现所有 CSV/TSV 文件
2. 写入文件级属性（delimiter 等），自动实体化
3. 展开列实体
"""
import os
import logging
from datetime import datetime
from storage.workspace import Workspace
from extractor.modules.utils.src import file_exists, open_text_file

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


def _process_table(rel_path: str, workspace: Workspace, delimiter: str = ',') -> None:
    """处理单个表格文件：写入属性（自动实体化）+ 展开列实体"""
    if not file_exists(workspace, rel_path):
        return

    basename = os.path.basename(rel_path)
    workspace.cypher('MATCH (n {name: $name}) SET n += $props', params={"name": basename, "props": {
        "created_at": datetime.now().isoformat(),
        "delimiter": "," if delimiter == ',' else "\\t",
        "path": rel_path,
    }})
    logger.info(f"  CSV properties: {basename}")

    # 读取表头并推断类型
    import csv
    with open_text_file(workspace, rel_path, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.reader(f, delimiter=delimiter)
        headers = next(reader, None)
        if not headers:
            return
        sample_rows = []
        for i, row in enumerate(reader):
            if i >= 100:
                break
            sample_rows.append(row)

    ts = datetime.now().isoformat()
    for col_idx, col_name in enumerate(headers):
        safe_col = col_name.replace("/", "_").replace("\\", "_").replace(".", "_")
        col_type = _infer_type(sample_rows, col_idx)
        workspace.cypher(f'CREATE (c:col:{col_type} {{name: "{safe_col}", created_at: "{ts}", col_type: "{col_type}"}})')
        workspace.cypher(f'MATCH (f {{name: "{basename}"}}),(c {{name: "{safe_col}"}}) CREATE (f)--(c)')

    logger.info(f"  Entity: {basename} ({len(headers)} columns)")


def _discover_csv_files(workspace: Workspace) -> list:
    """通过统一索引发现所有 CSV/TSV 文件（含虚拟实体）。"""
    exts = ('.csv', '.tsv')
    results = []
    seen = set()
    rows = workspace.cypher("MATCH (n) RETURN n")
    for row in rows:
        props = row.get("n", {})
        name = props.get("name", "")
        if not any(name.endswith(ext) for ext in exts):
            continue
        rel_path = props.get("path", name)
        if rel_path not in seen:
            seen.add(rel_path)
            results.append(rel_path)
    return results


def generate(workspace: Workspace) -> None:
    """发现所有 CSV/TSV 文件，写入属性并展开列实体"""
    logger.info("=== Generating CSV/TSV entities ===")

    count = 0
    for path in _discover_csv_files(workspace):
        if "database_description" in path:
            continue
        delimiter = '\t' if path.endswith('.tsv') else ','
        try:
            _process_table(path, workspace, delimiter=delimiter)
            count += 1
        except Exception as e:
            logger.warning(f"Failed to process {path}: {e}")

    logger.info(f"  Processed {count} CSV/TSV files")
