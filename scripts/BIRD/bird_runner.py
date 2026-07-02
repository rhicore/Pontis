"""Single-agent runner for BIRD cases."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterable

from agent.config import AgentSpec, create_agent
from agent.guardrail import build_guardrails
from scripts.BIRD.benchmark_runtime import (
    execute_sql,
    extract_sql,
    find_db_file,
    format_execution_result,
    get_agent_efficiency_metrics,
)
from scripts.BIRD.common import get_data_dir, get_db_base
from scripts.BIRD.models import BirdCase, BirdRunResult, CandidateReport
from scripts.BIRD.result_match import compare_execution_results
from scripts.BIRD.hard_guard import (
    bird_sql_output_guard,
    format_bird_sql_output_guard_force_feedback,
    format_bird_sql_output_guard_strict_feedback,
    format_bird_sql_output_guard_warning,
)
from utils.context_dump import reset_context_dump_meta, set_context_dump_meta


BIRD_SQL_RETRY_LIMIT = 12


def assign_question_ids(questions: list[dict]) -> list[dict]:
    normalized = []
    for idx, item in enumerate(questions):
        item = dict(item)
        if item.get("question_id") is None:
            item["question_id"] = idx
        normalized.append(item)
    return normalized


def load_bird_cases(
    *,
    train: bool = False,
    db: str | None = None,
    qids: Iterable[int] | None = None,
    limit: int | None = None,
) -> list[BirdCase]:
    data_dir = get_data_dir(train)
    json_path = data_dir / ("train.json" if train else "dev.json")
    rows = assign_question_ids(json.loads(json_path.read_text(encoding="utf-8")))
    if db:
        db_filter = {item.strip() for item in db.split(",") if item.strip()}
        rows = [row for row in rows if row.get("db_id") in db_filter]
    if qids:
        qid_set = {int(qid) for qid in qids}
        rows = [row for row in rows if int(row.get("question_id", 0)) in qid_set]
    if limit is not None:
        rows = rows[:limit]
    return [BirdCase.from_row(row) for row in rows]


def run_case(
    case: BirdCase,
    *,
    train: bool = False,
) -> BirdRunResult:
    db_dir = get_db_base(train) / case.db_id
    runner = PontisBirdRunner(Path(db_dir), case.db_id)
    return runner.run_case(case)


class PontisBirdRunner:
    """Run one BIRD case with one Pontis agent conversation."""

    def __init__(self, db_dir: Path, db_id: str):
        self.db_dir = Path(db_dir)
        self.db_id = db_id
        self.db_path = find_db_file(self.db_dir)
        if not self.db_path:
            raise FileNotFoundError(f"No SQLite database found under {self.db_dir}")

    def run_case(self, case: BirdCase) -> BirdRunResult:
        token = set_context_dump_meta(
            run_id=os.environ.get("PONTIS_CONTEXT_RUN_ID"),
            db_id=case.db_id,
            question_id=case.question_id,
        )
        try:
            return self._run_case(case)
        finally:
            reset_context_dump_meta(token)

    def _run_case(self, case: BirdCase) -> BirdRunResult:
        agent = create_agent(str(self.db_dir), _build_bird_agent_spec(self.db_id))
        attempts: list[CandidateReport] = []
        predicted_execution: set | str = "PARSE_ERROR"
        candidate: CandidateReport | None = None
        sql: str | None = None
        feedback: str | None = None
        execution_preview: str | None = None
        seen_deterministic_warnings: set[str] = set()
        force_feedback_seen = False

        for attempt_no in range(1, BIRD_SQL_RETRY_LIMIT + 1):
            request = (
                _build_initial_request(case)
                if attempt_no == 1
                else _build_revision_request(
                    case,
                    sql=sql,
                    feedback=feedback or "请重新提交一个唯一的 SQL fenced code block。",
                    execution_preview=execution_preview,
                )
            )
            started = time.time()
            raw_response = _chat(agent, request)
            sql = extract_sql(raw_response)
            candidate = CandidateReport(
                attempt=attempt_no,
                action="pontis_sql" if attempt_no == 1 else "pontis_sql_revision",
                request=request,
                raw_response=raw_response,
                predicted_sql=sql,
                elapsed=time.time() - started,
                efficiency=get_agent_efficiency_metrics(agent),
            )
            attempts.append(candidate)

            if not sql:
                feedback = "SQL 输出严格拦截：候选结果没有可解析 SQL；请提交唯一的 ```sql``` fenced code block。"
                execution_preview = None
                predicted_execution = "PARSE_ERROR"
                continue

            guard_feedback = _guard_feedback(
                sql,
                case=case,
                seen_deterministic_warnings=seen_deterministic_warnings,
                force_feedback_seen=force_feedback_seen,
            )
            if guard_feedback:
                force_feedback_seen = True
                feedback = guard_feedback
                execution_preview = None
                predicted_execution = "PARSE_ERROR"
                continue

            candidate_execution = execute_sql(self.db_path, sql)
            if isinstance(candidate_execution, str):
                feedback = (
                    "SQL 执行失败，必须修改后重新提交一个唯一的 SQL fenced code block。\n\n"
                    f"执行错误：{candidate_execution}"
                )
                execution_preview = None
                predicted_execution = candidate_execution
                continue

            predicted_execution = candidate_execution
            execution_preview = format_execution_result(candidate_execution, limit=8)
            break

        if candidate is None:
            raise RuntimeError("No candidate generated")

        golden_execution = execute_sql(self.db_path, case.golden_sql) if case.golden_sql else None
        comparison = compare_execution_results(
            predicted_execution,
            golden_execution,
            question=case.question,
        )
        correct = comparison.strict_correct
        result = (
            "CORRECT" if correct
            else "PARSE_ERROR" if candidate.predicted_sql is None
            else "EXEC_ERROR" if isinstance(predicted_execution, str)
            else "WRONG"
        )
        return BirdRunResult(
            case=case,
            candidate=candidate,
            result=result,
            correct=correct,
            predicted_execution=predicted_execution,
            golden_execution=golden_execution,
            attempts=attempts,
            strict_correct=comparison.strict_correct,
            business_correct=comparison.business_correct,
            match_type=comparison.match_type,
        )


def _build_bird_agent_spec(db_id: str) -> AgentSpec:
    spec = AgentSpec(
        projects=[db_id],
        effort="mid",
        tools=["find", "grep", "read", "jd", "meta", "query"],
        prompts=[
            "base",
            "tool",
            "ontology",
            "sql",
            "bird",
            "effort",
            "guardrail",
            "project",
            "readme",
        ],
    )
    spec.guardrails = build_guardrails(
        spec,
        [
            "round_limit",
            "exploration_check",
            "sql_check",
            "final_sql_validity_check",
            "bridge_check",
            "disambig_check",
        ],
    )
    return spec


def _build_initial_request(case: BirdCase) -> str:
    evidence = case.evidence.strip() or "无额外提示。"
    return f"""\
