"""Schema-linking challenge controller for final SQL judging."""
from __future__ import annotations

import re
from typing import Optional

from agent.guardrail_api import CallVerdict, Guardrail, GuardrailContext
from agent.guardrail.sql_utils import get_sql_from_messages


class BirdSchemaChallengeController(Guardrail):
    """Turn a final SQL into schema-linking reports, then judge them.

    This guardrail is an orchestration layer. It should keep prompts short and
    generic; task-specific rules belong in README retrieval, metadata,
    disambiguation entities, or other dedicated checks.
    """

    def __init__(self, report_count: int = 3) -> None:
        self.report_count = max(1, int(report_count or 3))
        self._phase = "await_initial_sql"
        self._reports: list[str] = []
        self._task: dict[str, str] = {}
        self._last_sql_key: Optional[str] = None
        self._judge_decision = ""
        self._grounded_context = ""
        self._grounded_tool_history: list[tuple] = []

    def check(self, ctx: GuardrailContext) -> dict:
        if ctx.pending_calls:
            if self._phase in {"await_judge", "await_judge_audit"}:
                prompt = self._build_no_more_tools_prompt()
                return {i: CallVerdict("block", prompt) for i in range(len(ctx.pending_calls))}
            return {}
        if ctx.agent is None:
            return {}

        setattr(ctx.agent, "_bird_schema_challenge_enabled", True)
        release_prompt_pending = bool(
            getattr(ctx.agent, "_bird_schema_challenge_release_prompt_pending", False)
        )
        setattr(
            ctx.agent,
            "_bird_schema_challenge_allow_final_recheck",
            self._phase == "released" and not release_prompt_pending,
        )
        self._sync_agent_state(ctx.agent)

        response = (ctx.last_response or "").strip()
        if not response:
            return {}
        sql = self._extract_sql_from_text(response) or get_sql_from_messages(ctx.messages)
        if not sql:
            return {}

        if self._phase == "released":
            setattr(ctx.agent, "_bird_schema_challenge_release_prompt_pending", False)
            setattr(ctx.agent, "_bird_schema_challenge_allow_final_recheck", True)
            setattr(ctx.agent, "_bird_schema_challenge_release_sql", sql)
            return {}

        if self._phase == "await_initial_sql":
            self._task = self._extract_task(ctx.messages)
            self._last_sql_key = self._normalize_sql(sql)
            self._grounded_tool_history = list(ctx.tool_history)
            self._phase = "await_report"
            return {"text": CallVerdict("block", self._build_first_report_prompt(ctx, sql))}

        if self._phase in {"await_report", "await_challenge_report"}:
            self._append_report(response)
            if len(self._reports) < self.report_count:
                self._phase = "await_challenge_report"
                prompt = self._build_challenge_prompt(len(self._reports) + 1)
            else:
                self._phase = "await_judge"
                prompt = self._build_judge_prompt()
            return {
                "text": CallVerdict(
                    "block",
                    prompt,
                    replace_messages=self._rewritten_messages(ctx, prompt),
                    replace_tool_history=[],
                )
            }

        if self._phase == "await_judge":
            self._judge_decision = response
            self._phase = "await_judge_audit"
            self._last_sql_key = self._normalize_sql(sql)
            prompt = self._build_judge_audit_prompt(response, sql)
            self._sync_agent_state(ctx.agent)
            return {
                "text": CallVerdict(
                    "block",
                    prompt,
                    replace_messages=self._rewritten_messages(ctx, prompt),
                    replace_tool_history=[],
                )
            }

        if self._phase == "await_judge_audit":
            self._phase = "released"
            self._last_sql_key = self._normalize_sql(sql)
            setattr(ctx.agent, "_bird_schema_challenge_release_prompt_pending", True)
            setattr(ctx.agent, "_bird_schema_challenge_allow_final_recheck", False)
            setattr(ctx.agent, "_bird_schema_challenge_release_sql", sql)
            self._sync_agent_state(ctx.agent)
            return {
                "text": CallVerdict(
                    "block",
                    self._build_release_for_final_review_prompt(sql),
                    replace_tool_history=list(self._grounded_tool_history),
                )
            }

        return {}

    @staticmethod
    def _build_no_more_tools_prompt() -> str:
        return """\
Do not call tools in the judge or audit phase.

Use the reports, prior observations, question, evidence, and candidate SQL
already present in this context. Output the required decision now, including the
final SQLite SELECT in a ```sql``` block.
"""

    def _append_report(self, response: str) -> None:
        report_id = f"R{len(self._reports) + 1}"
        self._reports.append(f"[{report_id}]\n{response.strip()}")

    def _rewritten_messages(self, ctx: GuardrailContext, prompt: str) -> list[dict]:
        system_messages = getattr(ctx.agent, "_system_messages", None)
        if not system_messages:
            system_messages = [m for m in ctx.messages if m.get("role") == "system"]
        return list(system_messages) + [{"role": "user", "content": prompt}]

    def _sync_agent_state(self, agent) -> None:
        setattr(agent, "_bird_schema_challenge_grounded_context", self._grounded_context)
        setattr(agent, "_bird_schema_challenge_reports", self._format_reports())
        setattr(agent, "_bird_schema_challenge_judge_decision", self._judge_decision)

    def _build_first_report_prompt(self, ctx: GuardrailContext, sql: str) -> str:
        task = self._format_task()
        evidence = self._recent_grounded_context(ctx.tool_history)
        self._grounded_context = evidence
        return f"""\
You have proposed a final SQL. Pause before release and write a concise SQL
report.

Focus on schema linking and grounded observations:
- target tables and columns
- join path
- entity/value grounding
- row or aggregation grain
- output columns
- rejected alternative schema-linking paths
- any mismatch between tool observations and the candidate SQL

Do not perform README/style review here. That is handled by a separate release
reviewer.

Required format:

[SQL Report]
[Task]
Question ID:
Question:
Evidence:

[Schema Linking]
- target tables:
- target columns:
- join path:
- entity/value grounding:
- row or aggregation grain:
- output columns:

[Grounded Observations]
- supporting observations:
- contradicted or zero-hit attempts:
- rejected alternatives:
- remaining uncertainty:

[Candidate SQL]
```sql
-- candidate SQLite SELECT
```

[Known Risks]
- ...

Original task:
{task}

Candidate SQL:
```sql
{sql.strip()}
```

Recent grounded tool observations:
{evidence}
"""

    def _build_challenge_prompt(self, report_no: int) -> str:
        return f"""\
You are a new Text-to-SQL schema-linking challenger.

Review the original task and prior SQL reports. Look for a different plausible
schema-linking path or value grounding. Use tools only when they can resolve a
material schema-linking uncertainty. Do not perform README/style review.

Compare candidates by:
- table and column semantics
- join path
- entity/value grounding
- row or aggregation grain
- requested output columns
- consistency with observed tool results

If you agree with the prior report, say why and keep the same SQL. If you find a
better path, provide a new report and candidate SQL.

Use the same report format as before and include one candidate SQLite SELECT.

Original task:
{self._format_task()}

Prior SQL reports:
{self._format_reports()}
"""

    def _build_judge_prompt(self) -> str:
        return f"""\
You are a Text-to-SQL judge.

Choose the best candidate SQL from the reports. Prioritize schema-linking
quality and consistency with grounded tool observations: target tables, target
columns, join path, entity/value grounding, row or aggregation grain, and output
columns. Only compare SQL style details when schema linking is otherwise
equivalent.

You may use tools only to resolve a material disagreement between reports.

Final output must contain:
- selected_report_id
- selection_reason
- one ```sql``` block containing the selected SQLite SELECT

Original task:
{self._format_task()}

SQL reports:
{self._format_reports()}
"""

    def _build_judge_audit_prompt(self, judge_decision: str, sql: str) -> str:
        return f"""\
You are auditing the previous Text-to-SQL judge decision.

Do not solve the task from scratch. Check whether the selected SQL is supported
by the reports and grounded observations. If the judge selected an unsupported
schema-linking path while a report provides a better grounded path, correct it.
Otherwise keep the judge SQL.

Use tools only for material disagreements. Do not perform README/style review.

Final output must contain:
- audit_result: `keep_judge_sql` or `corrected`
- selected_report_id: original report id, `JUDGE_SYNTHESIZED`, or `AUDIT_SYNTHESIZED`
- audit_reason
- one ```sql``` block containing the final SQLite SELECT

Original task:
{self._format_task()}

SQL reports:
{self._format_reports()}

Previous judge decision:
{judge_decision.strip()}

Previous judge SQL:
```sql
{sql.strip()}
```
"""

    @staticmethod
    def _build_release_for_final_review_prompt(sql: str) -> str:
        return f"""\
Schema-linking challenge and judge have selected the candidate SQL below.

Now output only this selected SQL as the final answer so the final release
reviewer can inspect it. Do not add explanation, do not run tools, and do not
change the SQL unless the next reviewer explicitly blocks it.

```sql
{sql.strip()}
```
"""

    def _format_task(self) -> str:
        return (
            f"Question ID: {self._task.get('question_id', '(unknown)')}\n"
            f"Question: {self._task.get('question', '(unknown)')}\n"
            f"Evidence: {self._task.get('evidence', '(none)') or '(none)'}"
        )

    def _format_reports(self) -> str:
        return "\n\n".join(self._reports) if self._reports else "(none)"

    @staticmethod
    def _extract_task(messages: list[dict]) -> dict[str, str]:
        fallback = ""
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            text = str(msg.get("content") or "")
            if text.startswith("BIRD README reviewer") or text.startswith("You are a new Text-to-SQL"):
                continue
            if not fallback:
                fallback = text.strip()
            question_id = _extract_line_value(text, "Question ID:")
            if "问题：" in text:
                return {
                    "question_id": question_id,
                    "question": _extract_between_any(text, "问题：", ["\n\n提示：", "\n提示："]).strip(),
                    "evidence": _extract_after(text, "提示：").strip(),
                }
            question = _extract_line_value(text, "Question:")
            if question:
                return {
                    "question_id": question_id,
                    "question": question,
                    "evidence": _extract_line_value(text, "Evidence:"),
                }
        if fallback:
            return {
                "question_id": "",
                "question": fallback,
                "evidence": "",
            }
        return {}

    @staticmethod
    def _recent_grounded_context(tool_history: list) -> str:
        items = []
        for name, args, result in tool_history[-16:]:
            if name == "meta":
                ref = args.get("ref") or args.get("path") or ""
                if ref:
                    items.append(f"- meta {ref}")
            elif name == "find":
                query = args.get("query") or args.get("pattern") or args.get("q") or ""
                preview = _single_line(result, 500)
                items.append(f"- find {query}\n  result_preview: {preview}")
            elif name == "query":
                sql = str(args.get("sql") or "").strip()
                preview = _single_line(result, 700)
                if sql:
                    items.append(f"- query: {sql}\n  result_preview: {preview}")
        return "\n".join(items) if items else "(none)"

    @staticmethod
    def _normalize_sql(sql: str) -> str:
        return re.sub(r"\s+", " ", (sql or "").strip()).lower()

    @staticmethod
    def _extract_sql_from_text(text: str) -> Optional[str]:
        candidates = []
        for match in re.finditer(r"```(?:sql)?\s*(.*?)\s*```", text or "", re.DOTALL | re.IGNORECASE):
            sql = match.group(1).strip()
            if BirdSchemaChallengeController._looks_like_select(sql):
                candidates.append(sql)
        return candidates[-1] if candidates else None

    @staticmethod
    def _looks_like_select(sql: str) -> bool:
        text = sql.strip()
        while True:
            stripped = re.sub(r"^\s*--[^\n]*(?:\n|$)", "", text, count=1)
            if stripped == text:
                break
            text = stripped
        return bool(re.match(r"^(select|with)\b", text.strip(), re.IGNORECASE))


def _extract_after(text: str, marker: str) -> str:
    idx = text.find(marker)
    if idx < 0:
        return ""
    return text[idx + len(marker):]


def _extract_line_value(text: str, marker: str) -> str:
    match = re.search(rf"^{re.escape(marker)}\s*(.*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _extract_between_any(text: str, start: str, ends: list[str]) -> str:
    s = text.find(start)
    if s < 0:
        return ""
    s += len(start)
    end_positions = [text.find(end, s) for end in ends]
    end_positions = [pos for pos in end_positions if pos >= 0]
    e = min(end_positions) if end_positions else -1
    return text[s:e] if e >= 0 else text[s:]


def _single_line(text: object, limit: int) -> str:
    value = str(text or "").strip().replace("\n", " ")
    if len(value) > limit:
        return value[:limit] + "..."
    return value
