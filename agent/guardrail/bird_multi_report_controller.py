"""BIRD schema-linking challenge controller for final SQL judging."""
from __future__ import annotations

import re
from typing import Optional

from agent.guardrail_api import CallVerdict, Guardrail, GuardrailContext
from agent.guardrail.sql_utils import get_sql_from_messages


class BirdSchemaChallengeController(Guardrail):
    """Turn a final SQL into schema-linking reports, then judge them.

    The controller only runs on text responses. It keeps the first report in the
    original context so the agent can summarize its own grounded exploration.
    Later challenge/judge phases reset the visible context to system messages
    plus accumulated reports.
    """

    def __init__(self, report_count: int = 2) -> None:
        self.report_count = max(1, int(report_count or 2))
        self._phase = "await_initial_sql"
        self._reports: list[str] = []
        self._task: dict[str, str] = {}
        self._last_sql_key: Optional[str] = None

    def check(self, ctx: GuardrailContext) -> dict:
        if ctx.pending_calls:
            return {}
        if ctx.agent is None:
            return {}

        setattr(ctx.agent, "_bird_schema_challenge_enabled", True)
        setattr(ctx.agent, "_bird_schema_challenge_allow_final_recheck", self._phase == "released")

        response = (ctx.last_response or "").strip()
        if not response:
            return {}
        sql = get_sql_from_messages(ctx.messages)
        if not sql:
            return {}

        if self._phase == "released":
            setattr(ctx.agent, "_bird_schema_challenge_allow_final_recheck", True)
            return {}

        if self._phase == "await_initial_sql":
            self._task = self._extract_task(ctx.messages)
            self._last_sql_key = self._normalize_sql(sql)
            self._phase = "await_report"
            return {
                "text": CallVerdict(
                    "block",
                    self._build_first_report_prompt(ctx, sql),
                )
            }

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
            self._phase = "released"
            self._last_sql_key = self._normalize_sql(sql)
            setattr(ctx.agent, "_bird_schema_challenge_allow_final_recheck", True)
            return {}

        return {}

    def _append_report(self, response: str) -> None:
        report_id = f"R{len(self._reports) + 1}"
        self._reports.append(f"[{report_id}]\n{response.strip()}")

    def _rewritten_messages(self, ctx: GuardrailContext, prompt: str) -> list[dict]:
        system_messages = getattr(ctx.agent, "_system_messages", None)
        if not system_messages:
            system_messages = [m for m in ctx.messages if m.get("role") == "system"]
        return list(system_messages) + [{"role": "user", "content": prompt}]

    def _build_first_report_prompt(self, ctx: GuardrailContext, sql: str) -> str:
        task = self._format_task()
        evidence = self._recent_grounded_context(ctx.tool_history)
        return f"""\
你刚才已经给出了候选最终 SQL。现在先不要释放最终答案。

请基于当前完整上下文复盘自己的 SQL 写作过程，输出一份结构化 SQL report。必须重复原始问题和 evidence，不要省略；必须重点说明 schema linking 决策：目标表、目标字段、实体/值定位、join path、行粒度、输出目标和已经排除的候选表/字段/连接路径。

必须使用以下格式：

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
- aggregation grain:
- output columns:

[Exploration Evidence]
- tool observations:
- rejected paths:

[Candidate SQL]
```sql
-- candidate SQLite SELECT
```

[Known Risks]
- ...

原始任务：
{task}

你刚才的候选 SQL：
```sql
{sql.strip()}
```

近期工具证据：
{evidence}
"""

    def _build_challenge_prompt(self, report_no: int) -> str:
        return f"""\
你现在作为一个新的 BIRD Text-to-SQL schema-linking challenge 智能体工作。

下面是其他智能体已经生成的 SQL report。你的职责是挑战 schema linking，不是做 SQL 语法审稿。请重新从原始问题和 evidence 出发，主动寻找是否存在不同且更合理的表、字段、join path、实体标识列、行粒度、输出目标或值来源。

重点检查：
- 同一自然语言概念是否可能对应另一张表或另一列；
- 当前 SQL 是否把维表/事实表、实体行/事件行、版本行/实体行混用；
- JOIN path 是否还有另一条能表达题意的路径；
- 输出的是不是 question/evidence 要求的实体标识或属性；
- 过滤值是否来自正确表的正确字段。

不要把主要精力放在 SQLite 语法、日期格式函数、ORDER BY 展示顺序、别名、空格、大小写、轻微格式化或可等价改写的表达式上。只有这些问题会改变 schema linking、目标行集或输出目标时，才作为辅助证据提及。

不要为已有 report 辩护；如果最终同意已有 schema linking，也必须说明你检查过哪些替代表/字段/JOIN/粒度候选，为什么没有更合理的替代路径。

你可以继续使用工具探索当前数据库。完成探索后，输出第 {report_no} 份结构化 SQL report，格式必须与前面一致，并包含一个候选 SQLite SELECT。

原始任务：
{self._format_task()}

已有 SQL reports：
{self._format_reports()}
"""

    def _build_judge_prompt(self) -> str:
        return f"""\
你现在作为 BIRD Text-to-SQL 裁判智能体工作。

下面有多份 SQL report。请优先比较 schema linking 质量：目标表、目标字段、join path、实体/事件/版本行粒度、输出标识或属性、过滤值来源。只有在 schema linking 等价时，再比较公式、排序、语法和展示细节。你可以使用工具核验关键 schema-linking 分歧。

最终输出必须包含：
- selected_report_id
- selection_reason
- 一个 ```sql``` 代码块，代码块内是一条最终 SQLite SELECT

原始任务：
{self._format_task()}

SQL reports：
{self._format_reports()}
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
        for msg in messages:
            if msg.get("role") != "user":
                continue
            text = str(msg.get("content") or "")
            if "Question ID:" not in text or "问题：" not in text:
                continue
            return {
                "question_id": _extract_line_value(text, "Question ID:"),
                "question": _extract_between(text, "问题：", "\n\n提示：").strip(),
                "evidence": _extract_after(text, "提示：").strip(),
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


def _extract_between(text: str, start: str, end: str) -> str:
    s = text.find(start)
    if s < 0:
        return ""
    s += len(start)
    e = text.find(end, s)
    if e < 0:
        return text[s:]
    return text[s:e]


def _extract_after(text: str, marker: str) -> str:
    idx = text.find(marker)
    if idx < 0:
        return ""
    return text[idx + len(marker):]


def _extract_line_value(text: str, marker: str) -> str:
    match = re.search(rf"^{re.escape(marker)}\s*(.*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _single_line(text: object, limit: int) -> str:
    value = str(text or "").strip().replace("\n", " ")
    if len(value) > limit:
        return value[:limit] + "..."
    return value
