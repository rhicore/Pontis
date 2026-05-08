"""CSV Info Generator - CSV文件信息生成器

职责：
- 匹配 *.csv/*.tsv 节点
- 添加文件级元信息（行数、列数）

独立执行：
    python -m extractor.csv_info ./my_data
"""
import logging
from storage.workspace import Workspace

logger = logging.getLogger(__name__)


def generate(workspace: Workspace) -> None:
    """为所有CSV/TSV节点生成信息"""
    logger.info("=== Generating CSV info ===")

    for ext, delim in [('.csv', ','), ('.tsv', '\t')]:
        rows = workspace.cypher(f"MATCH (n) WHERE n.name ENDS WITH '{ext}' RETURN n")
        for row in rows:
            path = row["n"]["name"]
            try:
                _generate_for_csv(path, workspace, delimiter=delim)
            except Exception as e:
                logger.warning(f"Failed to generate info for {path}: {e}")


def _generate_for_csv(path: str, workspace: Workspace, delimiter: str) -> bool:
    """为单个CSV生成信息"""
    meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": path})
    meta = meta_rows[0].get("n") if meta_rows else None
    if not meta:
        return False

    if "row_count" in meta:
        return False

    rel_path = meta.get("path")
    csv_path = workspace.resolve_data_path(rel_path) if rel_path else None
    if not csv_path or not workspace.data_exists(rel_path):
        return False

    try:
        import csv

        with open(csv_path, 'r', encoding='utf-8', errors='ignore') as f:
            # 读取表头
            reader = csv.reader(f, delimiter=delimiter)
            headers = next(reader, None)
            column_count = len(headers) if headers else 0

            # 统计行数
            row_count = sum(1 for _ in reader)

        workspace.cypher('MATCH (n {name: $name}) SET n += $props', params={"name": path, "props": {
            "row_count": row_count,
            "column_count": column_count,
        }})

        logger.info(f"  CSV info: {path} ({row_count} rows, {column_count} cols)")
        return True

    except Exception as e:
        logger.debug(f"Could not get CSV info: {e}")
        return False
