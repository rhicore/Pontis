#!/usr/bin/env python3
"""CLI for the external BIRD evaluation agent prototype."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    pontis_root = Path(__file__).resolve().parents[1]
    text2sql_root = pontis_root.parent
    sys.path.insert(0, str(pontis_root))
    sys.path.insert(0, str(text2sql_root / "tools"))
else:
    pontis_root = Path(__file__).resolve().parents[1]
    text2sql_root = pontis_root.parent
    if str(text2sql_root / "tools") not in sys.path:
        sys.path.insert(0, str(text2sql_root / "tools"))

from evaluation_agent.bird_runner import load_bird_cases, run_case


def main() -> None:
    parser = argparse.ArgumentParser(description="Run BIRD cases through the external evaluation agent.")
    parser.add_argument("--train", action="store_true", help="Use BIRD train split; default is dev.")
    parser.add_argument("--db", help="Database id or comma-separated database ids.")
    parser.add_argument("--qids", help="Comma-separated question ids.")
    parser.add_argument("--limit", type=int, help="Maximum number of cases after filtering.")
    args = parser.parse_args()

    qids = None
    if args.qids:
        qids = [int(item.strip()) for item in args.qids.split(",") if item.strip()]

    cases = load_bird_cases(train=args.train, db=args.db, qids=qids, limit=args.limit)
    if not cases:
        print("No cases selected.", file=sys.stderr)
        sys.exit(1)

    for case in cases:
        result = run_case(case, train=args.train)
        row = {
            "db_id": result.case.db_id,
            "question_id": result.case.question_id,
            "difficulty": result.case.difficulty,
            "result": result.result,
            "correct": result.correct,
            "attempts": len(result.attempts),
            "final_action": result.candidate.action,
            "final_exit_plan_requested": result.candidate.exit_plan_requested,
            "predicted_sql": result.candidate.predicted_sql,
            "elapsed": round(result.candidate.elapsed, 1),
            **result.candidate.efficiency,
        }
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
