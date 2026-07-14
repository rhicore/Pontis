"""Public distinct-value access used by overlap and value-domain strategies."""
from __future__ import annotations

from array import array

from extractor.utils.overlap_value_matchers import (
    _hash_index_force_rebuild,
    _hash_index_read_paths,
    _load_or_build_column_hash_index,
    _open_db_connection,
    _read_hash_array,
)


def open_database(db_connect, *, readonly: bool = True):
    return _open_db_connection(db_connect, readonly=readonly)


def load_or_build_distinct_hashes(cursor, column: dict, dialect: str) -> array:
    return _load_or_build_column_hash_index(cursor, column, dialect)


def load_cached_distinct_hashes(column: dict) -> array | None:
    """Read an existing index without opening the source database."""

    if _hash_index_force_rebuild():
        return None
    for path in _hash_index_read_paths(column):
        if not path.exists():
            continue
        try:
            return _read_hash_array(path)
        except (OSError, ValueError):
            continue
    return None
