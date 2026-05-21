#!/usr/bin/env python3
"""Check whether predicted SQL covers all tables/columns used by gold SQL."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from analyze_schema_linking_errors import _field, _load_schema, parse_sql


HEADER_RE = re.compile(r"^Q(?P<qid>\d+)\s+\[(?P<difficulty>[^\]]+)\]\s+(?P<status>\w+)")


def iter_log_rows(log_root: Path, data_root: Path):
    schema_cache: dict[str, dict[str, set[str]]] = {}
    for path in sorted(log_root.glob("*/benchmark/q*.log")):
        text = path.read_text(errors="ignore")
        header = HEADER_RE.match(text.splitlines()[0] if text.splitlines() else "")
        if not header:
            continue
        db = path.parts[-3]
        schema = schema_cache.setdefault(db, _load_schema(data_root, db))
        pred = parse_sql(_field(text, "Predicted SQL"), schema)
        gold = parse_sql(_field(text, "Golden SQL"), schema)
        missing_tables = gold.tables - pred.tables
        missing_columns = gold.columns - pred.columns
        covered = (
            not pred.parse_error
            and not gold.parse_error
            and not missing_tables
            and not missing_columns
        )
        yield {
            "path": str(path),
            "db": db,
            "qid": header.group("qid"),
            "difficulty": header.group("difficulty"),
            "status": header.group("status"),
            "covered": covered,
            "missing_tables": sorted(missing_tables),
            "missing_columns": sorted(missing_columns),
            "pred_parse_error": pred.parse_error,
            "gold_parse_error": gold.parse_error,
            "question": _field(text, "Question"),
            "evidence": _field(text, "Evidence"),
            "pred_sql": _field(text, "Predicted SQL"),
            "gold_sql": _field(text, "Golden SQL"),
        }


def pct(n: int, d: int) -> str:
    return "n/a" if d == 0 else f"{n / d * 100:.1f}%"


def analyze(log_root: Path, data_root: Path) -> dict:
    rows = list(iter_log_rows(log_root, data_root))
    scopes: dict[str, list[dict]] = {
        "all": rows,
        "correct": [r for r in rows if r["status"] == "CORRECT"],
        "incorrect": [r for r in rows if r["status"] != "CORRECT"],
    }
    for status in sorted({r["status"] for r in rows}):
        scopes[status.lower()] = [r for r in rows if r["status"] == status]

    summary = {}
    for name, scoped in scopes.items():
        covered = sum(1 for r in scoped if r["covered"])
        summary[name] = {
            "total": len(scoped),
            "covered": covered,
            "coverage": covered / len(scoped) if scoped else None,
        }

    by_db: dict[str, dict] = {}
    for db in sorted({r["db"] for r in rows}):
        db_rows = [r for r in rows if r["db"] == db]
        covered = sum(1 for r in db_rows if r["covered"])
        bad_rows = [r for r in db_rows if r["status"] != "CORRECT"]
        bad_covered = sum(1 for r in bad_rows if r["covered"])
        by_db[db] = {
            "total": len(db_rows),
            "covered": covered,
            "coverage": covered / len(db_rows) if db_rows else None,
            "incorrect_total": len(bad_rows),
            "incorrect_covered": bad_covered,
            "incorrect_coverage": bad_covered / len(bad_rows) if bad_rows else None,
        }

    missing_table_counts = Counter()
    missing_column_counts = Counter()
    missing_column_by_db: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        if row["status"] == "CORRECT" or row["covered"]:
            continue
        missing_table_counts.update(row["missing_tables"])
        missing_column_counts.update(row["missing_columns"])
        missing_column_by_db[row["db"]].update(row["missing_columns"])

    return {
        "log_root": str(log_root),
        "data_root": str(data_root),
        "summary": summary,
        "by_db": by_db,
        "top_missing_tables_in_incorrect": missing_table_counts.most_common(50),
        "top_missing_columns_in_incorrect": missing_column_counts.most_common(100),
        "top_missing_columns_by_db_in_incorrect": {
            db: counter.most_common(30) for db, counter in sorted(missing_column_by_db.items())
        },
        "uncovered_incorrect_examples": [
            row for row in rows if row["status"] != "CORRECT" and not row["covered"]
        ][:200],
    }


def print_report(result: dict, examples: int) -> None:
    print(f"Log root: {result['log_root']}")
    print(f"Data root: {result['data_root']}")
    print("\nCoverage:")
    for name in ["all", "correct", "incorrect", "wrong", "error", "exec_error"]:
        row = result["summary"].get(name)
        if not row or row["total"] == 0:
            continue
        print(f"  {name:10s} {row['covered']:4d}/{row['total']:<4d} {pct(row['covered'], row['total']):>6s}")

    print("\nBy DB:")
    for db, row in result["by_db"].items():
        print(
            f"  {db:26s} all={row['covered']:4d}/{row['total']:<4d} {pct(row['covered'], row['total']):>6s} "
            f"incorrect={row['incorrect_covered']:3d}/{row['incorrect_total']:<3d} "
            f"{pct(row['incorrect_covered'], row['incorrect_total']):>6s}"
        )

    print("\nTop missing tables in incorrect uncovered:")
    for name, count in result["top_missing_tables_in_incorrect"][:20]:
        print(f"  {name:30s} {count}")

    print("\nTop missing columns in incorrect uncovered:")
    for name, count in result["top_missing_columns_in_incorrect"][:30]:
        print(f"  {name:40s} {count}")

    if examples:
        print("\nExamples:")
        for row in result["uncovered_incorrect_examples"][:examples]:
            print(f"- {row['path']} | {row['status']} | Q{row['qid']}")
            print(f"  Q: {row['question'][:220]}")
            if row["missing_tables"]:
                print(f"  missing tables: {row['missing_tables']}")
            if row["missing_columns"]:
                print(f"  missing columns: {row['missing_columns'][:12]}")


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
    parser.add_argument("--examples", type=int, default=8)
    args = parser.parse_args()

    result = analyze(args.log_root, args.data_root)
    print_report(result, args.examples)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
