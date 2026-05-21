#!/usr/bin/env python3
"""Analyze table/column/value alignment errors from BIRD benchmark logs."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import sqlglot
from sqlglot import exp


FIELD_RE = re.compile(
    r"^(Question|Evidence|Predicted SQL|Golden SQL):\s*(.*?)(?=\n(?:Question|Evidence|Predicted SQL|Golden SQL):|\n---|\Z)",
    re.M | re.S,
)
HEADER_RE = re.compile(r"^Q(?P<qid>\d+)\s+\[(?P<difficulty>[^\]]+)\]\s+(?P<status>\w+)")


@dataclass(frozen=True)
class SqlParts:
    tables: frozenset[str]
    columns: frozenset[str]
    values: frozenset[str]
    parse_error: str | None = None


def _field(text: str, name: str) -> str:
    for match in FIELD_RE.finditer(text):
        if match.group(1) == name:
            return match.group(2).strip()
    return ""


def _normalize_identifier(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().strip("`\"[]").lower()


def _normalize_literal(value: object) -> str:
    text = str(value).strip()
    if text.startswith(("'", '"')) and text.endswith(("'", '"')) and len(text) >= 2:
        text = text[1:-1]
    return text.lower()


def _load_schema(data_root: Path, db_name: str) -> dict[str, set[str]]:
    db_file = data_root / db_name / f"{db_name}.sqlite"
    if not db_file.exists():
        return {}
    schema: dict[str, set[str]] = {}
    with sqlite3.connect(str(db_file)) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
        for (table,) in rows:
            try:
                cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            except sqlite3.Error:
                continue
            schema[_normalize_identifier(table)] = {
                _normalize_identifier(col[1]) for col in cols
            }
    return schema


def _table_aliases(tree: exp.Expression) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        name = _normalize_identifier(table.name)
        if not name:
            continue
        aliases[name] = name
        if table.alias:
            aliases[_normalize_identifier(table.alias)] = name
    return aliases


def _cte_names(tree: exp.Expression) -> set[str]:
    names: set[str] = set()
    for cte in tree.find_all(exp.CTE):
        if cte.alias:
            names.add(_normalize_identifier(cte.alias))
    return names


def _resolve_column(
    column: exp.Column,
    aliases: dict[str, str],
    schema: dict[str, set[str]],
    active_tables: set[str],
) -> str:
    col = _normalize_identifier(column.name)
    table_ref = _normalize_identifier(column.table)
    if table_ref:
        table = aliases.get(table_ref, table_ref)
        return f"{table}.{col}"

    candidates = [table for table in active_tables if col in schema.get(table, set())]
    if len(candidates) == 1:
        return f"{candidates[0]}.{col}"
    return f"?.{col}"


def _predicate_literals(tree: exp.Expression) -> set[str]:
    values: set[str] = set()
    binary_types = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike)

    def is_column_expr(node: exp.Expression | None) -> bool:
        if isinstance(node, exp.Column):
            return True
        if isinstance(node, exp.Cast):
            return isinstance(node.this, exp.Column)
        return False

    for node in tree.find_all(*binary_types):
        left = node.args.get("this")
        right = node.args.get("expression")
        if is_column_expr(left) and isinstance(right, exp.Literal):
            values.add(_normalize_literal(right.this))
        elif isinstance(left, exp.Literal) and is_column_expr(right):
            values.add(_normalize_literal(left.this))

    for node in tree.find_all(exp.In):
        if is_column_expr(node.args.get("this")):
            for literal in node.find_all(exp.Literal):
                values.add(_normalize_literal(literal.this))

    for node in tree.find_all(exp.Between):
        if is_column_expr(node.args.get("this")):
            for key in ("low", "high"):
                literal = node.args.get(key)
                if isinstance(literal, exp.Literal):
                    values.add(_normalize_literal(literal.this))
    return values


def _table_names(tree: exp.Expression) -> set[str]:
    cte_names = _cte_names(tree)
    names = set()
    for table in tree.find_all(exp.Table):
        name = _normalize_identifier(table.name)
        if name and name not in cte_names:
            names.add(name)
    return names


def parse_sql(sql: str, schema: dict[str, set[str]]) -> SqlParts:
    if not sql:
        return SqlParts(frozenset(), frozenset(), frozenset(), "empty_sql")
    try:
        tree = sqlglot.parse_one(sql, read="sqlite")
    except Exception as exc:
        return SqlParts(frozenset(), frozenset(), frozenset(), f"{type(exc).__name__}: {exc}")

    aliases = _table_aliases(tree)
    tables = _table_names(tree)
    columns = {
        _resolve_column(col, aliases, schema, tables)
        for col in tree.find_all(exp.Column)
        if col.name and col.name != "*"
    }
    values = _predicate_literals(tree)
    return SqlParts(frozenset(tables), frozenset(columns), frozenset(values))


def iter_logs(log_root: Path) -> Iterable[Path]:
    return sorted(log_root.glob("*/benchmark/q*.log"))


def analyze(log_root: Path, data_root: Path) -> dict:
    schema_cache: dict[str, dict[str, set[str]]] = {}
    totals = Counter()
    by_db: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, list[dict]] = defaultdict(list)

    for path in iter_logs(log_root):
        text = path.read_text(errors="ignore")
        header = HEADER_RE.match(text.splitlines()[0] if text.splitlines() else "")
        if not header:
            continue

        db = path.parts[-3]
        qid = header.group("qid")
        status = header.group("status")
        pred_sql = _field(text, "Predicted SQL")
        gold_sql = _field(text, "Golden SQL")
        question = _field(text, "Question")

        schema = schema_cache.setdefault(db, _load_schema(data_root, db))
        pred = parse_sql(pred_sql, schema)
        gold = parse_sql(gold_sql, schema)

        table_missing = gold.tables - pred.tables
        table_extra = pred.tables - gold.tables
        col_missing = gold.columns - pred.columns
        col_extra = pred.columns - gold.columns
        value_missing = gold.values - pred.values
        value_extra = pred.values - gold.values

        table_mismatch = bool(table_missing or table_extra)
        column_mismatch = bool(col_missing or col_extra)
        value_mismatch = bool(value_missing or value_extra)
        any_link_mismatch = table_mismatch or column_mismatch or value_mismatch

        keys = ["all", f"status_{status.lower()}"]
        if status != "CORRECT":
            keys.append("incorrect")
        for key in keys:
            totals[key] += 1
            by_db[db][key] += 1
            for name, flag in [
                ("table_mismatch", table_mismatch),
                ("column_mismatch", column_mismatch),
                ("value_mismatch", value_mismatch),
                ("any_link_mismatch", any_link_mismatch),
                ("pred_parse_error", bool(pred.parse_error)),
                ("gold_parse_error", bool(gold.parse_error)),
            ]:
                if flag:
                    totals[f"{key}_{name}"] += 1
                    by_db[db][f"{key}_{name}"] += 1

        for label, flag, missing, extra in [
            ("table_mismatch", table_mismatch, table_missing, table_extra),
            ("column_mismatch", column_mismatch, col_missing, col_extra),
            ("value_mismatch", value_mismatch, value_missing, value_extra),
            ("any_link_mismatch", any_link_mismatch, set(), set()),
        ]:
            if flag and status != "CORRECT" and len(examples[label]) < 12:
                examples[label].append(
                    {
                        "path": str(path),
                        "qid": qid,
                        "db": db,
                        "status": status,
                        "question": question,
                        "missing": sorted(missing),
                        "extra": sorted(extra),
                        "pred_sql": pred_sql,
                        "gold_sql": gold_sql,
                    }
                )

    return {
        "log_root": str(log_root),
        "data_root": str(data_root),
        "totals": dict(totals),
        "by_db": {db: dict(counter) for db, counter in sorted(by_db.items())},
        "examples": dict(examples),
    }


def _pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "n/a"
    return f"{part / whole * 100:.1f}%"


def print_report(result: dict, show_examples: int) -> None:
    totals = Counter(result["totals"])

    def line(scope: str, label: str) -> None:
        n = totals[scope]
        print(f"\n{label}: {n}")
        for metric in [
            "table_mismatch",
            "column_mismatch",
            "value_mismatch",
            "any_link_mismatch",
            "pred_parse_error",
        ]:
            count = totals[f"{scope}_{metric}"]
            print(f"  {metric:18s} {count:4d}  {_pct(count, n)}")

    print(f"Log root: {result['log_root']}")
    print(f"Data root: {result['data_root']}")
    line("all", "All completed logs")
    line("incorrect", "Incorrect logs")
    line("status_correct", "Correct logs")

    print("\nBy DB, incorrect only:")
    for db, row in result["by_db"].items():
        n = row.get("incorrect", 0)
        if not n:
            continue
        print(
            f"  {db:26s} n={n:3d} "
            f"table={_pct(row.get('incorrect_table_mismatch', 0), n):>6s} "
            f"col={_pct(row.get('incorrect_column_mismatch', 0), n):>6s} "
            f"value={_pct(row.get('incorrect_value_mismatch', 0), n):>6s} "
            f"any={_pct(row.get('incorrect_any_link_mismatch', 0), n):>6s}"
        )

    if show_examples <= 0:
        return
    for label, rows in result["examples"].items():
        print(f"\nExamples: {label}")
        for row in rows[:show_examples]:
            print(f"- {row['path']} | {row['status']} | Q{row['qid']}")
            print(f"  Q: {row['question']}")
            if row["missing"]:
                print(f"  missing gold: {row['missing']}")
            if row["extra"]:
                print(f"  extra pred: {row['extra']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log-root",
        type=Path,
        default=Path("workspace/baselines/pontis/runtime_logs/bird_dev_5.20"),
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("workspace/baselines/pontis/data/bird_dev/dev_databases"),
    )
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--examples", type=int, default=3)
    args = parser.parse_args()

    result = analyze(args.log_root, args.data_root)
    print_report(result, args.examples)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