数据库项目：{case.db_id}
问题：{case.question}
补充提示：{evidence}

请直接使用 Pontis 图谱和数据库查询工具探索必要事实，并输出最终 SQLite SQL。
最终回复只包含一个 ```sql``` 代码块。
"""


def _build_revision_request(
    case: BirdCase,
    *,
    sql: str | None,
    feedback: str,
    execution_preview: str | None,
) -> str:
    evidence = case.evidence.strip() or "无额外提示。"
    sql_block = sql or "无可解析 SQL"
    preview = f"\n执行结果预览：\n{execution_preview}\n" if execution_preview else ""
    return f"""\
数据库项目：{case.db_id}
问题：{case.question}
补充提示：{evidence}

当前 SQL：
```sql
{sql_block}
```
{preview}

修改要求：
{feedback.strip()}

请在同一问题约束下修正 SQL。最终回复只包含一个 ```sql``` 代码块。
"""


def _chat(agent, request: str) -> str:
    response = ""
    for event in agent.chat_stream(request):
        if event.get("type") == "done":
            response = event.get("content") or ""
            break
    return response.strip()


def _guard_feedback(
    sql: str,
    *,
    case: BirdCase,
    seen_deterministic_warnings: set[str],
    force_feedback_seen: bool,
) -> str | None:
    """Return blocking guard feedback.

    Deterministic strict findings request revision until cleared.
    Deterministic warnings are returned once per distinct message, then repeated
    occurrences do not request another revision.
    Force feedback is unconditional; it is not a SQL-content finding. The
    runner sends it at least once, then keeps attaching it only while strict or
    new warning feedback is already requesting another revision.
    """

    feedback_parts: list[str] = []

    deterministic_result = bird_sql_output_guard(sql, question=case.question, evidence=case.evidence)
    if deterministic_result.strict:
        feedback_parts.append(format_bird_sql_output_guard_strict_feedback(deterministic_result.strict))
    if deterministic_result.warnings:
        new_warnings = [
            warning
            for warning in deterministic_result.warnings
            if warning not in seen_deterministic_warnings
        ]
        seen_deterministic_warnings.update(new_warnings)
        if new_warnings:
            feedback_parts.append(format_bird_sql_output_guard_warning(new_warnings))
    if deterministic_result.force and (not force_feedback_seen or feedback_parts):
        feedback_parts.append(format_bird_sql_output_guard_force_feedback(deterministic_result.force))

    if not feedback_parts:
        return None
    return "\n\n".join(feedback_parts)
