"""Tests for the shared relation-preserving result comparator."""

from decimal import Decimal

from scripts.result_evaluation import (
    ComparisonPolicy,
    GoldenResult,
    ResultTable,
    compare_result_sets,
    load_result,
)


def table(columns, rows):
    return ResultTable(tuple(columns), tuple(tuple(row) for row in rows))


def test_unordered_comparison_preserves_tuple_association_and_duplicates():
    golden = GoldenResult(table(["ticker", "value"], [["AAPL", 10], ["MSFT", 20]]))
    reordered = table(["ticker", "value"], [["MSFT", 20], ["AAPL", 10]])
    detached = table(["ticker", "value"], [["AAPL", 20], ["MSFT", 10]])

    assert compare_result_sets(reordered, [golden]).business_correct
    assert not compare_result_sets(detached, [golden]).business_correct
    duplicated = table(["ticker", "value"], [["AAPL", 10], ["AAPL", 10]])
    assert not compare_result_sets(duplicated, [golden]).business_correct


def test_multiple_goldens_accept_any_complete_alternative():
    goldens = [
        GoldenResult(table(["status"], [["active"]]), name="gold_a.csv"),
        GoldenResult(table(["status"], [["enabled"]]), name="gold_b.csv"),
    ]
    comparison = compare_result_sets(table(["status"], [["enabled"]]), goldens)

    assert comparison.business_correct
    assert comparison.matched_gold_index == 1
    assert comparison.matched_gold_name == "gold_b.csv"


def test_ordering_is_an_explicit_gold_or_policy_property():
    predicted = table(["rank"], [[2], [1]])
    golden = table(["rank"], [[1], [2]])

    assert compare_result_sets(predicted, [GoldenResult(golden)]).business_correct
    assert not compare_result_sets(
        predicted,
        [GoldenResult(golden, ordered=True)],
    ).business_correct


def test_condition_columns_remain_complete_tuples():
    golden = GoldenResult(
        table(
            ["ignored", "ticker", "value"],
            [["x", "AAPL", 10], ["y", "MSFT", 20]],
        ),
        required_columns=(1, 2),
    )
    policy = ComparisonPolicy(allow_extra_predicted_columns=True)
    correct = table(
        ["company", "value", "ticker"],
        [["Apple", 10, "AAPL"], ["Microsoft", 20, "MSFT"]],
    )
    detached = table(
        ["company", "value", "ticker"],
        [["Apple", 20, "AAPL"], ["Microsoft", 10, "MSFT"]],
    )

    assert compare_result_sets(correct, [golden], policy=policy).business_correct
    assert not compare_result_sets(detached, [golden], policy=policy).business_correct


def test_numeric_tolerance_requires_one_to_one_row_matching():
    golden = GoldenResult(table(["ticker", "value"], [["AAPL", "10.00"], ["MSFT", "20.00"]]))
    predicted = table(["ticker", "value"], [["MSFT", "20.009"], ["AAPL", "10.004"]])
    policy = ComparisonPolicy(
        parse_numeric_strings=True,
        numeric_abs_tolerance=Decimal("0.01"),
    )

    assert compare_result_sets(predicted, [golden], policy=policy).business_correct

    duplicated_near_match = table(
        ["ticker", "value"],
        [["AAPL", "10.004"], ["AAPL", "10.005"]],
    )
    assert not compare_result_sets(
        duplicated_near_match,
        [golden],
        policy=policy,
    ).business_correct


def test_csv_loader_skips_physical_blank_lines_but_keeps_empty_fields(tmp_path):
    path = tmp_path / "result.csv"
    path.write_text("a,b\n1,2\n\n,\n", encoding="utf-8")

    loaded = load_result(path)

    assert loaded.rows == (("1", "2"), ("", ""))


def test_csv_loader_pads_short_rows_like_pandas(tmp_path):
    path = tmp_path / "result.csv"
    path.write_text("a,b,c\n1\n2,3\n", encoding="utf-8")

    loaded = load_result(path)

    assert loaded.rows == (("1", None, None), ("2", "3", None))


def test_json_array_values_are_hashable_for_bag_comparison():
    golden = GoldenResult(table(["items"], [[["a", "b"]]]))
    predicted = table(["items"], [[["a", "b"]]])

    assert compare_result_sets(predicted, [golden]).business_correct
