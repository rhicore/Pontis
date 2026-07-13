"""DB column profiler.

This extractor only uses database handles exposed by graph entities. A database
node must expose ``_db_connect``/``db_connect`` through the storage layer; table
and column discovery comes from the KG.
"""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Optional

from extractor.utils.refs import set_entity_meta
from extractor.utils.domain_profile import build_domain_profile
from storage.workspace import Workspace

logger = logging.getLogger(__name__)

_NUMERIC_TYPES = {"INT", "INTEGER", "REAL", "FLOAT", "DOUBLE", "DECIMAL", "NUMBER", "NUMERIC"}
_TEXT_TYPES = {"TEXT", "VARCHAR", "CHAR", "STRING"}
_CPC_LG_K = 11
_DEFAULT_SAMPLE_SIZE = 10
_DEFAULT_TOPK = 5
_DEFAULT_MAX_WORKERS = 4
_DEFAULT_CARDINALITY_MODE = "exact"
_CARDINALITY_MODES = {"exact", "sketch", "approx", "approximate"}
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^(?:https?://|www\.).+")
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_DIGITS_RE = re.compile(r"^[0-9]+$")
_ALPHA_RE = re.compile(r"^[a-z]+$")
_ALNUM_RE = re.compile(r"^[a-z0-9_-]+$")
_HEX_RE = re.compile(r"^[0-9a-f]*[a-f][0-9a-f]*$")


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
    cardinality_mode: str | None = None,
) -> None:
    """Generate profile fields for all DB columns."""
    logger.info("=== Generating DB column profiles ===")
    worker_count = _resolve_max_workers(max_workers)
    cardinality_mode = _resolve_cardinality_mode(cardinality_mode)
    tasks = _load_column_tasks(workspace, cardinality_mode=cardinality_mode)

    if not tasks:
        logger.info("  No DB columns need profiles")
        return

    logger.info(
        "  Profiling %s columns with %s workers (cardinality_mode=%s)",
        len(tasks),
        worker_count,
        cardinality_mode,
    )
    if worker_count <= 1:
        for task in tasks:
            _run_and_write_task(
                task,
                workspace,
                sample_size=sample_size,
                topk_size=topk_size,
                cardinality_mode=cardinality_mode,
            )
        return

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                _profile_column_task,
                task,
                sample_size=sample_size,
                topk_size=topk_size,
                cardinality_mode=cardinality_mode,
            ): task
            for task in tasks
        }
        for future in as_completed(futures):
            task = futures[future]
            try:
                stats = future.result()
            except Exception as e:
                logger.warning("Failed to generate profile for %s: %s", task.col_ref, e)
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


def _resolve_cardinality_mode(cardinality_mode: str | None) -> str:
    raw = cardinality_mode or os.environ.get("PONTIS_DB_COLUMN_STATS_CARDINALITY_MODE") or _DEFAULT_CARDINALITY_MODE
    mode = str(raw).strip().lower()
    if mode in {"approx", "approximate"}:
        return "sketch"
    if mode not in _CARDINALITY_MODES:
        logger.warning("Invalid cardinality_mode=%r, using %s", raw, _DEFAULT_CARDINALITY_MODE)
        return _DEFAULT_CARDINALITY_MODE
    return mode


def _load_column_tasks(
    workspace: Workspace,
    *,
    cardinality_mode: str = _DEFAULT_CARDINALITY_MODE,
) -> list[_ColumnTask]:
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
            if not _needs_profile(col, cardinality_mode=cardinality_mode):
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


def _needs_profile(meta: dict, *, cardinality_mode: str = _DEFAULT_CARDINALITY_MODE) -> bool:
    required = ("cardinality_method", "sample", "topk", "domain_profile")
    if any(key not in meta for key in required):
        return True
    if cardinality_mode == "exact" and str(meta.get("cardinality_method") or "") in {
        "cpc_sketch",
        "snowflake_approx_count_distinct",
    }:
        return True
    return False


def _normalize_type(data_type: str) -> str:
    text = str(data_type or "").upper()
    return text.split("(", 1)[0].strip()


def _run_and_write_task(
    task: _ColumnTask,
    workspace: Workspace,
    *,
    sample_size: int,
    topk_size: int,
    cardinality_mode: str,
) -> None:
    try:
        stats = _profile_column_task(
            task,
            sample_size=sample_size,
            topk_size=topk_size,
            cardinality_mode=cardinality_mode,
        )
    except Exception as e:
        logger.warning("Failed to generate profile for %s: %s", task.col_ref, e)
        return
    _write_profile(workspace, task.col_ref, stats)


