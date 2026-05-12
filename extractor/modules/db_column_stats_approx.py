"""Approximate DB column profiler.

This pass intentionally merges the old responsibilities of:
- db_column_stats_approx
- db_column_sample
- db_column_topk

Each column is scanned once. The pass produces:
- approximate cardinality via CPC sketch
- lightweight null / numeric / text stats
- small distinct sample
- approximate top-k frequent values
"""

import logging
from typing import Any, Optional

from datasketches import cpc_sketch

from storage.workspace import Workspace
from extractor.modules.utils.refs import db_column_ref, get_entity_meta, set_entity_meta
from extractor.modules.utils.src import file_exists, open_sqlite_db

logger = logging.getLogger(__name__)

_NUMERIC_TYPES = {"INT", "INTEGER", "REAL", "FLOAT"}
_TEXT_TYPES = {"TEXT", "VARCHAR", "CHAR"}
_CPC_LG_K = 11
_DEFAULT_SAMPLE_SIZE = 10
_DEFAULT_TOPK = 5


def generate(
    workspace: Workspace,
    sample_size: int = _DEFAULT_SAMPLE_SIZE,
    topk_size: int = _DEFAULT_TOPK,
) -> None:
    """Generate approximate profile fields for all DB columns."""
    logger.info("=== Generating approximate DB column profiles ===")

    for ext_suffix in [".db", ".sqlite", ".sqlite3", ".duckdb"]:
        db_rows = workspace.cypher(f"MATCH (n) WHERE n.name ENDS WITH '{ext_suffix}' RETURN n")
        for db_row in db_rows:
            db_ref = db_row["n"]["name"]
            tbl_rows = workspace.cypher(f'MATCH (d {{name: "{db_ref}"}})--(t:table) RETURN t')
            for tbl_row in tbl_rows:
                table_ref = tbl_row["t"]["name"]
                col_rows = workspace.cypher(
                    f'MATCH (d {{name: "{db_ref}"}})--(t {{name: "{table_ref}"}})--(c:col) RETURN c'
                )
                for col_row in col_rows:
                    col_name = col_row["c"]["name"]
                    col_ref = db_column_ref(db_ref, table_ref, col_name)
                    try:
                        _generate_for_column(
                            col_ref,
                            db_ref,
                            table_ref,
                            workspace,
                            sample_size=sample_size,
                            topk_size=topk_size,
                        )
                    except Exception as e:
                        logger.warning(f"Failed to generate approximate profile for {col_ref}: {e}")


def _needs_profile(meta: dict) -> bool:
    required = ("cardinality_method", "sample", "topk")
    return any(key not in meta for key in required)


def _generate_for_column(
    col_ref: str,
    db_ref: str,
    table_ref: str,
    workspace: Workspace,
    *,
    sample_size: int,
    topk_size: int,
) -> bool:
    meta = get_entity_meta(workspace, col_ref)
    if not meta:
        return False

    if not _needs_profile(meta):
        return False

    col_name = meta.get("name", col_ref)
    data_type = meta.get("col_type", "")
    db_meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": db_ref})
    db_meta = db_meta_rows[0].get("n") if db_meta_rows else None
    db_rel = db_meta.get("path", db_ref) if db_meta else db_ref
    if not file_exists(workspace, db_rel):
        return False

    stats = _profile_column(
        db_rel,
        table_ref,
        col_name,
        data_type,
        workspace,
        sample_size=sample_size,
        topk_size=topk_size,
    )
    if not stats:
        return False

    set_entity_meta(workspace, col_ref, stats)
    logger.info(
        "  Profiled: %s (cardinality≈%s, sample=%s, topk=%s)",
        col_ref,
        stats.get("cardinality"),
        len(stats.get("sample", [])),
        len(stats.get("topk", [])),
    )
    return True


def _profile_column(
    db_rel: str,
    table: str,
    column: str,
    data_type: str,
    workspace: Workspace,
    *,
    sample_size: int,
    topk_size: int,
) -> Optional[dict]:
    try:
        with open_sqlite_db(workspace, db_rel) as conn:
            cursor = conn.cursor()
            cursor.execute(f'SELECT "{column}" FROM "{table}"')

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
    except Exception as e:
        logger.debug(f"Could not profile column: {e}")
        return None


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
