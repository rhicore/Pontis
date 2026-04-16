"""DB Column LSH Index — 为数据库列构建 LSH 索引

流式读取 SQLite 列数据，为每个 .col 实体构建 hash bucket + KLL 索引。
通过 store.create_file() 分配路径并注册 KG 节点。
"""
import os
import logging

from storage import Store
from extractor.modules._lsh_index import LSHIndexWriter, choose_bucket_count

logger = logging.getLogger(__name__)

_FETCH_SIZE = 10000
DB_EXTENSIONS = ["*.db", "*.sqlite", "*.sqlite3", "*.duckdb"]

NUMERIC_TYPES = {'INT', 'INTEGER', 'REAL', 'FLOAT', 'DOUBLE', 'NUMERIC'}


def _parse_col_ref(ref: str):
    """解析 col ref → (db_file, table, col, dtype)"""
    parts = ref.split("::", 1)
    if len(parts) != 2:
        return None
    db_file, entity = parts
    # entity: table.col.TYPE.col → strip .col, then split
    if not entity.endswith('.col'):
        return None
    core = entity[:-4]  # table.col.TYPE
    segments = core.rsplit('.', 2)
    if len(segments) != 3:
        return None
    table, col_name, dtype = segments
    return db_file, table, col_name, dtype


def _build_index(ref: str, store: Store) -> None:
    """为单个列构建 LSH 索引。"""
    parsed = _parse_col_ref(ref)
    if parsed is None:
        return
    db_file, table, col_name, dtype = parsed

    abs_db = os.path.join(store.project_path, db_file)
    if not os.path.isfile(abs_db):
        return

    import sqlite3

    # 从元数据获取 cardinality 以决定 bucket 数
    cardinality = None
    meta = store._get_stored_meta(ref)
    if meta:
        cardinality = meta.get('cardinality')

    is_numeric = dtype.upper() in NUMERIC_TYPES
    num_buckets = choose_bucket_count(cardinality)

    writer = LSHIndexWriter(num_buckets=num_buckets, is_numeric=is_numeric)

    try:
        conn = sqlite3.connect(abs_db)
        cursor = conn.cursor()
        cursor.execute(f'SELECT "{col_name}" FROM "{table}"')

        while True:
            rows = cursor.fetchmany(_FETCH_SIZE)
            if not rows:
                break
            for (val,) in rows:
                writer.add_value(val)

        conn.close()
    except Exception as e:
        logger.warning(f"Failed to index {ref}: {e}")
        return

    idx_ref = ref + ".idx"
    file_path = store.create_file(
        ref=idx_ref,
        meta={
            "index_version": 1,
            "index_buckets": num_buckets,
            "index_distinct": writer._distinct_count,
            "index_has_kll": writer._kll is not None,
        },
        parent_ref=ref,
    )
    writer.write(file_path)


def generate(store: Store) -> None:
    """为所有 DB 列构建 LSH 索引。"""
    logger.info("=== Generating DB column LSH indexes ===")

    count = 0
    skipped = 0
    for ext in DB_EXTENSIONS:
        for ref in store.find_nodes(f"{ext}::*.*.*.col"):
            # 已有索引节点则跳过
            idx_refs = store.find_connected(ref, edge_type="contains", pattern="*.idx")
            if idx_refs:
                skipped += 1
                continue
            _build_index(ref, store)
            count += 1

    logger.info(f"  Indexed {count} columns, skipped {skipped} existing")
