"""BIRD evaluation agent that drives conversations with Pontis."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from openai import OpenAI

from agent.utils import load_agent_config
from scripts.BIRD.benchmark_runtime import execute_sql, find_db_file, is_correct
from utils.context_dump import dump_llm_context, reset_context_dump_meta, set_context_dump_meta

from .bird_prompts import BIRD_GUIDANCE, build_business_initial_request
from .models import BirdCase, CandidateReport, EvaluationResult
from .pontis_worker import PontisSqlWorker
from agent.guardrail.sql_plan_multi_agent import format_challenge_reports, format_judge_report


EVALUATION_AGENT_SYSTEM_PROMPT = """\
你是 BIRD evaluation agent。你代表业务员和 DBA agent 对话。

你只能通过工具和 DBA agent 交互：
- `plan`：一次性入口。仅在收到用户业务问题后的第一轮调用，用来让 DBA agent 探索数据库并提交 SQL plan。
- `select_plan`：在多份候选 SQL plan 中选择一份候选报告，并决定完全接受、部分接受修改，或全部重写。
- `reject`：最终 SQL 仍需修改时，说明具体修改原因。

你的职责是提出业务请求、审查 DBA agent 的 SQL plan 和最终 SQL 是否符合 BIRD 要求。

收到用户给出的数据库项目、业务问题和补充提示后，先调用一次 `plan` 工具向 DBA agent 提出数据分析请求。进入计划流程后，后续不再调用 `plan`。系统返回多份候选 SQL plan 后，请调用 `select_plan` 选定一条 schema-linking 路径。所有候选报告优先级相同，请只根据问题、证据、schema linking、过滤条件、行粒度和 SQL 输出要求裁决。路径选定后，后续修改都基于已选路径推进。最终 SQL 输出后，如满足要求可以直接回复 DONE；如需修改请调用 `reject`。