def _write_profile(workspace: Workspace, col_ref: str, stats: Optional[dict]) -> None:
    if not stats:
        return

    set_entity_meta(workspace, col_ref, stats)
    logger.info(
        "  Profiled: %s (cardinality=%s, method=%s, sample=%s, topk=%s)",
        col_ref,
        stats.get("cardinality"),
        stats.get("cardinality_method"),
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
    cardinality_mode: str | None = None,
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
    cardinality_mode = _resolve_cardinality_mode(cardinality_mode)
    col = _load_db_columns_from_row(col_rows[0])
    if not col or not _needs_profile(col, cardinality_mode=cardinality_mode):
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
    stats = _profile_column_task(
        task,
        sample_size=sample_size,
        topk_size=topk_size,
        cardinality_mode=_resolve_cardinality_mode(cardinality_mode),
    )
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
    cardinality_mode: str = _DEFAULT_CARDINALITY_MODE,
) -> Optional[dict]:
    try:
        conn = _connect(task.db_connect, readonly=True)
        try:
            cursor = conn.cursor()
            if task.dialect == "snowflake":
                try:
                    return _profile_column_snowflake(
                        cursor,
                        task,
                        sample_size=sample_size,
                        topk_size=topk_size,
                        cardinality_mode=cardinality_mode,
                    )
                except Exception as e:
                    logger.debug("Snowflake SQL-side profile failed for %s, falling back: %s", task.col_ref, e)
            table_sql = _qualified_table_sql(task.schema_name, task.table_name, task.dialect)
            column_sql = _quote_identifier(task.col_name, task.dialect)
            cursor.execute(f"SELECT {column_sql} FROM {table_sql}")
            return _profile_cursor(
                cursor,
                task.data_type,
                sample_size=sample_size,
                topk_size=topk_size,
                cardinality_mode=cardinality_mode,
            )
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
        return f"{_quote_identifier(schema_name, dialect)}.{_quote_identifier(table_name, dialect)}"
    return _quote_identifier(table_name, dialect)


def _quote_identifier(identifier: str, dialect: str = "") -> str:
    text = str(identifier or "")
    return '"' + text.replace('"', '""') + '"'


def _profile_column_snowflake(
    cursor,
    task: _ColumnTask,
    *,
    sample_size: int,
    topk_size: int,
    cardinality_mode: str,
) -> Optional[dict]:
    """Profile one Snowflake column with database-side aggregation."""

    table_sql = _qualified_table_sql(task.schema_name, task.table_name, task.dialect)
    column_sql = _quote_identifier(task.col_name, task.dialect)
    aggregate_sql = _snowflake_profile_aggregate_sql(table_sql, column_sql, task.data_type, cardinality_mode)
    cursor.execute(aggregate_sql)
    row = cursor.fetchone()
    if not row:
        return None

    total_rows = int(_row_value(row, 0) or 0)
    null_count = int(_row_value(row, 1) or 0)
    cardinality = int(round(float(_row_value(row, 2) or 0)))
    stats = {
        "cardinality": cardinality,
        "cardinality_lower_bound": cardinality,
        "cardinality_upper_bound": cardinality,
        "cardinality_method": (
            "snowflake_approx_count_distinct"
            if cardinality_mode == "sketch"
            else "snowflake_count_distinct"
        ),
        "null_count": null_count,
        "null_percentage": round((null_count / total_rows) * 100, 2) if total_rows else 0.0,
        "sample": _snowflake_sample(cursor, table_sql, column_sql, sample_size),
        "sample_method": "snowflake_select_distinct_limit",
        "topk": _snowflake_topk(cursor, table_sql, column_sql, topk_size, total_rows),
        "topk_method": "snowflake_group_by_count",
    }

    if task.data_type in _NUMERIC_TYPES:
        min_value = _row_value(row, 3)
        max_value = _row_value(row, 4)
        mean_value = _row_value(row, 5)
        if min_value is not None and max_value is not None:
            stats["min_value"] = _normalize_number(float(min_value))
            stats["max_value"] = _normalize_number(float(max_value))
        if mean_value is not None:
            stats["mean_value"] = round(float(mean_value), 4)
    elif task.data_type in _TEXT_TYPES:
        min_length = _row_value(row, 3)
        max_length = _row_value(row, 4)
        avg_length = _row_value(row, 5)
        if min_length is not None:
            stats["min_length"] = int(min_length)
        if max_length is not None:
            stats["max_length"] = int(max_length)
        if avg_length is not None:
            stats["avg_length"] = round(float(avg_length), 2)

    stats["domain_profile"] = _domain_profile_from_snowflake_row(
        row,
        data_type=task.data_type,
        min_value=_row_value(row, 3) if task.data_type in _NUMERIC_TYPES else None,
        max_value=_row_value(row, 4) if task.data_type in _NUMERIC_TYPES else None,
        min_length=stats.get("min_length"),
        max_length=stats.get("max_length"),
    )
    stats["domain_profile_method"] = "full_column_aggregate"

    return stats


