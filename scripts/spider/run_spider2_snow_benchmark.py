#!/usr/bin/env python3
"""Run Pontis SQL generation benchmark for Spider2-Snow.

Spider2-Snow official evaluation executes SQL on Snowflake and compares output
CSV files. This runner covers Pontis-side SQL generation: it runs one Pontis
agent per case, validates the final answer shape and Snowflake parseability,
and writes predictions for later official execution.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import sqlglot
from sqlglot import exp

PONTIS_ROOT = Path(__file__).resolve().parents[2]
TEXT2SQL_ROOT = PONTIS_ROOT.parent
for _path in (PONTIS_ROOT, TEXT2SQL_ROOT / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from agent.config import AgentSpec, create_agent
from agent.guardrail import build_guardrails
from scripts.BIRD.benchmark_runtime import TraceCollector, extract_sql, get_agent_efficiency_metrics
from scripts.spider.common import (
    SpiderSnowCase,
    SPIDER2_SNOW_CREDENTIAL,
    SPIDER2_SNOW_EVAL_SUITE,
    get_project_dir,
    get_results_dir,
    get_run_id,
    get_run_name,
    get_runtime_dir,
    group_cases_by_db,
    ensure_spider2_snow_neo4j,
    load_spider2_snow_cases,
    parse_csv_arg,
    set_run_id,
    sync_spider2_snow_pontis_config,
)
from scripts.spider.result_match import evaluate_spider_result_directory
from utils.context_dump import reset_context_dump_meta, set_context_dump_meta


logger = logging.getLogger(__name__)
SPIDER_SNOW_SQL_RETRY_LIMIT = 3
_SQL_ONLY_BLOCK_RE = re.compile(r"^\s*```sql\s*\n?.*?\n?```\s*$", re.IGNORECASE | re.DOTALL)


def run_case(case: SpiderSnowCase) -> dict:
    project_dir = get_project_dir(case.db_id)
    if not project_dir.exists():
        raise FileNotFoundError(f"Spider2-Snow project not prepared: {project_dir}. Run scripts/spider/extract.py first.")
    trace = TraceCollector()
    agent = create_agent(
        str(project_dir),
        _build_spider_snow_agent_spec(case.db_id),
        trace_callback=trace.callback,
    )

    token = set_context_dump_meta(
        run_id=os.environ.get("PONTIS_CONTEXT_RUN_ID") or get_run_id(),
        db_id=case.db_id,
        question_id=case.instance_id,
    )
    attempts: list[dict] = []
    sql: str | None = None
    validation_error: str | None = None
    started = time.time()
    try:
        feedback = ""
        for attempt_no in range(1, SPIDER_SNOW_SQL_RETRY_LIMIT + 1):
            request = (
                _build_initial_request(case)
                if attempt_no == 1
                else _build_revision_request(case, sql=sql, validation_error=validation_error or feedback)
            )
            response = agent.chat(request)
            sql = extract_sql(response)
            validation_error = _response_shape_issue(response) or _snowflake_select_issue(sql)
            attempts.append(
                {
                    "attempt": attempt_no,
                    "request": request,
                    "raw_response": response,
                    "predicted_sql": sql,
                    "validation_error": validation_error,
                    "efficiency": get_agent_efficiency_metrics(agent),
                }
            )
            if validation_error is None:
                break
            feedback = validation_error
    finally:
        reset_context_dump_meta(token)

    elapsed = time.time() - started
    status = "PASS" if sql and validation_error is None else "FAIL"
    row = {
        "run_id": get_run_id(),
        "run_name": get_run_name(),
        "instance_id": case.instance_id,
        "db_id": case.db_id,
        "instruction": case.instruction,
        "external_knowledge": case.external_knowledge,
        "predicted_sql": sql,
        "status": status,
        "validation_error": validation_error,
        "elapsed": round(elapsed, 1),
        "attempts": len(attempts),
        **get_agent_efficiency_metrics(agent),
    }
    _write_case_log(case, row, attempts, trace)
    return row


def _build_spider_snow_agent_spec(db_id: str) -> AgentSpec:
    spec = AgentSpec(
        projects=[db_id],
        effort="mid",
        max_rounds=18,
        tools=["find", "grep", "read", "jd", "meta", "query"],
        prompts=[
            "base",
            "tool",
            "ontology",
            "spider_snow",
            "effort",
            "guardrail",
            "project",
            "readme",
        ],
    )
    spec.guardrails = build_guardrails(spec, ["round_limit", "exploration_check"])
    return spec


def _build_initial_request(case: SpiderSnowCase) -> str:
    doc = case.external_knowledge or "无"
    return f"""\
Spider2-Snow instance: {case.instance_id}
Database id: {case.db_id}
External knowledge: {doc}
Instruction: {case.instruction}

