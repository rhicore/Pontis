"""BIRD adapter for the shared relation-preserving result evaluator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scripts.BIRD.result_guidance import (
    result_order_is_significant,
    sql_guided_column_mapping,
)
from scripts.result_evaluation import (
    ComparisonPolicy,
    GoldenResult,
    ResultComparison,
    ResultTable,
    compare_result_sets,
)


@dataclass(frozen=True)
class ExecutionResult:
    """SQLite execution result with columns and complete row tuples."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]

    @property
    def width(self) -> int:
        return len(self.columns)

    def as_result_table(self) -> ResultTable:
        return ResultTable(self.columns, self.rows)


@dataclass(frozen=True)
class ComparisonResult:
    """Executable proxy for whether the result satisfies the business answer."""

    business_correct: bool
    match_type: str


def compare_execution_results(
    predicted: ExecutionResult | str,
    golden: ExecutionResult | str | None,
    *,
    golden_sql: str | None = None,
    predicted_sql: str | None = None,
    ordered: bool | None = None,
) -> ComparisonResult:
    """Compare results, using SQL only to guide result-comparison policy."""

    if golden is None:
        return ComparisonResult(False, "missing_gold")
    if isinstance(predicted, str) or isinstance(golden, str):
        return ComparisonResult(False, "execution_error")

    if ordered is None:
        ordered = result_order_is_significant(golden_sql)

    guided_mapping = sql_guided_column_mapping(
        golden_sql,
        predicted_sql,
        golden_width=golden.width,
        predicted_width=predicted.width,
    )
    if guided_mapping is not None and (
        predicted.width != golden.width
        or guided_mapping != tuple(range(golden.width))
    ):
        guided = _project_execution_result(predicted, guided_mapping)
        comparison = _compare_result_tables(
            guided,
            golden,
            ordered=ordered,
            allow_extra_predicted_columns=False,
            allow_column_reorder=False,
        )
        if comparison.business_correct:
            match_type = (
                "sql_guided_projection"
                if predicted.width > golden.width
                else "sql_guided_column_reorder"
            )
            return ComparisonResult(True, match_type)

    comparison = _compare_result_tables(
        predicted,
        golden,
        ordered=ordered,
        allow_extra_predicted_columns=True,
        allow_column_reorder=True,
    )
    return ComparisonResult(comparison.business_correct, comparison.match_type)


def _compare_result_tables(
    predicted: ExecutionResult,
    golden: ExecutionResult,
    *,
    ordered: bool,
    allow_extra_predicted_columns: bool,
    allow_column_reorder: bool,
) -> ResultComparison:
    return compare_result_sets(
        predicted.as_result_table(),
        [GoldenResult(golden.as_result_table(), ordered=ordered, name="golden_sql")],
        policy=ComparisonPolicy(
            ordered=ordered,
            allow_extra_predicted_columns=allow_extra_predicted_columns,
            allow_column_reorder=allow_column_reorder,
            max_reordered_columns=8,
            trim_strings=True,
            parse_numeric_strings=True,
            float_round_digits=9,
        ),
    )


def _project_execution_result(
    result: ExecutionResult,
    mapping: tuple[int, ...],
) -> ExecutionResult:
    return ExecutionResult(
        columns=tuple(result.columns[index] for index in mapping),
        rows=tuple(tuple(row[index] for index in mapping) for row in result.rows),
    )
