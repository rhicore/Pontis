"""Legacy approximate CSV column profiler.

This replaces the old per-column CSV passes. Each CSV/TSV file is scanned once
and all column profiles are updated together.

Outputs per column:
- approximate cardinality via CPC sketch
- null percentage
- distinct sample
- approximate top-k via Space-Saving
- numeric/text lightweight stats

It depends on the inactive CSV schema projection and is therefore not part of
the default preprocess registry.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from typing import Any

from datasketches import cpc_sketch

from extractor.utils.refs import set_entity_meta
from storage.workspace import Workspace
from tool.utils.workspace_access import OpenFileSource, resolve_file_sources

logger = logging.getLogger(__name__)

_CPC_LG_K = 11
_DEFAULT_SAMPLE_SIZE = 10
_DEFAULT_TOPK = 5
_NUMERIC_LABELS = {"INT", "INTEGER", "FLOAT", "REAL"}


def generate(
    workspace: Workspace,
    sample_size: int = _DEFAULT_SAMPLE_SIZE,
    topk_size: int = _DEFAULT_TOPK,
    file: str | None = None,
) -> None:
    """Generate approximate profile fields for all CSV/TSV columns."""
    logger.info("=== Generating approximate CSV column profiles ===")

    seen = set()
    sources = []
    if file:
        sources.extend(resolve_file_sources(workspace, file, labels=("csv",), allow_directory=False))
        sources.extend(resolve_file_sources(workspace, file, labels=("tsv",), allow_directory=False))
    else:
        sources.extend(resolve_file_sources(workspace, ".", labels=("csv",), allow_directory=True))
        sources.extend(resolve_file_sources(workspace, ".", labels=("tsv",), allow_directory=True))
    for source in sorted(sources, key=lambda item: item.path):
        if source.path in seen:
            continue
        seen.add(source.path)
        try:
            _profile_csv(workspace, source, sample_size=sample_size, topk_size=topk_size)
        except Exception as exc:
            logger.warning("Failed to profile CSV %s: %s", source.path, exc)


def _profile_csv(
    workspace: Workspace,
    source: OpenFileSource,
    *,
    sample_size: int,
    topk_size: int,
) -> bool:
    col_rows = workspace.cypher(
        "MATCH (f:file)--(c:col) "
        "WHERE f.path = $csv_ref OR f.name = $csv_name "
        "RETURN c ORDER BY c.ordinal",
        params={"csv_ref": source.path, "csv_name": source.name},
    )
    columns = [row.get("c", {}) or {} for row in col_rows]
    if not columns:
        return False
    if not any(_needs_profile(col) for col in columns):
        return False

    delimiter = "\t" if source.path.lower().endswith(".tsv") else ","
    profilers: list[_ColumnProfiler] = []
    for idx, col in enumerate(columns):
        col_ref = col.get("_ref") or col.get("ref") or col.get("name")
        source_column = col.get("source_column") or col.get("name")
        if not col_ref or not source_column:
            continue
        profilers.append(_ColumnProfiler(
            ref=col_ref,
            name=source_column,
            ordinal=int(col.get("ordinal", idx) or idx),
            col_type=str(col.get("col_type") or ""),
            sample_size=sample_size,
            topk_size=topk_size,
        ))
    if not profilers:
        return False

    ordinal_map = {prof.ordinal: prof for prof in profilers}
    with source.open_file("r", encoding="utf-8", errors="ignore", newline="") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        headers = next(reader, None)
        if headers is None:
            return False
        for row in reader:
            for ordinal, profiler in ordinal_map.items():
                value = row[ordinal] if ordinal < len(row) else ""
                profiler.offer(value)

    updated = 0
    for profiler in profilers:
        stats = profiler.to_meta()
        set_entity_meta(workspace, profiler.ref, stats)
        updated += 1
    logger.info("  CSV profiled: %s (%s columns)", source.path, updated)
    return True


def _needs_profile(meta: dict) -> bool:
    required = ("cardinality_method", "sample", "topk")
    return any(key not in meta for key in required)


@dataclass
class _ColumnProfiler:
    ref: str
    name: str
    ordinal: int
    col_type: str
    sample_size: int
    topk_size: int

    sketch: cpc_sketch = field(default_factory=lambda: cpc_sketch(_CPC_LG_K))
    topk_counter: "_SpaceSavingCounter" = field(init=False)
    total_rows: int = 0
    null_count: int = 0
    non_null_count: int = 0
    sample: list[Any] = field(default_factory=list)
    sample_seen: set[str] = field(default_factory=set)
    numeric_count: int = 0
    numeric_sum: float = 0.0
    min_value: float | None = None
    max_value: float | None = None
    text_count: int = 0
    text_len_sum: int = 0
    min_length: int | None = None
    max_length: int | None = None

    def __post_init__(self) -> None:
        self.topk_counter = _SpaceSavingCounter(max(self.topk_size * 8, 32))

    def offer(self, value: str | None) -> None:
        self.total_rows += 1
        if value is None or value == "":
            self.null_count += 1
            return

        normalized = _normalize_value(value)
        self.non_null_count += 1
        self.sketch.update(_stable_token(normalized))
        self.topk_counter.offer(normalized)

        sample_token = _sample_token(normalized)
        if len(self.sample) < self.sample_size and sample_token not in self.sample_seen:
            self.sample_seen.add(sample_token)
            self.sample.append(normalized)

        text = str(value)
        text_len = len(text)
        self.text_count += 1
        self.text_len_sum += text_len
        self.min_length = text_len if self.min_length is None else min(self.min_length, text_len)
        self.max_length = text_len if self.max_length is None else max(self.max_length, text_len)

        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        self.numeric_count += 1
        self.numeric_sum += number
        self.min_value = number if self.min_value is None else min(self.min_value, number)
        self.max_value = number if self.max_value is None else max(self.max_value, number)

    def to_meta(self) -> dict:
        if self.total_rows == 0:
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
            "cardinality": int(round(self.sketch.get_estimate())),
            "cardinality_lower_bound": int(round(self.sketch.get_lower_bound(1))),
            "cardinality_upper_bound": int(round(self.sketch.get_upper_bound(1))),
            "cardinality_method": "cpc_sketch",
            "null_count": self.null_count,
            "null_percentage": round((self.null_count / self.total_rows) * 100, 2),
            "sample": self.sample,
            "sample_method": "single_pass_distinct_prefix",
            "topk": self.topk_counter.to_meta(self.topk_size, self.total_rows),
            "topk_method": "space_saving",
        }

        if self._should_report_numeric():
            stats["min_value"] = _normalize_number(self.min_value)
            stats["max_value"] = _normalize_number(self.max_value)
            stats["mean_value"] = round(self.numeric_sum / self.numeric_count, 4)
        elif self.text_count > 0:
            stats["min_length"] = self.min_length
            stats["max_length"] = self.max_length
            stats["avg_length"] = round(self.text_len_sum / self.text_count, 2)

        return stats

    def _should_report_numeric(self) -> bool:
        if self.numeric_count == 0:
            return False
        if self.col_type.upper() in _NUMERIC_LABELS:
            return True
        return self.non_null_count > 0 and self.numeric_count / self.non_null_count >= 0.95


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
        return [
            {
                "value": value,
                "count": count,
                "percentage": round((count / total_rows) * 100, 2),
            }
            for value, count in rows[:k]
        ]


def _stable_token(value: Any) -> str:
    return f"{type(value).__name__}:{value!r}"


def _sample_token(value: Any) -> str:
    return repr(value)


def _normalize_value(value: Any) -> Any:
    if value is None:
        return None
    return str(value)


def _normalize_number(value):
    if value is None:
        return None
    if float(value).is_integer():
        return int(value)
    return value


__all__ = ["generate"]
