"""Plan-mode multi-agent helpers.

This module owns generic plan/exit_plan follow-up logic used by external
drivers. It intentionally stays dataset-neutral: dataset policy can be passed
in by the caller through the ordinary agent prompt or business request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


CHALLENGER_PROMPT = """\
你是一个独立的 Text-to-SQL schema-linking challenger。你拥有和主 DBA agent
相同的数据库访问工具，负责在主 DBA agent 提交 SQL 写作计划后，基于同一业务
问题提出一条有竞争力的替代 schema-linking path。

你会收到上一位 DBA 通过 exit_plan 提交的 SQL 写作计划。请把这份 plan 视为
压缩后的探索上下文：先复述它已经确定的表、字段、连接路径、过滤条件和行粒度，
再沿着不同路径探索数据库并提交 alternative SQL plan。

工作要求：
- 重新从业务问题和补充提示中提取关键短语。
- 在整个数据库范围内寻找这些短语可能对应的表和列。
- 特别关注同名、近同名、合并字段与拆分字段、明细表与汇总表、角色字段与关系路径。
- alternative path 必须至少改变一个关键表、关键字段、连接路径、行粒度或过滤字段。
- 优先寻找“变动大”的 alternative path：换用不同表、不同来源层级、不同 JOIN 路径、不同关系端点，或把合并字段改成其他表中的拆分字段。
- 替代路径选择优先级：跨表、跨层级、跨路径替代方案优先；同一张表内的相近字段替代作为次级方案。
- 如果上一位 DBA 的路径证据很强，请提出最有竞争力的替代路径，并说明它和原路径的差异与风险。
- 报告中先写 alternative path，再写它与原路径的差异。
- 审查重点放在表、字段、连接路径、过滤字段和行粒度。
- 完成后调用 exit_plan，提交你的 schema-linking challenge report。
"""


JUDGE_PROMPT = """\
你是一个独立的 Text-to-SQL SQL plan judge。你拥有和候选生成 agent 相同的
数据库访问工具，负责比较多份候选 SQL plan，并选出最适合作为最终实现基础的
schema-linking path。

工作方式：
- 先从业务问题和补充信息中提取关键短语、实体、值、输出目标和聚合口径。
- 逐份比较候选报告中的表、字段、连接路径、过滤条件、行粒度和 SQL 输出。
- 必要时用数据库工具验证候选之间的关键差异，例如字段是否存在、字段含义、外键关系、行数覆盖和过滤结果。
- 裁决重点是 schema linking 与行粒度，不纠缠可机械修复的 SQL 小语法。
- 遇到数据库本身无法唯一决定、需要业务方或评测方偏好的分歧时，调用
  ask_question 询问外部监督者。
- 完成后调用 exit_plan，提交裁决报告。

裁决报告使用以下结构：
1. 候选对比表：逐个列出每份候选的表、字段、连接路径、过滤条件、行粒度、主要证据和风险。
2. 关键差异：说明哪些差异会改变结果。
3. 裁决：写明 selected_candidate_id。如果所有候选都不足以采用，写 selected_candidate_id=0。
4. 反馈：给出主 agent 后续应采用的路径和需要修改的点。
"""


class SchemaChallenger:
    """Runs one or more independent Pontis workers as schema-linking challengers."""

    def __init__(
        self,
        db_dir: Path,
        db_id: str,
        *,
        count: int = 1,
        main_agent_prompt: str | None = None,
    ):
        self.db_dir = Path(db_dir)
        self.db_id = db_id
        self.count = max(0, int(count))
        self.main_agent_prompt = main_agent_prompt

    def run(
        self,
        *,
        business_question: str,
        business_context: str,
        main_candidate: Any,
    ) -> list[Any]:
        from evaluation_agent.pontis_worker import PontisSqlWorker

        reports: list[Any] = []
        if self.count <= 0:
            return reports
        for index in range(self.count):
            worker = PontisSqlWorker(
                self.db_dir,
                self.db_id,
                main_agent_prompt=self._build_system_prompt(index),
                schema_challenge_count=0,
            )
            reports.append(
                worker.plan(
                    index + 1,
                    self._build_request(
                        business_question=business_question,
                        business_context=business_context,
                        main_candidate=main_candidate,
                        index=index,
                    ),
                )
            )
        return reports

    def _build_system_prompt(self, index: int) -> str:
        parts = [CHALLENGER_PROMPT]
        if self.main_agent_prompt and self.main_agent_prompt.strip():
            parts.append(self.main_agent_prompt.strip())
        if index > 0:
            parts.append(
                "你是另一位独立 challenger。请优先寻找新的字段来源、表层级或关系路径。"
            )
        return "\n\n".join(parts)

    @staticmethod
    def _build_request(
        *,
        business_question: str,
        business_context: str,
        main_candidate: Any,
        index: int,
    ) -> str:
        args = (main_candidate.exit_plan_request or {}).get("arguments") or {}
        plan = args.get("plan") or main_candidate.raw_response
        title = args.get("title") or ""
        reason = args.get("reason") or ""
        challenger_label = f"Challenger {index + 1}"
        extra_context = business_context.strip() or "无额外补充。"
        return f"""\
你是 {challenger_label}。请独立挑战上一位 DBA 的 schema linking。

数据库项目：当前项目
业务问题：{business_question}
业务补充信息：{extra_context}

