"""BIRD adapter for the shared relation-preserving result evaluator."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from scripts.result_evaluation import (
    ComparisonPolicy,
    GoldenResult,
    ResultTable,
    compare_result_sets,
)


_ORDER_BY_RE = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)


@dataclass(frozen=True)
class ExecutionResult:
    """SQLite execution result with columns and complete row tuples."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]

    @property
    def row_set(self) -> frozenset[tuple[Any, ...]]:
        return frozenset(self.rows)

    @property
    def width(self) -> int:
        return len(self.columns)

    def as_result_table(self) -> ResultTable:
        return ResultTable(self.columns, self.rows)


@dataclass(frozen=True)
class ComparisonResult:
    """Business match outcome plus a legacy strict diagnostic."""

    strict_correct: bool
    business_correct: bool
    match_type: str


def compare_execution_results(
    predicted: ExecutionResult | str,
    golden: ExecutionResult | str | None,
    *,
    ordered: bool = False,
) -> ComparisonResult:
    """Evaluate one BIRD result through the shared business comparator."""

    strict_correct = (
        isinstance(predicted, ExecutionResult)
        and isinstance(golden, ExecutionResult)
        and predicted.row_set == golden.row_set
    )
    if golden is None:
        return ComparisonResult(strict_correct, False, "missing_gold")
    if isinstance(predicted, str) or isinstance(golden, str):
        return ComparisonResult(strict_correct, False, "execution_error")

    comparison = compare_result_sets(
        predicted.as_result_table(),
        [GoldenResult(golden.as_result_table(), ordered=ordered, name="golden_sql")],
        policy=ComparisonPolicy(
            ordered=ordered,
            allow_extra_predicted_columns=False,
            allow_column_reorder=True,
            max_reordered_columns=8,
            trim_strings=True,
            parse_numeric_strings=False,
            float_round_digits=9,
        ),
    )
    return ComparisonResult(
        strict_correct,
        comparison.business_correct,
        comparison.match_type,
    )


def result_order_is_significant(sql: str | None) -> bool:
    """Return whether the golden query orders its outermost result."""

    if not sql:
        return False
    try:
        import sqlglot

        expression = sqlglot.parse_one(sql, read="sqlite")
        return expression.args.get("order") is not None
    except Exception:
        return bool(_ORDER_BY_RE.search(sql))
