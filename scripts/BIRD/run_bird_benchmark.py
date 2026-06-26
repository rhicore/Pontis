#!/usr/bin/env python3
"""Run BIRD through the external evaluation-agent flow.

This script is only an orchestration layer: it loads BIRD cases, schedules
workers, writes logs, and evaluates SQL execution equality.  BIRD-specific
business rules live under ``evaluation_agent``; Pontis core remains a generic
database agent.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluation_agent.bird_runner import load_bird_cases, run_case as run_eval_case
from evaluation_agent.models import BirdCase, EvaluationResult
from evaluation_agent.reflection import reflect_error, reflect_result
from scripts.BIRD.benchmark_runtime import (
    ProgressTracker,
    aggregate_efficiency,
    format_efficiency_line,
    format_execution_result,
)
from scripts.BIRD.common import (
    PONTIS_WORKSPACE_ROOT,
    get_benchmark_dir,
    get_db_dir,
    get_progress_path,
    get_results_dir,
    get_run_id,
    get_run_name,
    set_run_id,
)


logger = logging.getLogger(__name__)


BIRD_MAIN_AGENT_SYSTEM_PROMPT = """\
## BIRD benchmark

你正在回答 BIRD Text-to-SQL benchmark 问题。

user 会利用 plan 向你提交问题，完成数据库探索和 SQL 验证后，调用 `exit_plan` 提交结果。
`exit_plan.plan` 只允许包含一个 `sql` 代码块，代码块内是完整 SQL；不要在 plan 中写解释、步骤、原因或查询结果。
如被拒绝，必须优先按 evaluation agent 的反馈修改 SQL，并重新提交新的 SQL-only plan。
即使你认为反馈与你的数据库探索结论或业务理解冲突，也先执行反馈；不要用解释、rebuttal、等价改写或另一种格式化写法绕开反馈。
BIRD 中很多时间字段按原始文本存储。题面给出秒级时间但列值带毫秒或省略小时位时，优先按列中存储格式做前缀匹配，例如 `1:40%`，再基于匹配行回答。
"""


def parse_qids(raw: str | None) -> set[int] | None:
    if not raw:
        return None
    return {int(item.strip()) for item in raw.split(",") if item.strip()}


def group_cases(cases: list[BirdCase], limit_per_db: int | None) -> dict[str, list[BirdCase]]:
    by_db: dict[str, list[BirdCase]] = defaultdict(list)
    for case in cases:
        by_db[case.db_id].append(case)
    if limit_per_db is not None:
        by_db = {db_id: items[:limit_per_db] for db_id, items in by_db.items()}
    return dict(sorted(by_db.items()))


def cleanup_logs(db_map: dict[str, list[BirdCase]], *, train: bool) -> None:
    print("=== Cleanup ===")
    for db_id in sorted(db_map):
        bench_dir = get_benchmark_dir(db_id, train)
        if not bench_dir.exists():
            continue
        count = 0
        for old_log in bench_dir.glob("*.log"):
            old_log.unlink(missing_ok=True)
            count += 1
        if count:
            print(f"  [{db_id}] cleared {count} logs")
    print("Cleanup done\n")


def result_to_row(result: EvaluationResult, elapsed: float) -> dict:
    case = result.case
    efficiency = dict(result.candidate.efficiency or {})
    return {
        "run_id": get_run_id(),
        "db_id": case.db_id,
        "question_id": case.question_id,
        "difficulty": case.difficulty,
        "question": case.question,
        "evidence": case.evidence,
        "golden_sql": case.golden_sql,
        "predicted_sql": result.candidate.predicted_sql,
        "correct": result.correct,
        "result": result.result,
        "elapsed": round(elapsed, 1),
        "attempts": len(result.attempts),
        "final_action": result.candidate.action,
        "final_exit_plan_requested": result.candidate.exit_plan_requested,
        **efficiency,
    }


def apply_reflection_to_row(row: dict, reflection: dict | None) -> dict:
    if not reflection:
        return row
    row.update(reflection)
    return row


def error_row(case: BirdCase, elapsed: float, error: BaseException) -> dict:
    return {
        "run_id": get_run_id(),
        "db_id": case.db_id,
        "question_id": case.question_id,
        "difficulty": case.difficulty,
        "question": case.question,
        "evidence": case.evidence,
        "golden_sql": case.golden_sql,
        "predicted_sql": None,
        "correct": False,
        "result": "ERROR",
        "elapsed": round(elapsed, 1),
        "attempts": 0,
        "error": f"{type(error).__name__}: {error}",
    }


def write_case_log(
    bench_dir: Path,
    result: EvaluationResult,
    elapsed: float,
    reflection: dict | None = None,
) -> None:
    case = result.case
    lines = [
        f"Q{case.question_id} [{case.difficulty}] {result.result} {elapsed:.1f}s",
        f"Database: {case.db_id}",
        f"Question: {case.question}",
        f"Evidence: {case.evidence or '(none)'}",
        "",
        "Predicted SQL:",
        result.candidate.predicted_sql or "PARSE_ERROR",
        "",
        "Golden SQL:",
        case.golden_sql or "(none)",
        "",
        "Predicted execution result:",
        format_execution_result(result.predicted_execution),
    ]
    if result.golden_execution is not None:
        lines.extend(["", "Golden execution result:", format_execution_result(result.golden_execution)])

    if reflection:
        lines.extend(
            [
                "",
                "Reflection:",
                json.dumps(reflection, ensure_ascii=False, sort_keys=True),
            ]
        )

    for attempt in result.attempts:
        lines.extend(
            [
                "",
                f"--- Attempt {attempt.attempt}",
                f"Action: {attempt.action}",
                f"Exit plan requested: {attempt.exit_plan_requested}",
                f"Elapsed: {attempt.elapsed:.1f}s",
                f"Efficiency: {json.dumps(attempt.efficiency, ensure_ascii=False, sort_keys=True)}",
                "Request:",
                attempt.request,
                "",
                "Response:",
                attempt.raw_response,
                "",
                "Extracted SQL:",
                attempt.predicted_sql or "PARSE_ERROR",
            ]
        )

    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / f"q{case.question_id}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_error_log(
    bench_dir: Path,
    case: BirdCase,
    elapsed: float,
    error: BaseException,
    reflection: dict | None = None,
) -> None:
    lines = [
        f"Q{case.question_id} [{case.difficulty}] ERROR {elapsed:.1f}s",
        f"Database: {case.db_id}",
        f"Question: {case.question}",
        f"Evidence: {case.evidence or '(none)'}",
        "",
        f"Error: {type(error).__name__}: {error}",
    ]
    if reflection:
        lines.extend(["", "Reflection:", json.dumps(reflection, ensure_ascii=False, sort_keys=True)])
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / f"q{case.question_id}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_db_summary(bench_dir: Path, db_id: str, results: list[dict]) -> None:
    correct = sum(1 for row in results if row.get("correct"))
    total = len(results)
    pct = correct / total * 100 if total else 0.0

    by_diff: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in results:
        diff = row.get("difficulty", "?")
        by_diff[diff][1] += 1
        if row.get("correct"):
            by_diff[diff][0] += 1

    lines = [
        f"=== {db_id} Summary ===",
        f"Total: {correct}/{total} ({pct:.1f}%)",
        format_efficiency_line(results),
        "",
        "By difficulty:",
    ]
    for diff in ["simple", "moderate", "challenging", "?"]:
        c, t = by_diff.get(diff, [0, 0])
        if t:
            lines.append(f"  {diff}: {c}/{t} ({c / t * 100:.1f}%)")

    lines.extend(["", "Per query:"])
    for row in sorted(results, key=lambda item: item["question_id"]):
        status = "OK" if row.get("correct") else row.get("result", "FAIL")
        lines.append(
            f"  Q{row['question_id']} [{row.get('difficulty', '?')}] {status} "
            f"{row.get('elapsed', 0):.1f}s attempts={row.get('attempts', 0)} "
            f"rounds={row.get('llm_rounds', 0)} "
            f"cached_in={row.get('cached_input_tokens', 0)} "
            f"uncached_in={row.get('uncached_input_tokens', 0)} "
            f"out={row.get('output_tokens', 0)} "
            f"total={row.get('total_tokens', 0)}"
        )

    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "summary.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_total_summary(output_dir: Path, all_results: list[dict]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    by_db: dict[str, list[dict]] = defaultdict(list)
    for row in all_results:
        by_db[row["db_id"]].append(row)

    lines = ["=== BIRD Benchmark Summary ===", ""]
    total_correct = 0
    total_count = 0
    for db_id in sorted(by_db):
        rows = by_db[db_id]
        correct = sum(1 for row in rows if row.get("correct"))
        total = len(rows)
        total_correct += correct
        total_count += total
        lines.append(f"Database: {db_id} - {correct}/{total} ({correct / total * 100:.1f}%)")
    pct = total_correct / total_count * 100 if total_count else 0.0
    lines.append(f"\nTotal: {total_correct}/{total_count} ({pct:.1f}%)")
    lines.append(format_efficiency_line(all_results))

    by_diff: dict[str, list[dict]] = defaultdict(list)
    for row in all_results:
        by_diff[row.get("difficulty") or "unknown"].append(row)
    lines.append("\nBy difficulty:")
    for diff in ["simple", "moderate", "challenging", "unknown"]:
        rows = by_diff.get(diff, [])
        if rows:
            correct = sum(1 for row in rows if row.get("correct"))
            lines.append(f"  {diff}: {correct}/{len(rows)} ({correct / len(rows) * 100:.1f}%)")

    lines.append("\nEfficiency by database:")
    for db_id in sorted(by_db):
        lines.append(f"  {db_id}: {format_efficiency_line(by_db[db_id])}")

    text = "\n".join(lines) + "\n"
    (output_dir / "benchmark_summary.log").write_text(text, encoding="utf-8")
    print(f"\n{text}")


def write_structured_outputs(output_dir: Path, all_results: list[dict]) -> None:
    results_dir = output_dir / "results"
    evaluation_dir = output_dir / "evaluation"
    results_dir.mkdir(parents=True, exist_ok=True)
    evaluation_dir.mkdir(parents=True, exist_ok=True)

    (results_dir / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in all_results),
        encoding="utf-8",
    )
    predictions = {
        str(row["question_id"]): row.get("predicted_sql")
        for row in all_results
        if "question_id" in row
    }
    (results_dir / "predictions.json").write_text(
        json.dumps(predictions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    total = len(all_results)
    correct = sum(1 for row in all_results if row.get("correct"))
    by_db: dict[str, list[dict]] = defaultdict(list)
    by_diff: dict[str, list[dict]] = defaultdict(list)
    for row in all_results:
        by_db[row.get("db_id", "unknown")].append(row)
        by_diff[row.get("difficulty") or "unknown"].append(row)

    summary = {
        "run_id": get_run_id(),
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else 0.0,
        "performance": aggregate_efficiency(all_results),
        "by_database": {
            db_id: {
                "total": len(rows),
                "correct": sum(1 for row in rows if row.get("correct")),
                "accuracy": sum(1 for row in rows if row.get("correct")) / len(rows) if rows else 0.0,
                "performance": aggregate_efficiency(rows),
            }
            for db_id, rows in sorted(by_db.items())
        },
        "by_difficulty": {
            diff: {
                "total": len(rows),
                "correct": sum(1 for row in rows if row.get("correct")),
                "accuracy": sum(1 for row in rows if row.get("correct")) / len(rows) if rows else 0.0,
                "performance": aggregate_efficiency(rows),
            }
            for diff, rows in sorted(by_diff.items())
        },
    }
    (evaluation_dir / "evaluation.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        "# Pontis BIRD Evaluation",
        "",
        f"Total: {correct}/{total} ({summary['accuracy'] * 100:.2f}%)",
        "",
        "## Efficiency",
        "",
    ]
    averages = summary["performance"]["averages"]
    totals = summary["performance"]["totals"]
    lines.extend(
        [
            f"- LLM Rounds / Query: {averages['llm_rounds_per_query']:.3f}",
            f"- Cached Input Tokens / Query: {averages['cached_input_tokens_per_query']:.3f}",
            f"- Uncached Input Tokens / Query: {averages['uncached_input_tokens_per_query']:.3f}",
            f"- Output Tokens / Query: {averages['output_tokens_per_query']:.3f}",
            f"- Total Tokens / Query: {averages['total_tokens_per_query']:.3f}",
            f"- Total Tokens: {totals['total_tokens']}",
            "",
            "## By Database",
        ]
    )
    for db_id, item in summary["by_database"].items():
        lines.append(f"- {db_id}: {item['correct']}/{item['total']} ({item['accuracy'] * 100:.2f}%)")
    lines.extend(["", "## By Difficulty"])
    for diff, item in summary["by_difficulty"].items():
        lines.append(f"- {diff}: {item['correct']}/{item['total']} ({item['accuracy'] * 100:.2f}%)")
    (evaluation_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_database(
    db_id: str,
    cases: list[BirdCase],
    args: argparse.Namespace,
    tracker: ProgressTracker,
) -> list[dict]:
    print(f"[{db_id}] {len(cases)} queries - start")
    bench_dir = get_benchmark_dir(db_id, args.train)
    bench_dir.mkdir(parents=True, exist_ok=True)
    tracker.start_test(db_id)

    def run_one(case: BirdCase) -> dict:
        started = time.time()
        try:
            result = run_eval_case(
                case,
                train=args.train,
                main_agent_prompt=BIRD_MAIN_AGENT_SYSTEM_PROMPT,
            )
            elapsed = time.time() - started
            row = result_to_row(result, elapsed)
            reflection = None
            if args.reflection and not result.correct:
                reflection = reflect_result(
                    result,
                    get_db_dir(case.db_id, args.train),
                    rounds=args.reflection_rounds,
                )
                apply_reflection_to_row(row, reflection)
            write_case_log(bench_dir, result, elapsed, reflection=reflection)
            status = "OK" if row["correct"] else "FAIL"
            print(
                f"  Q{case.question_id} [{case.difficulty}] {status} {row['result']} "
                f"({elapsed:.1f}s) attempts={row.get('attempts', 0)} "
                f"rounds={row.get('llm_rounds', 0)} tokens={row.get('total_tokens', 0)}"
            )
            return row
        except Exception as exc:
            elapsed = time.time() - started
            reflection = None
            if args.reflection:
                try:
                    reflection = reflect_error(
                        case,
                        get_db_dir(case.db_id, args.train),
                        exc,
                        rounds=args.reflection_rounds,
                    )
                except Exception as reflect_exc:
                    reflection = {
                        "reflection_primary_error_category": "DB_EXPLORATION_FIXABLE",
                        "reflection_secondary_error_category": "",
                        "reflection_reason": f"reflection failed: {type(reflect_exc).__name__}: {reflect_exc}",
                        "reflection_fix_hint": "inspect runtime error manually.",
                    }
            write_error_log(bench_dir, case, elapsed, exc, reflection=reflection)
            print(f"  Q{case.question_id} [{case.difficulty}] ERROR: {exc}")
            return apply_reflection_to_row(error_row(case, elapsed, exc), reflection)

    results: list[dict] = []
    correct_so_far = 0
    done_so_far = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, case): case.question_id for case in cases}
        for future in as_completed(futures):
            row = future.result()
            results.append(row)
            done_so_far += 1
            if row.get("correct"):
                correct_so_far += 1
            tracker.update(db_id, done_so_far, correct_so_far)

    results.sort(key=lambda item: item["question_id"])
    write_db_summary(bench_dir, db_id, results)
    correct = sum(1 for row in results if row.get("correct"))
    pct = correct / len(results) * 100 if results else 0.0
    print(f"[{db_id}] => {correct}/{len(results)} ({pct:.1f}%)")
    tracker.finish(db_id, correct, len(results))
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run BIRD through the Pontis evaluation agent")
    parser.add_argument("--train", action="store_true", help="run BIRD train split; default is dev")
    parser.add_argument("--db", help="comma-separated database ids to run")
    parser.add_argument("--qids", help="comma-separated question ids to run")
    parser.add_argument("--limit", type=int, help="per-database case limit")
    parser.add_argument("--workers", type=int, default=1, help="parallel cases per database")
    parser.add_argument("--db-workers", type=int, default=1, help="parallel databases")
    parser.add_argument("--reflection", action="store_true", help="classify failed cases into reflection categories")
    parser.add_argument("--reflection-rounds", type=int, default=1, help="retry rounds for reflection JSON output")
    parser.add_argument("--run-id", help="output run id")
    parser.add_argument("--output-dir", type=Path, help="directory for structured results")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.run_id:
        set_run_id(args.run_id)
    os.environ.setdefault("PONTIS_CONTEXT_RUN_ID", get_run_name(args.train))
    os.environ.setdefault(
        "PONTIS_CONTEXT_DIR",
        str(PONTIS_WORKSPACE_ROOT / "context"),
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cases = load_bird_cases(train=args.train, db=args.db, qids=parse_qids(args.qids))
    by_db = group_cases(cases, args.limit)
    total_queries = sum(len(items) for items in by_db.values())

    mode_label = "Train" if args.train else "Dev"
    print(f"=== BIRD {mode_label} Benchmark ===")
    print(f"Databases: {len(by_db)}, Queries: {total_queries}")
    print(f"DB workers: {args.db_workers}, Query workers/db: {args.workers}")
    print("Review policy: hard guard until fixed; warn once; LLM review up to 2 rounds")
    print(f"Reflection: {'on' if args.reflection else 'off'}")
    print(f"Run id: {get_run_id()}")
    print(f"Runtime logs: {PONTIS_WORKSPACE_ROOT / 'runtime_logs' / get_run_name(args.train)}")

    output_dir = args.output_dir or get_results_dir(args.train)
    print(f"Results: {output_dir}\n")

    cleanup_logs(by_db, train=args.train)
    tracker = ProgressTracker(by_db, get_progress_path(args.train))

    all_results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.db_workers) as db_pool:
        futures = {
            db_pool.submit(run_database, db_id, cases_for_db, args, tracker): db_id
            for db_id, cases_for_db in by_db.items()
        }
        for future in as_completed(futures):
            db_id = futures[future]
            try:
                all_results.extend(future.result())
            except Exception as exc:
                logger.exception("[%s] fatal error: %s", db_id, exc)

    if all_results:
        all_results.sort(key=lambda row: (row["db_id"], row["question_id"]))
        write_total_summary(output_dir / "evaluation", all_results)
        write_structured_outputs(output_dir, all_results)


if __name__ == "__main__":
    main()
