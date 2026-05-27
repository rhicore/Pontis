"""Force one final BIRD README self-review before releasing text output."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from agent.guardrail_api import CallVerdict, Guardrail, GuardrailContext
from agent.guardrail.sql_utils import (
    extract_col_refs,
    extract_tables,
    get_current_sql,
    resolve_entity_ref,
)
from agent.runtime_metrics import estimate_messages_tokens

try:
    from scripts.BIRD.bird_readme import build_bird_readme_system_prompt
except Exception:  # pragma: no cover - optional benchmark dependency
    build_bird_readme_system_prompt = None

logger = logging.getLogger(__name__)

ENABLE_SIDE_REVIEWER = False


_RECHECK_TEMPLATE = """\
Complete BIRD SQL writing conventions:
{bird_readme}

Review the previous final answer against these conventions before release.
Keep the same final SQL when it satisfies the conventions. Repair the SQL when
a convention calls for a different final SQL.

Required output format:
```sql
-- final SQLite SELECT
```
"""

_REVIEW_REPORT_TEMPLATE = """\
Independent reviewer report for the previous final answer:
{reviewer_report}

Treat this report as a mandatory release checklist. Address every BLOCK item and
every required change before producing the final SQL. When the report says PASS,
still run the same README-derived convention check yourself against the README
conventions already provided in the previous message.

Previous final answer:
{previous_answer}

Required output format:
```sql
-- final SQLite SELECT
```
"""

_REVIEWER_SYSTEM_TEMPLATE = """\
BIRD SQL writing conventions:
{bird_readme}

You are an independent final-answer reviewer for BIRD SQL convention compliance.
Use the conventions above, the question, evidence, final SQL, and the compact
schema summary to judge whether the final SQL is ready for release.

Review method:
- Treat the conventions above as the only source of review rules.
- First identify the convention sentences that apply to this task and SQL.
- Compare the current SQL behavior with those applicable convention sentences.
- Choose BLOCK when an applicable convention supports a different final SQL.
- Choose PASS only when the applicable convention sentences support the current
  SQL and no convention-driven change remains.
- Base every violation and every PASS rationale on the convention text, not on
  general SQL intuition alone.

Return strict JSON with this schema:
{{
  "decision": "PASS" | "BLOCK",
  "checked_conventions": ["applicable README convention sentences considered"],
  "violations": [
    {{
      "convention": "README convention sentence or short quote",
      "observed_sql_behavior": "what the current SQL does",
      "required_sql_behavior": "what the final SQL should do"
    }}
  ],
  "required_changes": ["concrete SQL edits or checks for the main agent"],
  "rationale": "brief reason"
}}
"""

_REVIEWER_USER_TEMPLATE = """\
Question:
{question}

Evidence:
{evidence}

Final SQL:
```sql
{sql}
```

Previous final answer text:
{previous_answer}