def _snowflake_profile_aggregate_sql(
    table_sql: str,
    column_sql: str,
    data_type: str,
    cardinality_mode: str,
) -> str:
    cardinality_expr = (
        f"APPROX_COUNT_DISTINCT({column_sql})"
        if cardinality_mode == "sketch"
        else f"COUNT(DISTINCT {column_sql})"
    )
    common = [
        "COUNT(*) AS total_rows",
        f"COUNT_IF({column_sql} IS NULL) AS null_count",
        f"{cardinality_expr} AS cardinality",
    ]
    if data_type in _NUMERIC_TYPES:
        common.extend([
            f"MIN({column_sql}) AS min_value",
            f"MAX({column_sql}) AS max_value",
            f"AVG({column_sql}) AS mean_value",
        ])
    elif data_type in _TEXT_TYPES:
        value_expr = f"TO_VARCHAR({column_sql})"
        common.extend([
            f"MIN(LENGTH({value_expr})) AS min_length",
            f"MAX(LENGTH({value_expr})) AS max_length",
            f"AVG(LENGTH({value_expr})) AS avg_length",
        ])
    else:
        common.extend(["NULL AS extra_1", "NULL AS extra_2", "NULL AS extra_3"])
    value_expr = f"LOWER(TRIM(TO_VARCHAR({column_sql})))"
    numeric_expr = f"TRY_TO_DOUBLE({value_expr})"
    common.extend([
        f"COUNT_IF({column_sql} IS NOT NULL AND {value_expr} <> '') AS domain_nonempty_count",
        f"COUNT_IF(REGEXP_LIKE({value_expr}, '^[+-]?[0-9]+$')) AS domain_integer_count",
        f"COUNT_IF({numeric_expr} IS NOT NULL AND {numeric_expr} <> TRUNC({numeric_expr})) AS domain_fractional_count",
        f"COUNT_IF(REGEXP_LIKE({value_expr}, '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$')) AS domain_uuid_count",
        f"COUNT_IF(REGEXP_LIKE({value_expr}, '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$')) AS domain_email_count",
        f"COUNT_IF(REGEXP_LIKE({value_expr}, '^(https?://|www[.]).+$')) AS domain_url_count",
        f"COUNT_IF(REGEXP_LIKE({value_expr}, '^[0-9]{{1,3}}([.][0-9]{{1,3}}){{3}}$')) AS domain_ipv4_count",
        f"COUNT_IF(REGEXP_LIKE({value_expr}, '^[0-9a-f]*[a-f][0-9a-f]*$')) AS domain_hex_count",
        f"COUNT_IF(REGEXP_LIKE({value_expr}, '^[0-9]+$')) AS domain_digits_count",
        f"COUNT_IF(REGEXP_LIKE({value_expr}, '^[a-z]+$')) AS domain_alpha_count",
        f"COUNT_IF(REGEXP_LIKE({value_expr}, '^[a-z0-9_-]+$')) AS domain_alnum_count",
    ])
    return f"SELECT {', '.join(common)} FROM {table_sql}"


def _domain_profile_from_snowflake_row(
    row,
    *,
    data_type: str,
    min_value: Any,
    max_value: Any,
    min_length: int | None,
    max_length: int | None,
) -> dict:
    # The first six fields are the common profile aggregate fields above.
    values = [int(_row_value(row, index) or 0) for index in range(6, 17)]
    return build_domain_profile(
        data_type,
        nonempty_count=values[0],
        min_value=min_value,
        max_value=max_value,
        integer_count=values[1],
        fractional_count=values[2],
        uuid_count=values[3],
        email_count=values[4],
        url_count=values[5],
        ipv4_count=values[6],
        hex_count=values[7],
        digits_count=values[8],
        alpha_count=values[9],
        alnum_count=values[10],
        min_length=min_length,
        max_length=max_length,
    )


def _snowflake_sample(cursor, table_sql: str, column_sql: str, sample_size: int) -> list[Any]:
    if sample_size <= 0:
        return []
    cursor.execute(
        f"""
SELECT DISTINCT {column_sql} AS value
FROM {table_sql}
WHERE {column_sql} IS NOT NULL
LIMIT {int(sample_size)}
"""
    )
    return [_normalize_value(_row_value(row, 0)) for row in cursor.fetchall()]


def _snowflake_topk(cursor, table_sql: str, column_sql: str, topk_size: int, total_rows: int) -> list[dict[str, Any]]:
    if topk_size <= 0 or total_rows <= 0:
        return []
    cursor.execute(
        f"""
SELECT {column_sql} AS value, COUNT(*) AS count
FROM {table_sql}
WHERE {column_sql} IS NOT NULL
GROUP BY {column_sql}
ORDER BY count DESC, value
LIMIT {int(topk_size)}
"""
    )
    rows = []
    for row in cursor.fetchall():
        count = int(_row_value(row, 1) or 0)
        rows.append({
            "value": _normalize_value(_row_value(row, 0)),
            "count": count,
            "percentage": round((count / total_rows) * 100, 2),
        })
    return rows


