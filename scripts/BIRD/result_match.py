"""Execution-result comparison helpers for BIRD-style evaluations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import combinations, permutations
from math import isfinite, perm
import re
from typing import Any, Iterable


MAX_COLUMN_REORDER_WIDTH = 8
MAX_PROJECTED_GOLD_WIDTH = 5
MAX_PROJECTION_CANDIDATES = 200000
FLOAT_ROUND_DIGITS = 9

_TOP_LIKE_RE = re.compile(
    r"\b(top|highest|lowest|largest|smallest|most|least|max(?:imum)?|min(?:imum)?|"
    r"first|last|rank|ranking|nth|1st|2nd|3rd|\d+(?:st|nd|rd|th))\b",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T].*)?$")


@dataclass(frozen=True)
class ExecutionResult:
    """SQLite execution result with enough shape information for relaxed matching."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]

    @property
    def row_set(self) -> frozenset[tuple[Any, ...]]:
        return frozenset(self.rows)

    @property
    def width(self) -> int:
        return len(self.columns)


@dataclass(frozen=True)
class ComparisonResult:
    """Strict and business-relaxed match outcome."""

    strict_correct: bool
    business_correct: bool
    match_type: str


def compare_execution_results(
    predicted: ExecutionResult | str,
    golden: ExecutionResult | str | None,
    *,
    question: str = "",
) -> ComparisonResult:
    """Compare predicted and golden execution results."""

    if golden is None:
        return ComparisonResult(False, False, "missing_gold")
    if isinstance(predicted, str) or isinstance(golden, str):
        return ComparisonResult(False, False, "execution_error")

    if predicted.row_set == golden.row_set:
        return ComparisonResult(True, True, "exact")

    if _normalized_row_set(predicted) == _normalized_row_set(golden):
        return ComparisonResult(False, True, "value_equivalent")

    if _column_reorder_match(predicted, golden):
        return ComparisonResult(False, True, "column_reorder")

    if _predicted_superset_match(predicted, golden):
        return ComparisonResult(False, True, "predicted_superset")

    if _top_tie_superset_match(predicted, golden, question):
        return ComparisonResult(False, True, "tie_superset")

    return ComparisonResult(False, False, "no_match")


def _normalized_row_set(result: ExecutionResult) -> frozenset[tuple[Any, ...]]:
    return _projected_row_set(result, tuple(range(result.width)), normalize=True)


def _projected_row_set(
    result: ExecutionResult,
    indexes: tuple[int, ...],
    *,
    normalize: bool,
) -> frozenset[tuple[Any, ...]]:
    if normalize:
        return frozenset(tuple(_normalize_value(row[index]) for index in indexes) for row in result.rows)
    return frozenset(tuple(row[index] for index in indexes) for row in result.rows)


def _normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, FLOAT_ROUND_DIGITS) if isfinite(value) else value
    if isinstance(value, str):
        text = value.strip()
        parsed_date = _parse_date(text)
        if parsed_date is not None:
            return ("date", parsed_date.isoformat())
        return text
    return value


def _parse_date(text: str) -> date | None:
    if not _DATE_RE.match(text):
        return None
    head = text.split(" ", 1)[0].split("T", 1)[0].replace("/", "-")
    parts = head.split("-")
    if len(parts) != 3:
        return None
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _column_reorder_match(predicted: ExecutionResult, golden: ExecutionResult) -> bool:
    if predicted.width != golden.width or predicted.width <= 1:
        return False
    if predicted.width > MAX_COLUMN_REORDER_WIDTH:
        return False
    golden_rows = _normalized_row_set(golden)
    for indexes in permutations(range(predicted.width)):
        if indexes == tuple(range(predicted.width)):
            continue
        if _projected_row_set(predicted, indexes, normalize=True) == golden_rows:
            return True
    return False


def _predicted_superset_match(predicted: ExecutionResult, golden: ExecutionResult) -> bool:
    if predicted.width <= golden.width:
        return False
    if golden.width <= 0:
        return False
    if golden.width > MAX_PROJECTED_GOLD_WIDTH:
        return False
    golden_rows = _normalized_row_set(golden)
    for indexes in _projection_index_orders(predicted.width, golden.width):
        if _projected_row_set(predicted, indexes, normalize=True) == golden_rows:
            return True
    return False


def _top_tie_superset_match(
    predicted: ExecutionResult,
    golden: ExecutionResult,
    question: str,
) -> bool:
    if predicted.width != golden.width:
        return False
    if len(predicted.row_set) <= len(golden.row_set):
        return False
    if not _TOP_LIKE_RE.search(question or ""):
        return False
    if predicted.width > MAX_COLUMN_REORDER_WIDTH:
        return False

    golden_rows = _normalized_row_set(golden)
    identity = tuple(range(predicted.width))
    index_orders: Iterable[tuple[int, ...]]
    if predicted.width <= 1:
        index_orders = (identity,)
    else:
        index_orders = permutations(range(predicted.width))
    for indexes in index_orders:
        predicted_rows = _projected_row_set(predicted, indexes, normalize=True)
        if golden_rows < predicted_rows:
            return True
    return False


def _candidate_count(n: int, k: int) -> int:
    try:
        return perm(n, k)
    except ValueError:
        return MAX_PROJECTION_CANDIDATES + 1


def _projection_index_orders(predicted_width: int, golden_width: int) -> Iterable[tuple[int, ...]]:
    if _candidate_count(predicted_width, golden_width) > MAX_PROJECTION_CANDIDATES:
        return
    for indexes in combinations(range(predicted_width), golden_width):
        yield indexes
        if golden_width <= 1:
            continue
        for ordered in permutations(indexes):
            if ordered != indexes:
                yield ordered