请读取当前 Pontis 项目中的 `database/` DDL/table JSON 和必要的 `documents/` 文件，输出最终 Snowflake SQL。
最终回复只包含一个 ```sql``` 代码块。
"""


def _build_revision_request(case: SpiderSnowCase, *, sql: str | None, validation_error: str) -> str:
    doc = case.external_knowledge or "无"
    sql_text = sql or "无可解析 SQL"
    return f"""\
Spider2-Snow instance: {case.instance_id}
Database id: {case.db_id}
External knowledge: {doc}
Instruction: {case.instruction}

当前 SQL：
```sql
{sql_text}
```

修改要求：
{validation_error}

请重新输出一条 Snowflake 只读 SELECT/WITH SELECT SQL。最终回复只包含一个 ```sql``` 代码块。
"""


def _snowflake_select_issue(sql: str | None) -> str | None:
    if not sql:
        return "没有可解析 SQL；请输出唯一的 ```sql``` fenced code block。"
    try:
        tree = sqlglot.parse_one(sql, dialect="snowflake")
    except sqlglot.errors.SqlglotError as exc:
        return f"SQL 无法按 Snowflake 语法解析：{exc}。"
    if not _is_readonly_select(tree):
        return "最终 SQL 必须是一条只读 Snowflake SELECT 或 WITH ... SELECT 查询。"
    return None


def _response_shape_issue(response: str) -> str | None:
    if not _SQL_ONLY_BLOCK_RE.match(response or ""):
        return "最终回复必须只包含一个 ```sql``` fenced code block；不要在代码块前后输出解释文字。"
    return None


def _is_readonly_select(tree: exp.Expression) -> bool:
    if isinstance(tree, exp.Select):
        return True
    disallowed = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Merge)
    if isinstance(tree, disallowed):
        return False
    if any(isinstance(node, disallowed) for node in tree.walk()):
        return False
    return bool(tree.find(exp.Select))


def _write_case_log(case: SpiderSnowCase, row: dict, attempts: list[dict], trace: TraceCollector) -> None:
    log_dir = get_runtime_dir(case.db_id) / "benchmark"
    log_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"{case.instance_id} {row['status']} {row['elapsed']:.1f}s",
        f"Database: {case.db_id}",
        f"External knowledge: {case.external_knowledge or '(none)'}",
        f"Instruction: {case.instruction}",
        f"Validation error: {row.get('validation_error') or '(none)'}",
        "",
        "Predicted SQL:",
        row.get("predicted_sql") or "PARSE_ERROR",
        "",
        "Trace:",
        trace.detailed_trace_text(),
    ]
    for attempt in attempts:
        lines.extend(
            [
                "",
                f"--- Attempt {attempt['attempt']}",
                f"Validation error: {attempt.get('validation_error') or '(none)'}",
                "Request:",
                attempt["request"],
                "",
                "Response:",
                attempt["raw_response"],
            ]
        )
    (log_dir / f"{case.instance_id}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_summary(rows: list[dict]) -> Path:
    results_dir = get_results_dir()
    pontis_dir = results_dir / "pontis_agent"
    raw_dir = results_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    pontis_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = raw_dir / "results.jsonl"
    jsonl_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    predictions = {
        row["instance_id"]: row.get("predicted_sql")
        for row in rows
        if row.get("predicted_sql")
    }
    (raw_dir / "predictions.json").write_text(
        json.dumps(predictions, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sql_submission_dir = _write_official_sql_submission(rows, results_dir)
    total = len(rows)
    passed = sum(1 for row in rows if row.get("status") == "PASS")
    by_db: dict[str, list[dict]] = {}
    for row in rows:
        by_db.setdefault(row["db_id"], []).append(row)
    summary_json = {
        "run_id": get_run_id(),
        "run_name": get_run_name(),
        "total": total,
        "pass": passed,
        "pass_rate": passed / total if total else 0.0,
        "by_database": {
            db_id: {
                "total": len(items),
                "pass": sum(1 for item in items if item.get("status") == "PASS"),
            }
            for db_id, items in sorted(by_db.items())
        },
    }
    (pontis_dir / "results.json").write_text(
        json.dumps(summary_json, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        f"# Pontis Spider2-Snow SQL Generation",
        "",
        f"- run: `{get_run_name()}`",
        f"- total: {passed}/{total} PASS",
        f"- results: `{jsonl_path}`",
        f"- predictions: `{raw_dir / 'predictions.json'}`",
        f"- official SQL submission: `{sql_submission_dir}`",
        "",
        "## By Database",
        "",
    ]
    for db_id, items in sorted(by_db.items()):
        db_passed = sum(1 for item in items if item.get("status") == "PASS")
        lines.append(f"- `{db_id}`: {db_passed}/{len(items)} PASS")
    lines.extend([
        "",
        "| instance | db | status | attempts | elapsed |",
        "|---|---|---:|---:|---:|",
    ])
    for row in rows:
        lines.append(
            f"| `{row['instance_id']}` | `{row['db_id']}` | {row['status']} | {row['attempts']} | {row['elapsed']} |"
        )
    summary_path = pontis_dir / "summary.md"
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary_path


def _write_official_sql_submission(rows: list[dict], results_dir: Path) -> Path:
    sql_dir = results_dir / "official_submission_sql"
    if sql_dir.exists():
        for path in sql_dir.glob("*.sql"):
            path.unlink()
    sql_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        sql = (row.get("predicted_sql") or "").strip()
        if not sql:
            sql = 'SELECT NULL AS "output" WHERE 1 = 0;'
        (sql_dir / f"{row['instance_id']}.sql").write_text(sql.rstrip() + "\n", encoding="utf-8")
    return sql_dir


def _official_eval_command(*, sql_dir: Path, max_workers: int, timeout: int) -> list[str]:
    temp_dir = get_results_dir() / "official_eval_temp"
    return [
        sys.executable,
        "evaluate.py",
        "--mode",
        "sql",
        "--result_dir",
        str(sql_dir.resolve()),
        "--gold_dir",
        "gold",
        "--max_workers",
        str(max_workers),
        "--timeout",
        str(timeout),
        "--temp_dir",
        str(temp_dir.resolve()),
    ]


def _snowflake_credential_issue() -> str | None:
    if not SPIDER2_SNOW_CREDENTIAL.exists():
        return f"Snowflake credential file not found: {SPIDER2_SNOW_CREDENTIAL}"
    try:
        data = json.loads(SPIDER2_SNOW_CREDENTIAL.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return f"Snowflake credential file is not valid JSON: {exc}"
    user = data.get("user") or data.get("username")
    required = {
        "user": user,
        "password": data.get("password"),
        "account": data.get("account"),
    }
    missing = [key for key, value in required.items() if not str(value or "").strip()]
    if missing:
        return f"Snowflake credential is missing: {', '.join(missing)} in {SPIDER2_SNOW_CREDENTIAL}"
    placeholders = []
    for key, value in required.items():
        lowered = str(value).lower()
        if any(marker in lowered for marker in ("your_", "<", ">", "password", "username", "xxx")):
            placeholders.append(key)
    if placeholders:
        return f"Snowflake credential still has placeholder values: {', '.join(placeholders)} in {SPIDER2_SNOW_CREDENTIAL}"
    return None


def _run_official_evaluation(*, max_workers: int, timeout: int) -> dict:
    sql_dir = get_results_dir() / "official_submission_sql"
    if not sql_dir.exists():
        raise FileNotFoundError(f"Official SQL submission directory not found: {sql_dir}")
    if not SPIDER2_SNOW_EVAL_SUITE.exists():
        raise FileNotFoundError(f"Spider2-Snow evaluation suite not found: {SPIDER2_SNOW_EVAL_SUITE}")
    issue = _snowflake_credential_issue()
    if issue:
        raise RuntimeError(
            issue
            + "\n按 data/Spider2/assets/Snowflake_Guideline.md 获取账号/token 后，更新该 credential 文件再跑。"
        )

    command = _official_eval_command(sql_dir=sql_dir, max_workers=max_workers, timeout=timeout)
    log_path = get_results_dir() / "official_eval_stdout.log"
    started = time.time()
    completed = subprocess.run(
        command,
        cwd=SPIDER2_SNOW_EVAL_SUITE,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    log_path.write_text(completed.stdout, encoding="utf-8")
    result = {
        "command": command,
        "cwd": str(SPIDER2_SNOW_EVAL_SUITE),
        "returncode": completed.returncode,
        "elapsed": round(time.time() - started, 1),
        "stdout_log": str(log_path),
        "sql_submission_dir": str(sql_dir),
        "exec_result_dir": str(sql_dir) + "_csv",
        "correct_ids_csv": str(sql_dir) + ".csv",
    }
    (get_results_dir() / "official_eval.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Official Spider2-Snow evaluation failed. See {log_path}")
    return result


def _run_business_result_evaluation(instance_ids: set[str]) -> dict:
    predicted_result_dir = Path(str(get_results_dir() / "official_submission_sql") + "_csv")
    gold_root = SPIDER2_SNOW_EVAL_SUITE / "gold"
    rows = evaluate_spider_result_directory(
        predicted_result_dir=predicted_result_dir,
        gold_result_dir=gold_root / "exec_result",
        eval_config_path=gold_root / "spider2snow_eval.jsonl",
        instance_ids=instance_ids,
    )
    output_dir = get_results_dir() / "business_evaluation"
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.jsonl"
    results_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    correct = sum(1 for row in rows if row.get("business_correct"))
    summary = {
        "metric": "business_correct",
        "total": len(rows),
        "correct": correct,
        "accuracy": correct / len(rows) if rows else 0.0,
        "results": str(results_path),
    }
    summary_path = output_dir / "results.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**summary, "summary": str(summary_path)}


def _run_database(db_id: str, cases: list[SpiderSnowCase], *, workers: int) -> list[dict]:
    print(f"[{db_id}] {len(cases)} cases - start")
    rows: list[dict] = []
    if workers <= 1:
        for case in cases:
            row = _safe_run_case(case)
            rows.append(row)
            print(f"  {case.instance_id}: {row['status']}")
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_safe_run_case, case): case for case in cases}
            for future in as_completed(futures):
                case = futures[future]
                row = future.result()
                rows.append(row)
                print(f"  {case.instance_id}: {row['status']}")
    rows.sort(key=lambda row: row["instance_id"])
    passed = sum(1 for row in rows if row.get("status") == "PASS")
    print(f"[{db_id}] => {passed}/{len(rows)} PASS")
    return rows


def _safe_run_case(case: SpiderSnowCase) -> dict:
    try:
        return run_case(case)
    except Exception as exc:
        logger.exception("Spider2-Snow benchmark failed for %s", case.instance_id)
        return {
            "run_id": get_run_id(),
            "run_name": get_run_name(),
            "instance_id": case.instance_id,
            "db_id": case.db_id,
            "instruction": case.instruction,
            "external_knowledge": case.external_knowledge,
            "predicted_sql": None,
            "status": "ERROR",
            "validation_error": f"{type(exc).__name__}: {exc}",
            "elapsed": 0.0,
            "attempts": 0,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Comma-separated db_id filter.")
    parser.add_argument("--instances", help="Comma-separated Spider2-Snow instance_id filter.")
    parser.add_argument(
        "--dev-only",
        "--gold-sql-only",
        action="store_true",
        dest="dev_only",
        help="Only run Spider2-Snow cases that have local gold SQL files for correctness debugging.",
    )
    parser.add_argument("--limit", type=int, help="Limit selected cases before running.")
    parser.add_argument("--workers", type=int, default=1, help="parallel cases per database")
    parser.add_argument("--db-workers", type=int, default=1, help="parallel databases")
    parser.add_argument("--run-id")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--skip-official-eval",
        action="store_true",
        help="Only generate Pontis SQL files; do not execute the official Snowflake evaluator.",
    )
    parser.add_argument("--official-eval-workers", type=int, default=8)
    parser.add_argument("--official-eval-timeout", type=int, default=60)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.run_id:
        set_run_id(args.run_id)
    config_path = sync_spider2_snow_pontis_config()
    graph_config_path = ensure_spider2_snow_neo4j()

    cases = load_spider2_snow_cases(
        db=args.db,
        instances=parse_csv_arg(args.instances),
        limit=args.limit,
        dev_only=args.dev_only,
    )
    if not cases:
        raise SystemExit("No Spider2-Snow cases selected.")

    grouped = group_cases_by_db(cases)

    print(f"Spider2-Snow benchmark run: {get_run_name()} ({get_run_id()})")
    if args.dev_only:
        print("Split: dev-only / gold-SQL subset")
    print(f"Cases: {len(cases)}, databases: {len(grouped)}")
    print(f"DB workers: {args.db_workers}, Case workers/db: {args.workers}")
    print(f"Pontis config: {config_path}")
    print(f"Spider2-Snow Neo4j: {graph_config_path}")

    rows: list[dict] = []
    if args.db_workers <= 1:
        for db_id, db_cases in grouped.items():
            rows.extend(_run_database(db_id, db_cases, workers=args.workers))
    else:
        with ThreadPoolExecutor(max_workers=args.db_workers) as pool:
            futures = {
                pool.submit(_run_database, db_id, db_cases, workers=args.workers): db_id
                for db_id, db_cases in grouped.items()
            }
            for future in as_completed(futures):
                rows.extend(future.result())

    rows.sort(key=lambda row: (row["db_id"], row["instance_id"]))
    summary_path = _write_summary(rows)
    print(f"Summary: {summary_path}")
    if args.skip_official_eval:
        print(f"Official evaluation skipped. SQL submission: {get_results_dir() / 'official_submission_sql'}")
    else:
        print("Running official Spider2-Snow evaluation on Snowflake...")
        eval_result = _run_official_evaluation(
            max_workers=args.official_eval_workers,
            timeout=args.official_eval_timeout,
        )
        print(f"Official evaluation log: {eval_result['stdout_log']}")
        business_result = _run_business_result_evaluation(
            {row["instance_id"] for row in rows},
        )
        print(
            "Business result evaluation: "
            f"{business_result['correct']}/{business_result['total']} "
            f"({business_result['accuracy'] * 100:.2f}%)"
        )
        print(f"Business evaluation summary: {business_result['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
