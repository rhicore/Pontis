"""Tests for the Spider adapter to shared result evaluation."""

import json

from scripts.spider.result_match import (
    _condition_columns,
    compare_spider_result_files,
    discover_spider_gold_paths,
    evaluate_spider_result_directory,
)


def write_csv(path, text):
    path.write_text(text, encoding="utf-8")


def test_multiple_spider_gold_results_accept_any_alternative(tmp_path):
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    write_csv(gold_dir / "case_a.csv", "status\nactive\n")
    write_csv(gold_dir / "case_b.csv", "status\nenabled\n")
    predicted = tmp_path / "case.csv"
    write_csv(predicted, "status\nenabled\n")

    paths = discover_spider_gold_paths("case", gold_dir)
    comparison = compare_spider_result_files(
        predicted,
        paths,
        {"condition_cols": [], "ignore_order": True},
    )

    assert comparison.business_correct
    assert comparison.matched_gold_name == "case_b.csv"


def test_spider_condition_columns_preserve_row_association(tmp_path):
    gold = tmp_path / "gold.csv"
    correct = tmp_path / "correct.csv"
    detached = tmp_path / "detached.csv"
    write_csv(gold, "ignored,ticker,value\nx,AAPL,10\ny,MSFT,20\n")
    write_csv(correct, "company,value,ticker\nApple,10,AAPL\nMicrosoft,20,MSFT\n")
    write_csv(detached, "company,value,ticker\nApple,20,AAPL\nMicrosoft,10,MSFT\n")
    config = {"condition_cols": [1, 2], "ignore_order": True}

    assert compare_spider_result_files(correct, [gold], config).business_correct
    assert not compare_spider_result_files(detached, [gold], config).business_correct


def test_spider_directory_evaluation_reports_missing_prediction(tmp_path):
    gold_dir = tmp_path / "gold"
    pred_dir = tmp_path / "pred"
    gold_dir.mkdir()
    pred_dir.mkdir()
    write_csv(gold_dir / "case.csv", "value\n1\n")
    config_path = tmp_path / "eval.jsonl"
    config_path.write_text(
        json.dumps({"instance_id": "case", "condition_cols": [], "ignore_order": True}) + "\n",
        encoding="utf-8",
    )

    rows = evaluate_spider_result_directory(
        predicted_result_dir=pred_dir,
        gold_result_dir=gold_dir,
        eval_config_path=config_path,
    )

    assert rows[0]["business_correct"] is False
    assert rows[0]["match_type"] == "missing_prediction_result"


def test_extra_condition_column_variants_are_ignored_like_official_evaluator():
    assert _condition_columns([[0, 1], [2], [3]], 2) == [(0, 1), (2,)]
    assert _condition_columns([[0], [1]], 3) == [(0,), (1,), None]