上一位 DBA 的计划标题：
{title}

上一位 DBA 的理由：
{reason}

上一位 DBA 的 SQL 写作计划：
{plan}

请先用你自己的话重述上一位 DBA 的核心 plan，包括它选择的表、字段、连接路径、
过滤条件和行粒度；然后在这个压缩上下文基础上提出 alternative schema-linking
path。

请重点回答：
1. 题面关键短语分别可能对应哪些候选表/列？
2. 你的 alternative schema-linking path 是什么？
3. 这条 path 和上一位 DBA 的 path 至少在哪一个关键表、字段、连接路径、行粒度或过滤字段上不同？
4. 你检查到的跨表、跨层级、跨路径替代方案分别是什么？如果最终采用同表字段替代，请说明它相对其他候选更有竞争力的证据。
5. 给出 alternative SQL plan，并说明它的风险。

请完成探索后调用 exit_plan。
"""


class SQLPlanJudge:
    """Runs an independent Pontis worker to judge multiple SQL plan candidates."""

    def __init__(
        self,
        db_dir: Path,
        db_id: str,
        *,
        main_agent_prompt: str | None = None,
        ask_question_callback: Callable[[str, str], str] | None = None,
    ):
        self.db_dir = Path(db_dir)
        self.db_id = db_id
        self.main_agent_prompt = main_agent_prompt
        self.ask_question_callback = ask_question_callback

    def run(
        self,
        *,
        business_question: str,
        business_context: str,
        main_candidate: Any,
        challenge_reports: list[Any],
    ) -> Any:
        from evaluation_agent.pontis_worker import PontisSqlWorker

        worker = PontisSqlWorker(
            self.db_dir,
            self.db_id,
            main_agent_prompt=self._build_system_prompt(),
            schema_challenge_count=0,
        )
        self._register_ask_question_tool(worker, business_question, business_context)
        return worker.plan(
            1,
            self._build_request(
                business_question=business_question,
                business_context=business_context,
                main_candidate=main_candidate,
                challenge_reports=challenge_reports,
            ),
        )

    def _register_ask_question_tool(
        self,
        worker: Any,
        business_question: str,
        business_context: str,
    ) -> None:
        schema = {
            "type": "function",
            "function": {
                "name": "ask_question",
                "description": (
                    "Ask the external supervisor for business or evaluation "
                    "preference when database evidence cannot uniquely decide "
                    "between candidate SQL plans."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Specific question for the supervisor.",
                        },
                        "context": {
                            "type": "string",
                            "description": "Brief candidate comparison and why guidance is needed.",
                        },
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            },
        }

        def _exec_ask_question(_workspace, arguments: dict) -> str:
            question = str(arguments.get("question") or "").strip()
            context = str(arguments.get("context") or "").strip()
            if not question:
                return "ask_question error: missing question."
            if not self.ask_question_callback:
                return "ask_question unavailable: no external supervisor callback was configured."
            full_context = (
                f"业务问题：{business_question}\n"
                f"业务补充信息：{business_context.strip() or '无额外补充。'}\n\n"
                f"裁决上下文：{context}"
            )
            return self.ask_question_callback(question, full_context)

        worker.agent.tools.register("ask_question", schema, _exec_ask_question)

    def _build_system_prompt(self) -> str:
        parts = [JUDGE_PROMPT]
        if self.main_agent_prompt and self.main_agent_prompt.strip():
            parts.append(self.main_agent_prompt.strip())
        return "\n\n".join(parts)

    @staticmethod
    def _build_request(
        *,
        business_question: str,
        business_context: str,
        main_candidate: Any,
        challenge_reports: list[Any],
    ) -> str:
        extra_context = business_context.strip() or "无额外补充。"
        return f"""\
请裁决以下多份 SQL plan 候选。

业务问题：
{business_question}

业务补充信息：
{extra_context}

{format_candidate_reports(main_candidate, challenge_reports)}

请使用数据库工具核查会影响裁决的关键差异。完成后调用 exit_plan 提交裁决报告。
"""


def format_candidate_reports(main_candidate: Any, reports: list[Any]) -> str:
    args = (main_candidate.exit_plan_request or {}).get("arguments") or {}
    sections = [
        "# Candidate 1\n\n"
        f"标题：{args.get('title') or ''}\n"
        f"原因：{args.get('reason') or ''}\n"
        f"计划：\n{args.get('plan') or main_candidate.raw_response or ''}"
    ]
    for idx, report in enumerate(reports, start=1):
        candidate_id = idx + 1
        sections.append(
            f"# Candidate {candidate_id}\n\n"
            f"{report.raw_response or '(empty report)'}"
        )
    return "\n\n".join(sections)


def format_challenge_reports(reports: list[Any]) -> str:
    if not reports:
        return "未运行 schema challenger。"
    sections = []
    for idx, report in enumerate(reports, start=1):
        candidate_id = idx + 1
        sections.append(
            f"# Candidate {candidate_id}\n\n"
            f"action: {report.action}\n"
            f"exit_plan_requested: {report.exit_plan_requested}\n\n"
            f"{report.raw_response or '(empty report)'}"
        )
    return "\n\n".join(sections)


def format_judge_report(report: Any | None) -> str:
    if report is None:
        return "未运行 SQL plan judge。"
    return report.raw_response or "(empty judge report)"
