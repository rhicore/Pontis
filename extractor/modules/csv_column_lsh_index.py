"""CSV Column LSH Index — 为 CSV/TSV 列构建 LSH 索引

流式读取 CSV 文件，为每个 .col 实体构建 hash bucket + KLL 索引。
索引文件存储在 .pontis/cache/，索引信息记录在列实体的 meta 中。
"""
import csv
import os
import logging

from storage import Store
from extractor.modules.utils.lsh_index import LSHIndexWriter, choose_bucket_count

logger = logging.getLogger(__name__)

NUMERIC_TYPES = {'INT', 'INTEGER', 'REAL', 'FLOAT', 'DOUBLE', 'NUMERIC'}


def _parse_col_ref(ref: str):
    """解析 col ref → (csv_file, col_name, dtype)"""
    parts = ref.split("::", 1)
    if len(parts) != 2:
        return None
    csv_file, entity = parts
    if not entity.endswith('.col'):
        return None
    core = entity[:-4]
    segments = core.rsplit('.', 2)
    if len(segments) != 3:
        return None
    _, col_name, dtype = segments
    return csv_file, col_name, dtype


def _build_index(ref: str, store: Store, delimiter: str = ',') -> None:
    """为单个 CSV 列构建 LSH 索引。"""
    parsed = _parse_col_ref(ref)
    if parsed is None:
        return
    csv_file, col_name, dtype = parsed

    abs_csv = os.path.join(store.project_path, csv_file)
    if not os.path.isfile(abs_csv):
        return

    cardinality = None
    meta = store._get_stored_meta(ref)
    if meta:
        cardinality = meta.get('cardinality')

    is_numeric = dtype.upper() in NUMERIC_TYPES
    num_buckets = choose_bucket_count(cardinality)

    writer = LSHIndexWriter(num_buckets=num_buckets, is_numeric=is_numeric)

    try:
        with open(abs_csv, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            for row in reader:
                val = row.get(col_name, '').strip()
                if not val:
                    writer.add_value(None)
                    continue
                if is_numeric:
                    try:
                        if '.' in val:
                            writer.add_value(float(val))
                        else:
                            writer.add_value(int(val))
                    except ValueError:
                        writer.add_value(val)
                else:
                    writer.add_value(val)
    except Exception as e:
        logger.warning(f"Failed to index {ref}: {e}")
        return

    # 存储索引文件到 .pontis/cache/lsh/
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


def generate(store: Store) -> None:
    """为所有 CSV/TSV 列构建 LSH 索引。"""
    logger.info("=== Generating CSV/TSV column LSH indexes ===")

    count = 0
    skipped = 0

    for pattern, delimiter in [("**/*.csv", ','), ("**/*.tsv", '\t')]:
        for ref in store.find_nodes(f"{pattern.split('/')[-1]}::*.*.*.col"):
            col_meta = store._get_stored_meta(ref) or {}
            if col_meta.get("_index"):
                skipped += 1
                continue
            _build_index(ref, store, delimiter=delimiter)
            count += 1

    logger.info(f"  Indexed {count} columns, skipped {skipped} existing")
