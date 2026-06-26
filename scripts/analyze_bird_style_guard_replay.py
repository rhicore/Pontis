#!/usr/bin/env python3
"""Replay deterministic BIRD SQL output guard on a benchmark result file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation_agent.sql_output_guard import bird_sql_output_guard


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results_jsonl", type=Path)
    parser.add_argument("--qids", help="Comma-separated question ids to include.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    qids = None
    if args.qids:
        qids = {int(item.strip()) for item in args.qids.split(",") if item.strip()}

    rows = []
    for line in args.results_jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if qids is not None and int(row["question_id"]) not in qids:
            continue
        rows.append(row)

    wrong = []
    hard_covered = []
    warn_covered = []
    correct_hard_hit = []
    correct_warn_hit = []
    details = []

    for row in rows:
        guard = bird_sql_output_guard(
            row.get("predicted_sql") or "",
            question=row.get("question") or "",
            evidence=row.get("evidence") or "",
        )
        qid = int(row["question_id"])
        if row.get("correct"):
            if guard.hard:
                correct_hard_hit.append(qid)
            if guard.warnings:
                correct_warn_hit.append(qid)
        else:
            wrong.append(qid)
            if guard.hard:
                hard_covered.append(qid)
            if guard.warnings:
                warn_covered.append(qid)
        if guard.hard or guard.warnings:
            details.append((qid, bool(row.get("correct")), guard.hard, guard.warnings))

    uncovered = sorted(set(wrong) - set(hard_covered))
    print(f"rows: {len(rows)}")
    print(f"wrong: {len(wrong)} {sorted(wrong)}")
    print(f"wrong hard-covered: {len(hard_covered)} {sorted(hard_covered)}")
    print(f"wrong not hard-covered: {len(uncovered)} {uncovered}")
    print(f"correct hard-hit: {len(correct_hard_hit)} {sorted(correct_hard_hit)}")
    print(f"correct warn-hit: {len(correct_warn_hit)} {sorted(correct_warn_hit)}")
    print()
    for qid, correct, hard, warnings in sorted(details):
        print(f"Q{qid} correct={correct}")
        for item in hard:
            print(f"  HARD {item}")
        for item in warnings:
            print(f"  WARN {item}")


if __name__ == "__main__":
    main()
