#!/usr/bin/env python3
"""Compare a schema-linking benchmark run against an older labeled run.

The script is intentionally dataset-agnostic at the mechanism level: it parses
SQL structure, joins it with existing reflection labels when available, and
emits coarse buckets for manual review. It does not encode database-, table-, or
question-specific rules.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-results", required=True, type=Path)
    parser.add_argument("--new-results", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    old_rows = _load_jsonl(args.old_results)
    new_rows = _load_jsonl(args.new_results)
    old_by_key = {(r.get("db_id"), int(r.get("question_id", -1))): r for r in old_rows}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "db_id",
                "question_id",
                "category",
                "result_status",
                "correct",
                "coarse_schema_bucket",
                "likely_failure_family",
                "gold_tables",
                "pred_tables",
                "gold_columns",
                "pred_columns",
                "gold_select_columns",
                "pred_select_columns",
                "gold_where_columns",
                "pred_where_columns",
                "gold_aggregates",
                "pred_aggregates",
                "question",
                "evidence",
                "golden_sql",
                "predicted_sql",
            ],
        )
        writer.writeheader()
        for new in sorted(new_rows, key=lambda r: (str(r.get("db_id")), int(r.get("question_id", -1)))):
            key = (new.get("db_id"), int(new.get("question_id", -1)))
            old = old_by_key.get(key, {})
            gold = _sql_features(new.get("golden_sql") or old.get("golden_sql") or old.get("SQL") or "")
            pred = _sql_features(new.get("predicted_sql") or "")
            writer.writerow(
                {
                    "db_id": new.get("db_id", ""),
                    "question_id": new.get("question_id", ""),
                    "category": old.get("reflection_primary_error_category", ""),
                    "result_status": _result_status(new),
                    "correct": bool(new.get("correct")),
                    "coarse_schema_bucket": _bucket(new, gold, pred),
                    "likely_failure_family": _failure_family(new, gold, pred),
                    "gold_tables": _join(gold["tables"]),
                    "pred_tables": _join(pred["tables"]),
                    "gold_columns": _join(gold["columns"]),
                    "pred_columns": _join(pred["columns"]),
                    "gold_select_columns": _join(gold["select_columns"]),
                    "pred_select_columns": _join(pred["select_columns"]),
                    "gold_where_columns": _join(gold["where_columns"]),
                    "pred_where_columns": _join(pred["where_columns"]),
                    "gold_aggregates": _join(gold["aggregates"]),
                    "pred_aggregates": _join(pred["aggregates"]),
                    "question": new.get("question") or old.get("question", ""),
                    "evidence": new.get("evidence") or old.get("evidence", ""),
                    "golden_sql": new.get("golden_sql") or old.get("golden_sql", ""),
                    "predicted_sql": new.get("predicted_sql", ""),
                }
            )


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _sql_features(sql: str) -> dict[str, set[str]]:
    cleaned = _clean_sql(sql)
    features = {
        "tables": set(),
        "columns": set(),
        "select_columns": set(),
        "where_columns": set(),
        "aggregates": set(),
    }
    if not cleaned:
        return features
    try:
        parsed = sqlglot.parse_one(cleaned, read="sqlite")
    except Exception:
        try:
            parsed = sqlglot.parse_one(cleaned)
        except Exception:
            return features

    for table in parsed.find_all(exp.Table):
        name = table.name
        if name:
            features["tables"].add(_norm(name))
    for col in parsed.find_all(exp.Column):
        if col.name:
            parts = [part for part in (col.table, col.name) if part]
            features["columns"].add(_norm(".".join(parts)))
            features["columns"].add(_norm(col.name))
    for expr in parsed.expressions:
        for col in expr.find_all(exp.Column):
            _add_column(features["select_columns"], col)
    where = parsed.args.get("where")
    if where:
        for col in where.find_all(exp.Column):
            _add_column(features["where_columns"], col)
    for func in parsed.find_all(exp.Func):
        name = func.sql_name().lower()
        if name in {"count", "sum", "avg", "min", "max"}:
            features["aggregates"].add(name)
    if "distinct" in cleaned.lower():
        features["aggregates"].add("distinct")
    if re.search(r"\bgroup\s+by\b", cleaned, re.I):
        features["aggregates"].add("group_by")
    if re.search(r"\border\s+by\b", cleaned, re.I):
        features["aggregates"].add("order_by")
    if re.search(r"\blimit\b", cleaned, re.I):
        features["aggregates"].add("limit")
    return features


def _bucket(row: dict[str, Any], gold: dict[str, set[str]], pred: dict[str, set[str]]) -> str:
    if row.get("correct"):
        return "exec_correct"
    status = _result_status(row)
    if status in {"api_error", "error"}:
        return "runtime_error"
    if status == "exec_error":
        return "exec_error"
    if not pred["tables"]:
        return "unparsed_or_empty"
    if pred["tables"] == gold["tables"]:
        if _core_columns(pred["columns"]) == _core_columns(gold["columns"]):
            if pred["aggregates"] == gold["aggregates"]:
                return "schema_close_execution_or_value"
            return "schema_close_aggregation_or_shape"
        if _overlap_ratio(_core_columns(pred["columns"]), _core_columns(gold["columns"])) >= 0.5:
            return "same_tables_partial_columns"
        return "same_tables_different_columns"
    if pred["tables"] & gold["tables"]:
        return "partial_table_overlap"
    return "different_tables"


def _failure_family(row: dict[str, Any], gold: dict[str, set[str]], pred: dict[str, set[str]]) -> str:
    status = _result_status(row)
    if status == "correct":
        return "exec_correct"
    if status in {"api_error", "error"}:
        return "runtime_error"
    if status == "exec_error":
        return "execution_failure"
    if not pred["tables"]:
        return "empty_or_unparsed_sql"

    gold_core = _core_columns(gold["columns"])
    pred_core = _core_columns(pred["columns"])
    gold_select = _core_columns(gold["select_columns"])
    pred_select = _core_columns(pred["select_columns"])
    gold_where = _core_columns(gold["where_columns"])
    pred_where = _core_columns(pred["where_columns"])

    if pred["tables"] == gold["tables"]:
        if pred_core == gold_core:
            if pred["aggregates"] != gold["aggregates"]:
                return "aggregation_or_row_grain"
            return "value_literal_or_execution_semantics"
        if pred_select != gold_select and _overlap_ratio(pred_core, gold_core) >= 0.5:
            return "output_column_or_target_grain"
        if pred_where != gold_where and _overlap_ratio(pred_core, gold_core) >= 0.5:
            return "predicate_field_landing"
        return "same_table_field_landing"

    if pred["tables"] & gold["tables"]:
        if pred["tables"] - gold["tables"]:
            return "extra_join_or_overexpanded_path"
        return "missing_join_or_underexpanded_path"
    return "wrong_schema_path"


def _result_status(row: dict[str, Any]) -> str:
    if row.get("correct"):
        return "correct"
    result = str(row.get("result", "") or "")
    if "insufficient balance" in result.lower() or "error code: 402" in result.lower():
        return "api_error"
    if result.startswith("EXEC_ERROR") or "execution error" in result.lower():
        return "exec_error"
    if result.startswith("ERROR"):
        return "error"
    return "wrong"


def _core_columns(cols: set[str]) -> set[str]:
    return {col.split(".")[-1] for col in cols if col}


def _overlap_ratio(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, min(len(left), len(right)))


def _clean_sql(sql: str) -> str:
    text = str(sql or "").strip()
    match = re.search(r"```(?:sql)?\s*(.*?)\s*```", text, re.DOTALL | re.I)
    if match:
        text = match.group(1).strip()
    text = re.sub(r"^\s*--[^\n]*(?:\n|$)", "", text)
    return text.strip()


def _add_column(target: set[str], col: exp.Column) -> None:
    if not col.name:
        return
    parts = [part for part in (col.table, col.name) if part]
    target.add(_norm(".".join(parts)))
    target.add(_norm(col.name))


def _norm(value: str) -> str:
    return value.strip().strip('"`[]').lower()


def _join(values: set[str]) -> str:
    return "|".join(sorted(values))


if __name__ == "__main__":
    main()
