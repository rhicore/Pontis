"""DB Column LSH Index — 为数据库列构建 LSH 索引

流式读取 SQLite 列数据，为每个 .col 实体构建 hash bucket + KLL 索引。
索引文件存储在 .pontis/cache/，索引信息记录在列实体的 meta 中。
"""
import os
import logging

from storage import Store
from extractor.modules.utils.lsh_index import LSHIndexWriter, choose_bucket_count

logger = logging.getLogger(__name__)

_FETCH_SIZE = 5000


def _build_index(ref: str, store: Store) -> None:
    """为单个 .col 实体构建 LSH 索引。"""
    col_meta = store._get_stored_meta(ref)
    if not col_meta:
        return

    parsed = _parse_col_ref(ref)
    if not parsed:
        return

    db_file, table, col_name, dtype = parsed
    db_meta = store._get_stored_meta(db_file)
    if not db_meta:
        return

    db_path = db_meta.get("path", db_file)
    abs_path = os.path.join(store.project_path, db_path)
    if not os.path.isfile(abs_path):
        return

    is_numeric = dtype in ("INT", "INTEGER", "REAL", "FLOAT", "NUMERIC", "BIGINT",
                            "SMALLINT", "TINYINT", "DOUBLE", "DECIMAL")
    num_buckets = choose_bucket_count(col_meta.get("cardinality", 100))

    writer = LSHIndexWriter(
        num_buckets=num_buckets,
        is_numeric=is_numeric,
        top_k=100 if is_numeric else 50,
    )

    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{abs_path}?mode=ro", uri=True)
        cursor = conn.execute(f'SELECT "{col_name}" FROM "{table}"')
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

    # 存储索引文件到 .pontis/cache/lsh/，索引信息写入列实体 meta
    ent_id = store.resolve_ref(ref)[0]
    cache_file = store.cache_path("lsh", f"{ent_id}.lsh")

    writer.write(cache_file)

    store.set_meta(ref, {
        "_index": {
            "version": 1,
            "buckets": num_buckets,
            "distinct": writer._distinct_count,
            "has_kll": writer._kll is not None,
        },
    })


def _parse_col_ref(ref: str):
    """解析 col ref → (db_file, table, col_name, dtype)"""
    parts = ref.split("::", 1)
    if len(parts) != 2:
        return None
    db_file, entity = parts
    if not entity.endswith('.col'):
        return None
    core = entity[:-4]  # table.col.TYPE
    segments = core.rsplit('.', 2)
    if len(segments) != 3:
        return None
    table, col_name, dtype = segments
    return db_file, table, col_name, dtype


def generate(store: Store) -> None:
    """为所有 DB 列构建 LSH 索引。"""
    logger.info("=== Generating DB column LSH indexes ===")

    count = 0
    skipped = 0
    for ext in DB_EXTENSIONS:
        for ref in store.find_nodes(f"{ext}::*.*.*.col"):
            # 已有索引则跳过
            col_meta = store._get_stored_meta(ref) or {}
            if col_meta.get("_index"):
                skipped += 1
                continue
            _build_index(ref, store)
            count += 1

    logger.info(f"  Indexed {count} columns, skipped {skipped} existing")


DB_EXTENSIONS = ["*.db", "*.sqlite", "*.sqlite3", "*.duckdb"]
