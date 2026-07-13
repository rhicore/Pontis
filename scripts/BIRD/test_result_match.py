"""Regression tests for BIRD business-correct result matching."""

from scripts.BIRD.result_match import (
    ExecutionResult,
    compare_execution_results,
    result_order_is_significant,
)


def result(columns, rows):
    return ExecutionResult(tuple(columns), tuple(tuple(row) for row in rows))


def test_unordered_business_match_preserves_complete_rows():
    golden = result(["ticker", "value"], [["AAPL", 10], ["MSFT", 20]])
    reordered = result(["ticker", "value"], [["MSFT", 20], ["AAPL", 10]])
    detached = result(["ticker", "value"], [["AAPL", 20], ["MSFT", 10]])

    assert compare_execution_results(reordered, golden).business_correct
    assert not compare_execution_results(detached, golden).business_correct


def test_business_match_preserves_duplicate_counts():
    golden = result(["department"], [["Sales"], ["Sales"]])
    predicted = result(["department"], [["Sales"]])

    comparison = compare_execution_results(predicted, golden)
    assert comparison.strict_correct
    assert not comparison.business_correct
    assert comparison.match_type == "row_count_mismatch"


def test_business_match_rejects_extra_columns():
    golden = result(["ticker"], [["AAPL"]])
    predicted = result(["ticker", "company"], [["AAPL", "Apple"]])

    comparison = compare_execution_results(predicted, golden)
    assert not comparison.business_correct
    assert comparison.match_type == "column_count_mismatch"


def test_business_match_allows_one_global_column_reorder():
    golden = result(["ticker", "value"], [["AAPL", 10], ["MSFT", 20]])
    predicted = result(["value", "ticker"], [[20, "MSFT"], [10, "AAPL"]])

    comparison = compare_execution_results(predicted, golden)
    assert comparison.business_correct
    assert comparison.match_type == "column_reorder"


def test_ordered_business_match_checks_full_row_sequence():
    golden = result(["ticker", "value"], [["AAPL", 10], ["MSFT", 20]])
    predicted = result(["ticker", "value"], [["MSFT", 20], ["AAPL", 10]])

    comparison = compare_execution_results(predicted, golden, ordered=True)
    assert not comparison.business_correct
    assert comparison.match_type == "ordered_row_mismatch"


def test_outer_order_by_controls_ordered_comparison():
    assert result_order_is_significant("SELECT a FROM t ORDER BY a")
    assert result_order_is_significant("SELECT a FROM t UNION SELECT a FROM u ORDER BY a")
    assert not result_order_is_significant(
        "SELECT a FROM (SELECT a FROM t ORDER BY a LIMIT 1) AS ranked"
    )
