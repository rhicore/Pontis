"""Approximate DB column profiler.

This extractor only uses database handles exposed by graph entities. A database
node must expose ``_db_connect``/``db_connect`` through the storage layer; table
and column discovery comes from the KG.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Optional

from datasketches import cpc_sketch

from extractor.utils.refs import set_entity_meta
from storage.workspace import Workspace

logger = logging.getLogger(__name__)

_NUMERIC_TYPES = {"INT", "INTEGER", "REAL", "FLOAT", "DOUBLE", "DECIMAL", "NUMBER", "NUMERIC"}
_TEXT_TYPES = {"TEXT", "VARCHAR", "CHAR", "STRING"}
_CPC_LG_K = 11
_DEFAULT_SAMPLE_SIZE = 10
_DEFAULT_TOPK = 5
_DEFAULT_MAX_WORKERS = 4


@dataclass(frozen=True)
class _ColumnTask:
    col_ref: str
    db_ref: str
    db_connect: Any
    dialect: str
    schema_name: str
    table_name: str
    col_name: str
    data_type: str


def generate(
    workspace: Workspace,
    sample_size: int = _DEFAULT_SAMPLE_SIZE,
    topk_size: int = _DEFAULT_TOPK,
    max_workers: int | None = None,
) -> None:
    """Generate approximate profile fields for all DB columns."""
    logger.info("=== Generating approximate DB column profiles ===")
    worker_count = _resolve_max_workers(max_workers)
    tasks = _load_column_tasks(workspace)

    if not tasks:
        logger.info("  No DB columns need approximate profiles")
        return

    logger.info("  Profiling %s columns with %s workers", len(tasks), worker_count)
    if worker_count <= 1:
        for task in tasks:
            _run_and_write_task(task, workspace, sample_size=sample_size, topk_size=topk_size)
        return

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                _profile_column_task,
                task,
                sample_size=sample_size,
                topk_size=topk_size,
            ): task
            for task in tasks
        }
        for future in as_completed(futures):
            task = futures[future]
            try:
                stats = future.result()
            except Exception as e:
                logger.warning("Failed to generate approximate profile for %s: %s", task.col_ref, e)
                continue
            _write_profile(workspace, task.col_ref, stats)


def _resolve_max_workers(max_workers: int | None) -> int:
    if max_workers is not None:
        return max(1, int(max_workers))
    raw = os.environ.get("PONTIS_DB_COLUMN_STATS_WORKERS")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            logger.warning("Invalid PONTIS_DB_COLUMN_STATS_WORKERS=%r, using default", raw)
    return _DEFAULT_MAX_WORKERS


def _load_column_tasks(workspace: Workspace) -> list[_ColumnTask]:
    tasks: list[_ColumnTask] = []
    db_rows = workspace.cypher(
        """
        MATCH (d:db)
        WITH d, coalesce(d._db_connect, d.db_connect) AS db_connect
        WHERE db_connect IS NOT NULL
        RETURN d, db_connect
        """
    )
    for db_row in db_rows:
        db_node = db_row.get("d") or {}
        db_connect = db_row.get("db_connect")
        if not callable(db_connect):
            continue
        db_ref = _node_ref(db_node)
        if not db_ref:
            continue
        dialect = str(getattr(db_connect, "dialect", "") or db_node.get("dialect") or "sqlite").lower()
        for col in _load_db_columns(workspace, db_ref):
            if not _needs_profile(col):
                continue
            tasks.append(
                _ColumnTask(
                    col_ref=col["col_ref"],
                    db_ref=db_ref,
                    db_connect=db_connect,
                    dialect=dialect,
                    schema_name=col.get("schema_name", ""),
                    table_name=col["table_name"],
                    col_name=col["col_name"],
                    data_type=_normalize_type(col.get("data_type", "")),
                )
            )
    return tasks


def _load_db_columns(workspace: Workspace, db_ref: str) -> list[dict]:
    rows = workspace.cypher(
        """
        MATCH (d:db)-[:RELATED_TO*1..3]-(c:col)
        WHERE coalesce(d._ref, d.ref, d.path, d.name) = $db_ref
        MATCH (r)-[:RELATED_TO]-(c)
        WHERE r:table OR r:view
        OPTIONAL MATCH (s:schema)-[:RELATED_TO]-(r)
        RETURN DISTINCT c, r, s
        """,
        params={"db_ref": db_ref},
    )
    columns: dict[str, dict] = {}
    for row in rows:
        col = row.get("c") or {}
        rel = row.get("r") or {}
        schema = row.get("s") or {}
        col_ref = _node_ref(col)
        table_name = rel.get("table_name") or rel.get("name")
        col_name = col.get("column_name") or col.get("name")
        if not col_ref or not table_name or not col_name:
            continue
        columns[col_ref] = {
            **col,
            "col_ref": col_ref,
            "table_ref": _node_ref(rel),
            "table_name": str(table_name),
            "schema_name": str(schema.get("schema_name") or schema.get("name") or rel.get("schema_name") or ""),
            "col_name": str(col_name),
            "data_type": col.get("data_type") or col.get("col_type") or "",
        }
    return list(columns.values())


def _node_ref(node: dict) -> str:
    return str(node.get("_ref") or node.get("ref") or node.get("path") or node.get("name") or "")


def _needs_profile(meta: dict) -> bool:
    required = ("cardinality_method", "sample", "topk")
    return any(key not in meta for key in required)


def _normalize_type(data_type: str) -> str:
    text = str(data_type or "").upper()
    return text.split("(", 1)[0].strip()


def _run_and_write_task(
    task: _ColumnTask,
    workspace: Workspace,
    *,
    sample_size: int,
    topk_size: int,
) -> None:
    try:
        stats = _profile_column_task(task, sample_size=sample_size, topk_size=topk_size)
    except Exception as e:
        logger.warning("Failed to generate approximate profile for %s: %s", task.col_ref, e)
        return
    _write_profile(workspace, task.col_ref, stats)


def _write_profile(workspace: Workspace, col_ref: str, stats: Optional[dict]) -> None:
    if not stats:
        return

    set_entity_meta(workspace, col_ref, stats)
    logger.info(
        "  Profiled: %s (cardinality≈%s, sample=%s, topk=%s)",
        col_ref,
        stats.get("cardinality"),
        len(stats.get("sample", [])),
        len(stats.get("topk", [])),
    )


def _generate_for_column(
    col_ref: str,
    db_ref: str,
    table_ref: str,
    workspace: Workspace,
    *,
    sample_size: int,
    topk_size: int,
) -> bool:
    """Compatibility path for direct callers that still expect one-column profiling."""
    db_rows = workspace.cypher(
        """
        MATCH (d:db)
        WHERE coalesce(d._ref, d.ref, d.path, d.name) = $db_ref
        RETURN d, coalesce(d._db_connect, d.db_connect) AS db_connect
        """,
        params={"db_ref": db_ref},
    )
    if not db_rows or not callable(db_rows[0].get("db_connect")):
        return False
    db_node = db_rows[0].get("d") or {}
    dialect = str(getattr(db_rows[0]["db_connect"], "dialect", "") or db_node.get("dialect") or "sqlite").lower()
    col_rows = workspace.cypher(
        """
        MATCH (r)-[:RELATED_TO]-(c:col)
        WHERE coalesce(r._ref, r.ref, r.path, r.name) = $table_ref
          AND coalesce(c._ref, c.ref, c.path, c.name) = $col_ref
        OPTIONAL MATCH (s:schema)-[:RELATED_TO]-(r)
        RETURN c, r, s
        """,
        params={"table_ref": table_ref, "col_ref": col_ref},
    )
    if not col_rows:
        return False
    col = _load_db_columns_from_row(col_rows[0])
    if not col or not _needs_profile(col):
        return False
    task = _ColumnTask(
        col_ref=col_ref,
        db_ref=db_ref,
        db_connect=db_rows[0]["db_connect"],
        dialect=dialect,
        schema_name=col.get("schema_name", ""),
        table_name=col["table_name"],
        col_name=col["col_name"],
        data_type=_normalize_type(col.get("data_type", "")),
    )
    stats = _profile_column_task(task, sample_size=sample_size, topk_size=topk_size)
    if not stats:
        return False
    _write_profile(workspace, col_ref, stats)
    return True


def _load_db_columns_from_row(row: dict) -> dict | None:
    col = row.get("c") or {}
    rel = row.get("r") or {}
    schema = row.get("s") or {}
    table_name = rel.get("table_name") or rel.get("name")
    col_name = col.get("column_name") or col.get("name")
    if not table_name or not col_name:
        return None
    return {
        **col,
        "table_name": str(table_name),
        "schema_name": str(schema.get("schema_name") or schema.get("name") or rel.get("schema_name") or ""),
        "col_name": str(col_name),
        "data_type": col.get("data_type") or col.get("col_type") or "",
    }


def _profile_column_task(
    task: _ColumnTask,
    *,
    sample_size: int,
    topk_size: int,
) -> Optional[dict]:
    try:
        conn = _connect(task.db_connect, readonly=True)
        try:
            cursor = conn.cursor()
            table_sql = _qualified_table_sql(task.schema_name, task.table_name, task.dialect)
            column_sql = _quote_identifier(task.col_name)
            cursor.execute(f"SELECT {column_sql} FROM {table_sql}")
            return _profile_cursor(cursor, task.data_type, sample_size=sample_size, topk_size=topk_size)
        finally:
            conn.close()
    except Exception as e:
        logger.debug("Could not profile column %s: %s", task.col_ref, e)
        return None


def _connect(db_connect, *, readonly: bool):
    try:
        return db_connect(readonly=readonly)
    except TypeError:
        return db_connect()


def _qualified_table_sql(schema_name: str, table_name: str, dialect: str) -> str:
    if schema_name and dialect not in {"sqlite", "duckdb"}:
        return f"{_quote_identifier(schema_name)}.{_quote_identifier(table_name)}"
    return _quote_identifier(table_name)


def _quote_identifier(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _profile_cursor(
    cursor,
    data_type: str,
    *,
    sample_size: int,
    topk_size: int,
) -> Optional[dict]:
    sketch = cpc_sketch(_CPC_LG_K)
    total_rows = 0
    null_count = 0

    numeric_count = 0
    numeric_sum = 0.0
    min_value = None
    max_value = None

    text_count = 0
    text_len_sum = 0
    min_length = None
    max_length = None

    sample = []
    sample_seen = set()
    topk_counter = _SpaceSavingCounter(max(topk_size * 4, 16))

    for (value,) in cursor:
        total_rows += 1
        if value is None:
            null_count += 1
            continue

        sketch.update(_stable_token(value))
        topk_counter.offer(_normalize_value(value))

        sample_token = _sample_token(value)
        if len(sample) < sample_size and sample_token not in sample_seen:
            sample_seen.add(sample_token)
            sample.append(_normalize_value(value))

        if data_type in _NUMERIC_TYPES:
            try:
                num = float(value)
            except (TypeError, ValueError):
                continue
            numeric_count += 1
            numeric_sum += num
            min_value = num if min_value is None else min(min_value, num)
            max_value = num if max_value is None else max(max_value, num)
        elif data_type in _TEXT_TYPES:
            text = str(value)
            text_len = len(text)
            text_count += 1
            text_len_sum += text_len
            min_length = text_len if min_length is None else min(min_length, text_len)
            max_length = text_len if max_length is None else max(max_length, text_len)

    if total_rows == 0:
        return {
            "cardinality": 0,
            "cardinality_lower_bound": 0,
            "cardinality_upper_bound": 0,
            "cardinality_method": "cpc_sketch",
            "null_count": 0,
            "null_percentage": 0.0,
            "sample": [],
            "sample_method": "single_pass_distinct_prefix",
            "topk": [],
            "topk_method": "space_saving",
        }

    stats = {
        "cardinality": int(round(sketch.get_estimate())),
        "cardinality_lower_bound": int(round(sketch.get_lower_bound(1))),
        "cardinality_upper_bound": int(round(sketch.get_upper_bound(1))),
        "cardinality_method": "cpc_sketch",
        "null_count": null_count,
        "null_percentage": round((null_count / total_rows) * 100, 2),
        "sample": sample,
        "sample_method": "single_pass_distinct_prefix",
        "topk": topk_counter.to_meta(topk_size, total_rows),
        "topk_method": "space_saving",
    }

    if data_type in _NUMERIC_TYPES and numeric_count > 0:
        stats["min_value"] = _normalize_number(min_value)
        stats["max_value"] = _normalize_number(max_value)
        stats["mean_value"] = round(numeric_sum / numeric_count, 4)
    elif data_type in _TEXT_TYPES and text_count > 0:
        stats["min_length"] = min_length
        stats["max_length"] = max_length
        stats["avg_length"] = round(text_len_sum / text_count, 2)

    return stats


class _SpaceSavingCounter:
    """Approximate heavy-hitter counter for one-pass top-k."""

    def __init__(self, capacity: int):
        self.capacity = max(1, capacity)
        self._counts: dict[Any, int] = {}

    def offer(self, value: Any) -> None:
        if value in self._counts:
            self._counts[value] += 1
            return
        if len(self._counts) < self.capacity:
            self._counts[value] = 1
            return

        smallest_key = min(self._counts, key=self._counts.get)
        smallest_count = self._counts.pop(smallest_key)
        self._counts[value] = smallest_count + 1

    def to_meta(self, k: int, total_rows: int) -> list[dict[str, Any]]:
        rows = sorted(self._counts.items(), key=lambda item: (-item[1], str(item[0])))
        out = []
        for value, count in rows[:k]:
            out.append({
                "value": value,
                "count": count,
                "percentage": round((count / total_rows) * 100, 2),
            })
        return out


def _normalize_number(value):
    if value is None:
        return None
    if float(value).is_integer():
        return int(value)
    return value


def _stable_token(value: Any) -> str:
    if isinstance(value, bytes):
        return f"bytes:{len(value)}:{value[:32]!r}"
    return f"{type(value).__name__}:{value!r}"


def _sample_token(value: Any) -> str:
    if isinstance(value, bytes):
        return f"bytes:{len(value)}:{value[:32]!r}"
    return repr(value)


def _normalize_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"<BLOB:{len(value)}bytes>"
    return value
