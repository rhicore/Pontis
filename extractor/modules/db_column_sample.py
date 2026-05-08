"""DB Column Sample Generator - 数据库列采样生成器

职责：
- 匹配所有 *.db 下的列节点
- 将sample数据直接放入列节点的_meta.yml根级别

独立执行：
    python -m extractor.db_column_sample ./my_data
"""
import logging
from typing import Optional, List, Any
from storage.workspace import Workspace

logger = logging.getLogger(__name__)



def generate(workspace: Workspace, sample_size: int = 10) -> None:
    """为所有DB列生成样本"""
    logger.info("=== Generating DB column samples ===")

    for ext_suffix in [".db", ".sqlite", ".sqlite3", ".duckdb"]:
        db_rows = workspace.cypher(f"MATCH (n) WHERE n.name ENDS WITH '{ext_suffix}' RETURN n")
        for db_row in db_rows:
            db_ref = db_row["n"]["name"]
            tbl_rows = workspace.cypher(f'MATCH (d {{name: "{db_ref}"}})--(t:table) RETURN t')
            for tbl_row in tbl_rows:
                table_ref = tbl_row["t"]["name"]
                col_rows = workspace.cypher(f'MATCH (d {{name: "{db_ref}"}})--(t {{name: "{table_ref}"}})--(c:col) RETURN c')
                for col_row in col_rows:
                    col_ref = col_row["c"]["name"]
                    try:
                        _generate_for_column(col_ref, db_ref, table_ref, workspace, sample_size)
                    except Exception as e:
                        logger.warning(f"Failed to generate sample for {col_ref}: {e}")


def _generate_for_column(col_ref: str, db_ref: str, table_ref: str,
                         workspace: Workspace, sample_size: int) -> bool:
    """为单个列生成sample数据并存入meta根级别"""
    meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": col_ref})
    meta = meta_rows[0].get("n") if meta_rows else None
    if not meta:
        return False

    if "sample" in meta:
        return False

    col_name = col_ref
    table_name = table_ref
    db_meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": db_ref})
    db_meta = db_meta_rows[0].get("n") if db_meta_rows else None
    db_rel = db_meta.get("path", db_ref) if db_meta else db_ref
    if not workspace.data_exists(db_rel):
        return False

    samples = _get_samples(db_rel, table_name, col_name, sample_size, workspace)
    if samples is None:
        return False

    workspace.cypher('MATCH (n {name: $name}) SET n += $props',
                  params={"name": col_ref, "props": {"sample": samples}})
    logger.info(f"  Sample added: {col_ref} ({len(samples)} items)")
    return True


def _get_samples(db_rel: str, table: str, column: str, sample_size: int, workspace: Workspace) -> Optional[List[Any]]:
    """从数据库获取样本"""
    try:
        with workspace.open_db(db_rel) as conn:
            cursor = conn.cursor()

            cursor.execute(f'''
                SELECT DISTINCT "{column}"
                FROM "{table}"
                WHERE "{column}" IS NOT NULL
                LIMIT {sample_size}
            ''')

            rows = cursor.fetchall()

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