Compact schema summary for entities referenced by the SQL:
{schema_summary}
"""


class BirdReadmeFinalRecheck(Guardrail):
    """Block the first final text response and append BIRD README review context."""

    def __init__(self) -> None:
        self._triggered = False
        self._pending_report_messages: list[str] = []

    def check(self, ctx: GuardrailContext) -> dict:
        if ctx.pending_calls or self._triggered:
            return {}
        if build_bird_readme_system_prompt is None:
            return {}

        previous_answer = (ctx.last_response or "").strip()
        if not previous_answer:
            return {}

        self._triggered = True
        bird_readme = build_bird_readme_system_prompt()
        if ENABLE_SIDE_REVIEWER:
            reviewer_report = self._run_side_review(ctx, bird_readme, previous_answer)
            self._pending_report_messages.append(
                _REVIEW_REPORT_TEMPLATE.format(
                    previous_answer=previous_answer,
                    reviewer_report=reviewer_report,
                )
            )
        message = _RECHECK_TEMPLATE.format(
            bird_readme=bird_readme,
        )
        return {"text": CallVerdict("block", message)}

    def drain_ready(self, ctx: GuardrailContext) -> list[str]:
        messages = list(self._pending_report_messages)
        self._pending_report_messages.clear()
        return messages

    def _run_side_review(self, ctx: GuardrailContext, bird_readme: str,
                         previous_answer: str) -> str:
        agent = getattr(ctx, "agent", None)
        client = getattr(agent, "client", None)
        config = getattr(agent, "config", {}) or {}
        if client is None or not config.get("model"):
            return "SIDE_REVIEW_UNAVAILABLE: no agent client/model is available."

        sql = get_current_sql(ctx) or _extract_sql_fallback(previous_answer)
        task = _extract_task(ctx.messages)
        schema_summary = _build_schema_summary(ctx, sql)
        system_message = {
            "role": "system",
            "content": _REVIEWER_SYSTEM_TEMPLATE.format(bird_readme=bird_readme),
        }
        user_message = {
            "role": "user",
            "content": _REVIEWER_USER_TEMPLATE.format(
                question=task.get("question") or "(unknown)",
                evidence=task.get("evidence") or "(none)",
                sql=sql or "(no SQL block detected)",
                previous_answer=previous_answer,
                schema_summary=schema_summary,
            ),
        }
        messages = [system_message, user_message]

        kwargs: dict[str, Any] = {
            "model": config["model"],
            "messages": messages,
        }
        if config.get("thinking", False):
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            kwargs["reasoning_effort"] = config.get("thinking_effort", "high")
        else:
            kwargs["temperature"] = 0

        try:
            response = client.chat.completions.create(**kwargs)
            if hasattr(agent, "_record_llm_usage"):
                agent._record_llm_usage(
                    response,
                    static_prompt_tokens=estimate_messages_tokens([system_message]),
                    prompt_text=None,
                )
            content = response.choices[0].message.content or ""
            report = content.strip()
            logger.info("BIRD README side reviewer report:\n%s", report)
            return report or "SIDE_REVIEW_EMPTY: reviewer returned an empty report."
        except Exception as exc:  # pragma: no cover - provider/network dependent
            logger.warning("BIRD README side reviewer failed: %s", exc)
            return f"SIDE_REVIEW_FAILED: {exc}"


def _extract_sql_fallback(text: str) -> str:
    match = re.search(r"```(?:sql)?\s*(.*?)\s*```", text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    select_match = re.search(r"\bSELECT\b.*", text, re.IGNORECASE | re.DOTALL)
    if select_match:
        return select_match.group(0).strip()
    return ""


def _extract_task(messages: list[dict]) -> dict[str, str]:
    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        if "Question:" not in content and "问题：" not in content:
            continue
        question = _extract_line_value(content, "Question:")
        evidence = _extract_line_value(content, "Evidence:")
        if not question and "问题：" in content:
            question = _extract_between(content, "问题：", "\n\n提示：").strip()
            evidence = _extract_after(content, "提示：").strip()
        if question:
            return {"question": question, "evidence": evidence}
    return {"question": "", "evidence": ""}


def _extract_line_value(text: str, label: str) -> str:
    pattern = re.compile(rf"^{re.escape(label)}\s*(.*)$", re.MULTILINE)
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _extract_between(text: str, start: str, end: str) -> str:
    start_idx = text.find(start)
    if start_idx < 0:
        return ""
    start_idx += len(start)
    end_idx = text.find(end, start_idx)
    if end_idx < 0:
        return text[start_idx:]
    return text[start_idx:end_idx]


def _extract_after(text: str, label: str) -> str:
    idx = text.find(label)
    if idx < 0:
        return ""
    return text[idx + len(label):]


def _build_schema_summary(ctx: GuardrailContext, sql: str) -> str:
    if not sql or ctx.workspace is None:
        return "(not available)"
    try:
        tables, aliases = extract_tables(sql)
        columns = extract_col_refs(sql, aliases)
    except Exception as exc:
        return f"(SQL parse failed: {exc})"

    lines: list[str] = []
    seen_refs: set[str] = set()
    for table in sorted(tables)[:12]:
        ref = resolve_entity_ref(ctx.workspace, table)
        lines.append(_format_meta_line(ctx.workspace, "table", table, ref))
        if ref:
            seen_refs.add(ref)

    for table, column in columns[:40]:
        ref = resolve_entity_ref(ctx.workspace, table, column)
        if ref in seen_refs:
            continue
        lines.append(_format_meta_line(ctx.workspace, "column", f"{table}.{column}", ref))
        if ref:
            seen_refs.add(ref)

    return "\n".join(lines) if lines else "(no table or column references detected)"


def _format_meta_line(workspace, kind: str, raw_name: str, ref: str | None) -> str:
    meta = _load_meta(workspace, ref) if ref else {}
    parts = [f"- {kind}: {raw_name}"]
    if ref:
        parts.append(f"ref={ref}")
    for key in ("brief", "detail", "hints"):
        value = _compact_meta_value(meta.get(key))
        if value:
            parts.append(f"{key}={value}")
    return " | ".join(parts)


def _load_meta(workspace, ref: str | None) -> dict:
    if not ref:
        return {}
    candidates = [ref, ref.rsplit("/", 1)[-1]]
    for name in candidates:
        try:
            rows = workspace.cypher("MATCH (n {name: $name}) RETURN n LIMIT 1", params={"name": name})
        except Exception:
            return {}
        if rows:
            meta = rows[0].get("n") or {}
            if isinstance(meta, dict):
                return meta
    return {}


def _compact_meta_value(value: Any, limit: int = 360) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(item) for item in value[:6])
    elif isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        value = str(value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        return value[:limit - 3] + "..."
    return value