def _row_value(row, index: int):
    if isinstance(row, dict):
        return list(row.values())[index]
    return row[index]


def _profile_cursor(
    cursor,
    data_type: str,
    *,
    sample_size: int,
    topk_size: int,
    cardinality_mode: str,
) -> Optional[dict]:
    sketch = _new_cpc_sketch() if cardinality_mode == "sketch" else None
    distinct_values: set[str] | None = set() if cardinality_mode == "exact" else None
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
    domain_counts = _new_domain_counts()

    for (value,) in cursor:
        total_rows += 1
        if value is None:
            null_count += 1
            continue

        stable_token = _stable_token(value)
        if sketch is not None:
            sketch.update(stable_token)
        if distinct_values is not None:
            distinct_values.add(stable_token)
        topk_counter.offer(_normalize_value(value))
        _offer_domain_value(domain_counts, value)

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
            "cardinality_method": _cardinality_method(cardinality_mode),
            "null_count": 0,
            "null_percentage": 0.0,
            "sample": [],
            "sample_method": "single_pass_distinct_prefix",
            "topk": [],
            "topk_method": "space_saving",
            "domain_profile": build_domain_profile(data_type),
            "domain_profile_method": "full_column_scan",
        }

    cardinality_stats = _cursor_cardinality_stats(cardinality_mode, sketch, distinct_values)
    stats = {
        **cardinality_stats,
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

    stats["domain_profile"] = build_domain_profile(
        data_type,
        nonempty_count=domain_counts["nonempty"],
        min_value=min_value,
        max_value=max_value,
        integer_count=domain_counts["integer"],
        fractional_count=domain_counts["fractional"],
        uuid_count=domain_counts["uuid"],
        email_count=domain_counts["email"],
        url_count=domain_counts["url"],
        ipv4_count=domain_counts["ipv4"],
        hex_count=domain_counts["hex"],
        digits_count=domain_counts["digits"],
        alpha_count=domain_counts["alpha"],
        alnum_count=domain_counts["alnum"],
        min_length=min_length,
        max_length=max_length,
    )
    stats["domain_profile_method"] = "full_column_scan"

    return stats


def _new_domain_counts() -> dict[str, int]:
    return {
        "nonempty": 0,
        "integer": 0,
        "fractional": 0,
        "uuid": 0,
        "email": 0,
        "url": 0,
        "ipv4": 0,
        "hex": 0,
        "digits": 0,
        "alpha": 0,
        "alnum": 0,
    }


def _offer_domain_value(counts: dict[str, int], value: Any) -> None:
    text = str(value).strip().lower()
    if not text:
        return
    counts["nonempty"] += 1
    try:
        numeric = float(text)
    except (TypeError, ValueError):
        numeric = None
    if numeric is not None:
        if numeric.is_integer():
            counts["integer"] += 1
        else:
            counts["fractional"] += 1
    if _UUID_RE.fullmatch(text):
        counts["uuid"] += 1
    if _EMAIL_RE.fullmatch(text):
        counts["email"] += 1
    if _URL_RE.fullmatch(text):
        counts["url"] += 1
    if _IPV4_RE.fullmatch(text):
        counts["ipv4"] += 1
    if _HEX_RE.fullmatch(text):
        counts["hex"] += 1
    if _DIGITS_RE.fullmatch(text):
        counts["digits"] += 1
    if _ALPHA_RE.fullmatch(text):
        counts["alpha"] += 1
    if _ALNUM_RE.fullmatch(text):
        counts["alnum"] += 1


def _new_cpc_sketch():
    try:
        from datasketches import cpc_sketch
    except ImportError as exc:
        raise RuntimeError("datasketches is required when cardinality_mode='sketch'") from exc
    return cpc_sketch(_CPC_LG_K)


def _cursor_cardinality_stats(cardinality_mode: str, sketch, distinct_values: set[str] | None) -> dict[str, int | str]:
    if cardinality_mode == "sketch":
        estimate = int(round(sketch.get_estimate()))
        return {
            "cardinality": estimate,
            "cardinality_lower_bound": int(round(sketch.get_lower_bound(1))),
            "cardinality_upper_bound": int(round(sketch.get_upper_bound(1))),
            "cardinality_method": "cpc_sketch",
        }
    cardinality = len(distinct_values or set())
    return {
        "cardinality": cardinality,
        "cardinality_lower_bound": cardinality,
        "cardinality_upper_bound": cardinality,
        "cardinality_method": "count_distinct",
    }


def _cardinality_method(cardinality_mode: str) -> str:
    return "cpc_sketch" if cardinality_mode == "sketch" else "count_distinct"


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
