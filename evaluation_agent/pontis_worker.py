"""外部 evaluation agent 调用 Pontis 的客户端适配层。

该适配层保持数据集无关，只负责把 evaluation agent 的普通用户请求转发给
Pontis 主 agent，并提取 Pontis 返回的候选 SQL。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from agent.config import AgentSpec, create_agent, default_spec
from scripts.BIRD.benchmark_runtime import TraceCollector, extract_sql, get_agent_efficiency_metrics

from .models import CandidateReport


def build_worker_spec(db_id: str) -> AgentSpec:
    spec = default_spec("")
    spec.projects = [db_id]
    return spec


def _append_system_prompt(agent, prompt: str | None) -> None:
    if not prompt or not prompt.strip():
        return
    current = agent.system_prompt
    if isinstance(current, list):
        parts = list(current)
    else:
        parts = [str(current)]
    parts.append(prompt.strip())
    agent.set_system_prompt(parts)


class PontisSqlWorker:
    """Thin adapter around the generic Pontis agent."""

    def __init__(
        self,
        db_dir: Path,
        db_id: str,
        trace_callback=None,
        main_agent_prompt: str | None = None,
        schema_challenge_count: int = 0,
        judge_question_callback: Callable[[str, str], str] | None = None,
    ):
        self.db_dir = Path(db_dir)
        self.db_id = db_id
        self.collector = TraceCollector()
        self.spec = build_worker_spec(db_id)
        callback = trace_callback or self.collector.callback
        self.agent = create_agent(str(self.db_dir), self.spec, trace_callback=callback)
        _append_system_prompt(self.agent, main_agent_prompt)
        self._last_exit_plan_request: dict[str, Any] | None = None
        self.main_agent_prompt = main_agent_prompt
        self.schema_challenge_count = max(0, int(schema_challenge_count))
        self.judge_question_callback = judge_question_callback
        self._current_business_request = ""
        self._schema_challenge_used = False
        self._schema_path_selected = False

    def send_message(self, attempt: int, request: str) -> CandidateReport:
        return self._chat_events(attempt, "send_message", request, self.agent.chat_stream(request))

    def plan(self, attempt: int, request: str) -> CandidateReport:
        if self._current_business_request:
            report = self._chat_events(attempt, "plan", request, self.agent.chat_stream(request))
            return self._attach_schema_challenges(report, self._current_business_request, request)
        self._current_business_request = request
        report = self._chat_events(attempt, "plan", request, self.agent.chat_stream(request))
        return self._attach_schema_challenges(report, self._current_business_request, "")

    def approve(self, attempt: int, comment: str = "") -> CandidateReport:
        self._restore_last_exit_plan_if_needed()
        if not self.agent.has_pending_approval():
            request = "批准。请继续执行。"
            if comment.strip():
                request += f"\n\n补充要求：{comment.strip()}"
            return self._chat_events(attempt, "approve", request, self.agent.chat_stream(request))
        report = self._chat_events(
            attempt,
            "approve",
            comment,
            self.agent.resolve_approval_stream(True, comment),
        )
        self._last_exit_plan_request = None
        return report

    def reject(self, attempt: int, reason: str) -> CandidateReport:
        self._restore_last_exit_plan_if_needed()
        if not self.agent.has_pending_approval():
            request = (
                "驳回。请根据下面原因修正你的计划或 SQL，然后重新给出结果。\n\n"
                f"驳回原因：{reason.strip()}"
            )
            report = self._chat_events(attempt, "reject", request, self.agent.chat_stream(request))
            return self._attach_schema_challenges(report, self._current_business_request or request, request)
        report = self._chat_events(
            attempt,
            "reject",
            reason,
            self.agent.resolve_approval_stream(False, reason),
        )
        self._last_exit_plan_request = None
        return self._attach_schema_challenges(report, self._current_business_request or reason, reason)

    def _attach_schema_challenges(
        self,
        report: CandidateReport,
        business_question: str,
        business_context: str,
    ) -> CandidateReport:
        if (
            not report.exit_plan_requested
            or self.schema_challenge_count <= 0
            or self._schema_challenge_used
            or self._schema_path_selected
        ):
            return report
        self._schema_challenge_used = True
        from agent.guardrail.sql_plan_multi_agent import SchemaChallenger, SQLPlanJudge

        challenger = SchemaChallenger(
            self.db_dir,
            self.db_id,
            count=self.schema_challenge_count,
            main_agent_prompt=self.main_agent_prompt,
        )
        report.challenge_reports = challenger.run(
            business_question=business_question,
            business_context=business_context,
            main_candidate=report,
        )
        if report.challenge_reports:
            judge = SQLPlanJudge(
                self.db_dir,
                self.db_id,
                main_agent_prompt=self.main_agent_prompt,
                ask_question_callback=self.judge_question_callback,
            )
            report.judge_report = judge.run(
                business_question=business_question,
                business_context=business_context,
                main_candidate=report,
                challenge_reports=report.challenge_reports,
            )
        return report

    def mark_schema_path_selected(self) -> None:
        self._schema_path_selected = True

    def _chat_events(self, attempt: int, action: str, request: str, events) -> CandidateReport:
        started = time.time()
        response = ""
        exit_plan_request = None
        for event in events:
            if event.get("type") == "done":
                response = event.get("content") or ""
            elif event.get("type") == "exit_plan_request":
                exit_plan_request = {
                    "name": event.get("name"),
                    "arguments": event.get("arguments") or {},
                    "id": event.get("id"),
                }
                self._last_exit_plan_request = exit_plan_request
                response = _format_exit_plan_request(exit_plan_request)
                break
        elapsed = time.time() - started
        predicted_sql = extract_sql(response)
        efficiency: dict[str, Any] = get_agent_efficiency_metrics(self.agent)
        return CandidateReport(
            attempt=attempt,
            action=action,
            request=request,
            raw_response=response or "",
            predicted_sql=predicted_sql,
            elapsed=elapsed,
            efficiency=efficiency,
            exit_plan_requested=exit_plan_request is not None,
            exit_plan_request=exit_plan_request,
        )

    def _restore_last_exit_plan_if_needed(self) -> None:
        if self.agent.has_pending_approval() or not self._last_exit_plan_request:
            return
        tool_call_id = self._last_exit_plan_request.get("id")
        if not tool_call_id:
            return
        self.agent._pending_approval = {
            "tool_call_id": tool_call_id,
            "name": self._last_exit_plan_request.get("name") or "exit_plan",
            "arguments": self._last_exit_plan_request.get("arguments") or {},
        }


def _format_exit_plan_request(event: dict[str, Any]) -> str:
    args = event.get("arguments") or {}
    title = args.get("title") or "Approval request"
    plan = args.get("plan") or ""
    reason = args.get("reason") or ""
    parts = [f"Exit plan requested: {title}"]
    if reason:
        parts.append(f"Reason: {reason}")
    if plan:
        parts.append(f"Plan:\n{plan}")
    return "\n\n".join(parts)