""" + BIRD_GUIDANCE


EVALUATION_AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "plan",
            "description": "一次性入口：向 DBA agent 发送业务问题，让它探索数据库并提交 SQL plan。",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "发给 DBA agent 的自然语言消息。",
                    }
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_plan",
            "description": "裁决多份候选 SQL plan，并把裁决结果交给 DBA agent。",
            "parameters": {
                "type": "object",
                "properties": {
                    "decision": {
                        "type": "string",
                        "enum": [
                            "accept_candidate",
                            "accept_candidate_with_revision",
                            "reject_all_with_revision",
                        ],
                        "description": "accept_candidate=完全接受某份候选；accept_candidate_with_revision=部分接受某份候选并给出修改意见；reject_all_with_revision=所有候选都不接受并给出自己的意见。",
                    },
                    "candidate_id": {
                        "type": "integer",
                        "description": "候选编号。1 表示 Candidate 1；2 表示 Candidate 2；3 表示 Candidate 3，依此类推。reject_all_with_revision 时填 0。",
                    },
                    "feedback": {
                        "type": "string",
                        "description": "裁决说明。完全接受时可简短说明；部分接受或全部不接受时给出具体修改意见。",
                    },
                },
                "required": ["decision", "candidate_id", "feedback"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reject",
            "description": "驳回 DBA agent 的 plan 或 SQL，并要求其修改。",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "具体、可执行的驳回原因。",
                    }
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
]


class EvaluationToolCall:
    def __init__(self, name: str | None, arguments: dict[str, Any], tool_call_id: str | None, content: str):
        self.name = name
        self.arguments = arguments
        self.tool_call_id = tool_call_id
        self.content = content


class BirdEvaluationLLM:
    """LLM evaluation agent that plays the BIRD business/evaluator role."""

    def __init__(self, project_path: Path):
        self.config = load_agent_config(str(project_path))
        if not self.config["api_key"]:
            raise RuntimeError("No API key configured for BIRD evaluation agent.")
        self.client = OpenAI(
            api_key=self.config["api_key"],
            base_url=self.config["provider"],
            timeout=120.0,
        )
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": EVALUATION_AGENT_SYSTEM_PROMPT}
        ]

    def start(self, request: str) -> None:
        self.messages.append({"role": "user", "content": request})

    def next_action(self) -> EvaluationToolCall:
        kwargs: dict[str, Any] = {
            "model": self.config["model"],
            "messages": self.messages,
            "tools": EVALUATION_AGENT_TOOLS,
        }
        if self.config.get("thinking", False):
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            kwargs["reasoning_effort"] = self.config.get("thinking_effort", "high")
        else:
            kwargs["temperature"] = self.config.get("temperature", 0.3)
        dump_llm_context("evaluation_agent", kwargs)
        response = self.client.chat.completions.create(**kwargs)
        msg = response.choices[0].message
        self.messages.append(msg.to_dict())
        tool_calls = msg.tool_calls or []
        if not tool_calls:
            return EvaluationToolCall(None, {}, None, msg.content or "")
        for skipped in tool_calls[1:]:
            self.messages.append({
                "role": "tool",
                "tool_call_id": skipped.id,
                "content": "本轮只执行第一个 evaluation agent 工具调用；此工具调用已跳过，请等待下一轮。",
            })
        call = tool_calls[0]
        try:
            arguments = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            arguments = {}
        return EvaluationToolCall(call.function.name, arguments, call.id, msg.content or "")

    def add_tool_result(self, tool_call_id: str | None, content: str) -> None:
        if tool_call_id:
            self.messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            })
        else:
            self.messages.append({"role": "user", "content": content})

    def answer_judge_question(self, question: str, context: str) -> str:
        kwargs: dict[str, Any] = {
            "model": self.config["model"],
            "messages": [
                {"role": "system", "content": EVALUATION_AGENT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "SQL plan judge 正在裁决多个候选方案，需要 BIRD evaluation agent "
                        "给出业务/评测偏好。请只回答这个问题，不调用工具。\n\n"
                        f"裁决问题：{question}\n\n"
                        f"上下文：\n{context}"
                    ),
                },
            ],
        }
        if self.config.get("thinking", False):
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            kwargs["reasoning_effort"] = self.config.get("thinking_effort", "high")
        else:
            kwargs["temperature"] = self.config.get("temperature", 0.3)
        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""


class BirdEvaluationAgent:
    """Dataset-level agent that asks Pontis questions and reviews the answers."""

    def __init__(self, db_dir: Path, db_id: str, main_agent_prompt: str | None = None):
        self.db_dir = Path(db_dir)
        self.db_id = db_id
        self.db_path = find_db_file(self.db_dir)
        if not self.db_path:
            raise FileNotFoundError(f"No SQLite database found under {self.db_dir}")
        challenge_count = int(os.environ.get("PONTIS_SCHEMA_CHALLENGE_COUNT", "1") or "0")
        self.pontis = PontisSqlWorker(
            self.db_dir,
            db_id,
            main_agent_prompt=main_agent_prompt,
            schema_challenge_count=challenge_count,
            judge_question_callback=self._answer_judge_question,
        )
        self.evaluation_agent = BirdEvaluationLLM(self.db_dir)

    def _answer_judge_question(self, question: str, context: str) -> str:
        return self.evaluation_agent.answer_judge_question(question, context)

    def run_case(self, case: BirdCase, *, max_attempts: int = 2) -> EvaluationResult:
        """Run one BIRD case by conversing with the generic Pontis agent."""
        token = set_context_dump_meta(
            run_id=os.environ.get("PONTIS_CONTEXT_RUN_ID"),
            db_id=case.db_id,
            question_id=case.question_id,
        )
        try:
            return self._run_case(case, max_attempts=max_attempts)
        finally:
            reset_context_dump_meta(token)

    def _run_case(self, case: BirdCase, *, max_attempts: int = 2) -> EvaluationResult:
        """Run one BIRD case by conversing with the generic Pontis agent."""
        attempts: list[CandidateReport] = []
        candidate: CandidateReport | None = None
        self.evaluation_agent.start(build_business_initial_request(case))
        predicted_execution: set | str = "PARSE_ERROR"
        turn = 1
        sql_attempts = 0
        plan_approved = False

        for _ in range(max(6, max_attempts * 4)):
            action = self.evaluation_agent.next_action()
            if action.name == "plan":
                if candidate is not None:
                    self.evaluation_agent.add_tool_result(
                        action.tool_call_id,
                        "本题已经进入计划流程。请使用 `select_plan` 裁决当前 SQL plan，或使用 `reject` 修改最终 SQL。",
                    )
                    continue
                message = str(action.arguments.get("message") or "").strip()
                if not message:
                    self.evaluation_agent.add_tool_result(action.tool_call_id, "plan 工具缺少 message，请重新调用工具。")
                    continue
                candidate = self.pontis.plan(turn, message)
            elif action.name == "select_plan":
                if not candidate or not candidate.exit_plan_requested:
                    self.evaluation_agent.add_tool_result(
                        action.tool_call_id,
                        "当前没有待裁决的 SQL plan。请先调用 `plan`。",
                    )
                    continue
                decision_result = self._apply_plan_decision(turn, candidate, action.arguments)
                if isinstance(decision_result, str):
                    self.evaluation_agent.add_tool_result(action.tool_call_id, decision_result)
                    continue
                candidate, plan_approved = decision_result
            elif action.name == "reject":
                reason = str(action.arguments.get("reason") or "").strip()
                if not reason:
                    self.evaluation_agent.add_tool_result(action.tool_call_id, "reject 工具缺少 reason，请重新调用工具。")
                    continue
                candidate = self.pontis.reject(turn, reason)
            elif candidate and candidate.predicted_sql and not isinstance(predicted_execution, str):
                break
            else:
                self.evaluation_agent.add_tool_result(
                    action.tool_call_id,
                    "请调用 `plan`、`select_plan` 或 `reject` 继续评测流程。",
                )
                continue

            attempts.append(candidate)
            turn += 1

            if candidate.exit_plan_requested and not plan_approved:
                self.evaluation_agent.add_tool_result(
                    action.tool_call_id,
                    self._format_candidate_for_evaluation_agent(case, candidate, predicted_execution, plan_approved),
                )
                continue

            if candidate.predicted_sql:
                predicted_execution = execute_sql(self.db_path, candidate.predicted_sql)
                sql_attempts += 1
                self.evaluation_agent.add_tool_result(
                    action.tool_call_id,
                    self._format_candidate_for_evaluation_agent(case, candidate, predicted_execution, plan_approved),
                )
                if not isinstance(predicted_execution, str):
                    break
                if isinstance(predicted_execution, str) and sql_attempts >= max_attempts:
                    break
                continue

            self.evaluation_agent.add_tool_result(
                action.tool_call_id,
                self._format_candidate_for_evaluation_agent(case, candidate, predicted_execution, plan_approved),
            )

        if candidate is None:
            raise RuntimeError("No candidate generated")

        golden_execution = execute_sql(self.db_path, case.golden_sql) if case.golden_sql else None
        correct = bool(golden_execution is not None and is_correct(predicted_execution, golden_execution))
        result = (
            "CORRECT" if correct
            else "PARSE_ERROR" if candidate.predicted_sql is None
            else "EXEC_ERROR" if isinstance(predicted_execution, str)
            else "WRONG"
        )
        return EvaluationResult(
            case=case,
            candidate=candidate,
            result=result,
            correct=correct,
            predicted_execution=predicted_execution,
            golden_execution=golden_execution,
            attempts=attempts,
        )

    def _format_candidate_for_evaluation_agent(
        self,
        case: BirdCase,
        candidate: CandidateReport,
        predicted_execution: set | str,
        plan_approved: bool,
    ) -> str:
        if candidate.exit_plan_requested:
            args = (candidate.exit_plan_request or {}).get("arguments") or {}
            if not candidate.challenge_reports:
                return (
                    "DBA agent 基于已选 schema-linking 路径提交了修订 SQL plan。\n"
                    "请审查这版 plan 是否落实了已选路径和修改意见，并逐项审查其中"
                    "“拟提交最终 SQL”的输出列、过滤条件、聚合、排序和 LIMIT 是否符合要求。"
                    "如果 plan 中没有完整 SQL 代码块，请要求 DBA agent 补充后重提。\n\n"
                    "如满意，请调用 `select_plan`：decision=accept_candidate，candidate_id=1。\n"
                    "如仍需修改，请调用 `select_plan`：decision=accept_candidate_with_revision，candidate_id=1，"
                    "并在 feedback 中写清楚修改意见。\n\n"
                    "# Candidate 1: Revised DBA plan\n\n"
                    f"标题：{args.get('title') or ''}\n"
                    f"原因：{args.get('reason') or ''}\n"
                    f"计划：\n{args.get('plan') or ''}"
                )
            return (
                "系统生成了多份候选 SQL plan。每份候选报告优先级相同。\n"
                "请调用 `select_plan` 完成裁决。裁决时必须审查每份候选中"
                "“拟提交最终 SQL”的输出列、过滤条件、聚合、排序和 LIMIT；"
                "没有完整 SQL 代码块的候选不能直接接受。\n\n"
                "可选裁决：\n"
                "- 完全接受某份候选：decision=accept_candidate，candidate_id 填对应编号。\n"
                "- 部分接受某份候选并给出修改意见：decision=accept_candidate_with_revision，candidate_id 填对应编号。\n"
                "- 当前候选均需重写：decision=reject_all_with_revision，candidate_id=0。\n\n"
                "# Candidate 1\n\n"
                f"标题：{args.get('title') or ''}\n"
                f"原因：{args.get('reason') or ''}\n"
                f"计划：\n{args.get('plan') or ''}\n\n"
                f"{format_challenge_reports(candidate.challenge_reports)}\n\n"
                "# Independent SQL Plan Judge Report\n\n"
                f"{format_judge_report(candidate.judge_report)}"
            )
        if candidate.predicted_sql:
            status = "已批准计划" if plan_approved else "尚未批准计划"
            if isinstance(predicted_execution, str):
                execution = f"SQL 执行失败：{predicted_execution}"
            else:
                execution = "SQL 已成功执行。"
            return (
                f"DBA agent 输出了最终 SQL（{status}）。请判断是否需要 `reject` 修改；"
                "如果满意，可以直接回复 DONE。\n\n"
                f"SQL:\n{candidate.predicted_sql}\n\n"
                f"{execution}"
            )
        return (
            "DBA agent 没有提交 exit_plan，也没有输出 SQL。"
            "请调用 `reject` 要求它先探索数据库并提交 SQL plan。"
        )

    def _apply_plan_decision(
        self,
        turn: int,
        candidate: CandidateReport,
        arguments: dict[str, Any],
    ) -> tuple[CandidateReport, bool] | str:
        decision = str(arguments.get("decision") or "").strip()
        try:
            candidate_id = int(arguments.get("candidate_id"))
        except (TypeError, ValueError):
            return "select_plan 工具的 candidate_id 必须是整数。"
        feedback = str(arguments.get("feedback") or "").strip()
        max_candidate_id = 1 + len(candidate.challenge_reports)

        if decision == "accept_candidate":
            if candidate_id == 1:
                self.pontis.mark_schema_path_selected()
                return self.pontis.approve(turn, feedback), True
            if 2 <= candidate_id <= max_candidate_id:
                self.pontis.mark_schema_path_selected()
                selected = self._candidate_report_text(candidate, candidate_id)
                reason = (
                    f"裁决结果：完全接受候选 {candidate_id}。\n\n"
                    "请采用该候选报告中的 schema-linking path，重新提交 SQL plan。\n\n"
                    f"{selected}\n\n"
                    f"裁决说明：{feedback}"
                )
                return self.pontis.reject(turn, reason), False
            return f"candidate_id 超出范围。当前可选候选为 1 到 {max_candidate_id}。"

        if decision == "accept_candidate_with_revision":
            if 1 <= candidate_id <= max_candidate_id:
                self.pontis.mark_schema_path_selected()
                selected = self._candidate_report_text(candidate, candidate_id)
                reason = (
                    f"裁决结果：部分接受候选 {candidate_id}，并按反馈修改。\n\n"
                    f"{selected}\n\n"
                    f"修改意见：{feedback}"
                )
                return self.pontis.reject(turn, reason), False
            return f"candidate_id 超出范围。当前可选候选为 1 到 {max_candidate_id}。"

        if decision == "reject_all_with_revision":
            self.pontis.mark_schema_path_selected()
            reason = (
                "裁决结果：当前候选报告均需重写。\n\n"
                f"修改意见：{feedback}"
            )
            return self.pontis.reject(turn, reason), False

        return "select_plan 工具的 decision 必须是 accept_candidate、accept_candidate_with_revision 或 reject_all_with_revision。"

    @staticmethod
    def _candidate_report_text(candidate: CandidateReport, candidate_id: int) -> str:
        if candidate_id == 1:
            args = (candidate.exit_plan_request or {}).get("arguments") or {}
            return (
                "# Candidate 1: Main DBA plan\n"
                f"标题：{args.get('title') or ''}\n"
                f"原因：{args.get('reason') or ''}\n"
                f"计划：\n{args.get('plan') or candidate.raw_response or ''}"
            )
        challenge_index = candidate_id - 2
        if 0 <= challenge_index < len(candidate.challenge_reports):
            report = candidate.challenge_reports[challenge_index]
            return (
                f"# Candidate {candidate_id}: Schema challenger {challenge_index + 1}\n"
                f"{report.raw_response or ''}"
            )
        return f"# Candidate {candidate_id}\n"
