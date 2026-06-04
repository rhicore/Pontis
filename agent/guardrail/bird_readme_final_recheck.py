"""Final BIRD README reviewer before releasing BIRD text output."""
from __future__ import annotations

import json
import os
import re
import hashlib
from typing import Any, Optional

from openai import OpenAI

from agent.guardrail_api import CallVerdict, Guardrail, GuardrailContext
from agent.guardrail.bird_readme_rule_retriever import (
    format_rule_cards,
    retrieve_bird_readme_rules,
)
from agent.guardrail.sql_utils import get_sql_from_messages
from agent.utils import load_agent_config

try:
    from scripts.BIRD.bird_readme import build_bird_readme_system_prompt
except Exception:  # pragma: no cover - optional benchmark dependency
    build_bird_readme_system_prompt = None

ENABLE_FINAL_RECHECK = True


def _review_mode() -> str:
    mode = os.environ.get("PONTIS_BIRD_README_FINAL_RECHECK_MODE", "reviewer").strip().lower()
    if mode in {"off", "disabled", "none", "0", "false"}:
        return "off"
    return "reviewer"


_REVIEWER_SYSTEM_PROMPT = """\
You are a BIRD README release reviewer for a Text-to-SQL guardrail.

Decide whether the current candidate SQL should be released under the retrieved
README candidate rules, question, evidence, SQL, and grounded tool observations.
Do not solve the task from scratch and do not output a replacement SQL.

Scope:
- Use only retrieved candidate README rules and previous required actions.
- Do not invent rule ids or use rules that were not retrieved.
- Treat retrieved README rules as authoritative; SQL reports, judge/audit text,
  and tool observations are context, not permission to waive a retrieved rule.
- Do not act as a schema-linking challenger. If the only concern is that a
  different table, column, join path, or entity grounding might be better,
  approve instead of blocking.
- Reject only when the current context shows that a retrieved README rule
  requires a concrete SQL edit.
- On later review turns, first verify that every previous required action was
  actually executed, then review the revised SQL against the current retrieved
  rules.

Return JSON only:
{
  "approved": false,
  "previous_required_actions_satisfied": true,
  "selected_rules": [
    {
      "rule_id": "Rxx",
      "rule_text": "Original README rule text.",
      "risk_reason": "Why this candidate SQL violates or may violate this rule.",
      "required_action": "Concrete edit the main agent must make to satisfy the rule, without writing the full SQL. Do not name a new table/column to use unless that exact table/column is already specified by question/evidence or the quoted README rule; otherwise phrase the action as re-checking the complete phrase/source against available metadata."
    }
  ],
  "unmet_previous_rules": [
    {
      "rule_id": "Rxx",
      "reason": "Why a previous required action is still unmet."
    }
  ],
  "review_summary": "One sentence summary. If approved, explain that no material README violation was found."
}

Constraints:
- If this is the first review for the candidate answer, previous_required_actions_satisfied must be true and unmet_previous_rules must be empty.
- If there were previous required actions in this review thread, approved can be true only when previous_required_actions_satisfied is true.
- If any previous required action is not fully executed, approved must be false and selected_rules must include the still-unmet README rule.
- If approved is true, selected_rules must be empty.
- If approved is false, return one or more rules. There is no upper limit; include all material remaining violations.
- Do not invent rule ids.
- Do not output a replacement SQL.
- Do not reject merely to ask the main agent to verify something.
- Do not turn schema-linking uncertainty into a README rejection.
"""


_BLOCK_MESSAGE_TEMPLATE = """\
BIRD README reviewer rejected the current final SQL.

Re-read the question, evidence, schema/tool observations, and candidate SQL.
You must revise the SQL to satisfy every selected README rule and required action below.
The reviewer feedback is not a schema-linking challenge. Do not change table,
join path, or entity grounding solely because another path might also be
plausible.
Do not explain your reasoning; output only the revised final SQL.

Required output format:
```sql
-- final SQLite SELECT
```

Selected README rules:
{selected_rules}
"""

_REVIEW_RETRY_TEMPLATE = """\
BIRD README reviewer could not complete the release check for the current final SQL.

Do not change the SQL and do not call tools. Output only the same candidate SQL
again in a ```sql``` block so the release reviewer can retry with a compact
review packet.

```sql
{sql}
```
"""


class BirdReadmeFinalRecheck(Guardrail):
    """Keep reviewing final BIRD SQL until the reviewer approves it."""

    def __init__(self) -> None:
        self._config: Optional[dict[str, Any]] = None
        self._client: Optional[OpenAI] = None
        self._approved_sql: set[tuple[str, int, str]] = set()
        self._feedback_count = 0
        self._review_messages: list[dict[str, str]] = []
        self._last_blocked_sql_key: Optional[str] = None
        self._last_selected_rules_text = ""
        self._last_candidate_rule_ids: set[str] = set()
        self._review_error_count = 0

    def check(self, ctx: GuardrailContext) -> dict:
        release_sql = getattr(ctx.agent, "_bird_schema_challenge_release_sql", None)
        if ctx.pending_calls:
            if release_sql:
                self._log(ctx, "BIRD README final recheck skipped: pending tool calls")
            return {}
        if build_bird_readme_system_prompt is None:
            if release_sql:
                self._log(ctx, "BIRD README final recheck skipped: README prompt unavailable")
            return {}
        if _review_mode() == "off" or not ENABLE_FINAL_RECHECK:
            if release_sql:
                self._log(ctx, "BIRD README final recheck skipped: review mode off")
            return {}
        schema_challenge_active = (
            getattr(ctx.agent, "_bird_schema_challenge_enabled", False)
            and not getattr(ctx.agent, "_bird_schema_challenge_allow_final_recheck", False)
        )
        legacy_multi_report_active = (
            getattr(ctx.agent, "_bird_multi_report_enabled", False)
            and not getattr(ctx.agent, "_bird_multi_report_allow_final_recheck", False)
        )
        if schema_challenge_active or legacy_multi_report_active:
            if release_sql:
                self._log(
                    ctx,
                    "BIRD README final recheck skipped: schema challenge still active "
                    f"allow={getattr(ctx.agent, '_bird_schema_challenge_allow_final_recheck', None)}",
                )
            return {}

        previous_answer = (ctx.last_response or "").strip()
        if not previous_answer:
            if release_sql:
                self._log(ctx, "BIRD README final recheck skipped: empty previous answer")
            return {}
        sql = (
            self._extract_sql_from_text(previous_answer)
            or (release_sql if isinstance(release_sql, str) and release_sql.strip() else None)
            or get_sql_from_messages(ctx.messages)
        )
        if not sql:
            if release_sql:
                self._log(ctx, "BIRD README final recheck skipped: no SQL extracted")
            return {}
        sql_key = self._normalize_sql(sql)
        approval_key = self._approval_key(ctx, sql_key)
        if approval_key in self._approved_sql:
            if release_sql:
                self._log(ctx, "BIRD README final recheck skipped: SQL already approved")
            return {}
        if self._last_blocked_sql_key == sql_key and self._last_selected_rules_text:
            self._feedback_count += 1
            return {
                "text": CallVerdict(
                    "block",
                    _BLOCK_MESSAGE_TEMPLATE.format(selected_rules=self._last_selected_rules_text),
                )
            }

        bird_readme = build_bird_readme_system_prompt()
        self._ensure_review_messages()
        packet = self._build_review_packet(
            ctx,
            sql,
            previous_answer,
            bird_readme,
            require_rule_selection=(self._feedback_count == 0),
        )
        try:
            review = self._review(packet, ctx)
            self._review_error_count = 0
            if (
                self._feedback_count > 0
                and bool(review.get("approved"))
                and not bool(review.get("previous_required_actions_satisfied"))
            ):
                selected_rules = self._previous_actions_not_satisfied_rules(review, bird_readme)
            elif self._feedback_count > 0 and bool(review.get("approved")):
                self._approved_sql.add(approval_key)
                return {}
            else:
                selected_rules = self._format_selected_rules(review, bird_readme, ctx)
        except Exception as exc:
            self._review_error_count += 1
            self._log(
                ctx,
                "BIRD README review failed: "
                f"{type(exc).__name__}: {str(exc)[:500]}",
            )
            return {
                "text": CallVerdict(
                    "block",
                    _REVIEW_RETRY_TEMPLATE.format(sql=sql.strip()),
                )
            }

        if not selected_rules.strip():
            self._approved_sql.add(approval_key)
            return {}

        self._feedback_count += 1
        self._last_blocked_sql_key = sql_key
        self._last_selected_rules_text = selected_rules
        return {
            "text": CallVerdict(
                "block",
                _BLOCK_MESSAGE_TEMPLATE.format(selected_rules=selected_rules),
            )
        }

    def _ensure_client(self, ctx: GuardrailContext) -> tuple[OpenAI, str]:
        if self._config is None:
            self._config = load_agent_config(getattr(ctx.workspace, "project_path", None))
        if self._client is None:
            self._client = OpenAI(
                api_key=self._config["api_key"],
                base_url=self._config["provider"],
                timeout=120.0,
            )
        return self._client, self._config["model"]

    def _review(self, packet: str, ctx: GuardrailContext) -> dict[str, Any]:
        client, model = self._ensure_client(ctx)
        self._review_messages.append({"role": "user", "content": packet})
        response = client.chat.completions.create(
            model=model,
            messages=self._review_messages,
            temperature=0,
        )
        content = response.choices[0].message.content or ""
        self._review_messages.append({"role": "assistant", "content": content})
        parsed = self._parse_review(content)
        self._log(
            ctx,
            "BIRD README review decision: "
            f"approved={parsed.get('approved')} "
            f"previous_required_actions_satisfied={parsed.get('previous_required_actions_satisfied')} "
            f"selected_rules={[item.get('rule_id') for item in parsed.get('selected_rules', []) if isinstance(item, dict)]} "
            f"summary={parsed.get('review_summary', '')}",
        )
        return parsed

    def _ensure_review_messages(self) -> None:
        if self._review_messages:
            return
        self._review_messages = [
            {"role": "system", "content": _REVIEWER_SYSTEM_PROMPT},
        ]

    def _build_review_packet(
        self,
        ctx: GuardrailContext,
        sql: str,
        previous_answer: str,
        bird_readme: str,
        require_rule_selection: bool = False,
    ) -> str:
        task = self._extract_task(ctx.messages)
        recent_context = self._combined_grounded_context(ctx)
        retrieved_rules = retrieve_bird_readme_rules(
            bird_readme,
            question=task.get("question", ""),
            evidence=task.get("evidence", ""),
            sql=sql,
            recent_context=recent_context,
            top_k=int(os.environ.get("PONTIS_BIRD_README_RULE_RETRIEVAL_TOPK", "24") or 24),
            project_path=getattr(ctx.workspace, "project_path", None),
        )
        self._last_candidate_rule_ids = {card.rule_id for card in retrieved_rules}
        self._log(
            ctx,
            "BIRD README retrieved candidate rules: "
            + ", ".join(card.rule_id for card in retrieved_rules),
        )
        retrieved_rule_text = format_rule_cards(retrieved_rules)
        previous_actions = (
            f"[Previous reviewer required actions]\n{self._last_selected_rules_text}\n\n"
            if self._last_selected_rules_text
            else ""
        )
        review_instruction = (
            "First release check: select only grounded violations of the retrieved README rules. Approve if none are present."
            if require_rule_selection
            else "Continuation check: verify previous required actions first, then select any remaining grounded violations of the retrieved README rules."
        )
        compact_answer = self._compact_text(previous_answer.strip(), 1200)
        compact_context = self._compact_text(recent_context, 6000)
        return f"""\
{previous_actions}[Question]
{task.get("question", "(unknown)")}

[Evidence]
{task.get("evidence", "(none)") or "(none)"}

[Candidate final SQL]
```sql
{sql.strip()}
```

[Raw final answer text]
{compact_answer}

[Retrieved candidate README rules]
{retrieved_rule_text}

[Recent grounded tool observations]
{compact_context}

{review_instruction}
"""

    @staticmethod
    def _parse_review(content: str) -> dict[str, Any]:
        parsed = None
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    parsed = None
        if not isinstance(parsed, dict):
            raise ValueError("reviewer returned malformed JSON")
        parsed["approved"] = BirdReadmeFinalRecheck._coerce_approved(parsed.get("approved"))
        parsed["previous_required_actions_satisfied"] = BirdReadmeFinalRecheck._coerce_approved(
            parsed.get("previous_required_actions_satisfied", False)
        )
        rules = parsed.get("selected_rules")
        if not isinstance(rules, list):
            raise ValueError("reviewer JSON missing selected_rules list")
        if parsed["approved"]:
            parsed["selected_rules"] = []
            return parsed
        parsed["selected_rules"] = rules
        return parsed

    @staticmethod
    def _log(ctx: GuardrailContext, message: str) -> None:
        logger = getattr(getattr(ctx, "agent", None), "logger", None)
        if logger is not None:
            logger.info(message)

    def _previous_actions_not_satisfied_rules(self, review: dict[str, Any], bird_readme: str) -> str:
        unmet = review.get("unmet_previous_rules")
        prefix = "Reviewer approved without confirming all previous required actions. Continue enforcing the previous reviewer requirements."
        if isinstance(unmet, list) and unmet:
            rule_map = self._readme_rule_map(bird_readme)
            lines = [prefix]
            seen = set()
            for item in unmet:
                if not isinstance(item, dict):
                    continue
                rid = str(item.get("rule_id") or "").strip().upper()
                if rid not in rule_map or rid in seen:
                    continue
                seen.add(rid)
                lines.append(f"{len(seen)}. {rule_map[rid]}")
                reason = str(item.get("reason") or "").strip()
                if reason:
                    lines.append(f"   Risk: Previous required action is still unmet. {reason}")
                lines.append("   Required action: Fully execute the previous reviewer required action for this rule before final release.")
            if seen:
                return "\n".join(lines)
        if self._last_selected_rules_text:
            return prefix + "\n" + self._last_selected_rules_text
        return self._fallback_selected_rules(ValueError("previous required actions were not confirmed"), bird_readme)

    @staticmethod
    def _readme_rule_map(bird_readme: str) -> dict[str, str]:
        rules = {}
        for line in bird_readme.splitlines():
            match = re.match(r"^(R\d+)\.\s+(.*)$", line.strip())
            if match:
                rules[match.group(1)] = f"{match.group(1)}. {match.group(2).strip()}"
        return rules

    @staticmethod
    def _coerce_approved(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "yes", "1", "approved", "pass"}
        return False

    def _format_selected_rules(self, review: dict[str, Any], bird_readme: str, ctx: GuardrailContext) -> str:
        rule_map = self._readme_rule_map(bird_readme)
        lines = []
        seen = set()
        item_no = 0
        for item in review.get("selected_rules", []):
            if not isinstance(item, dict):
                continue
            rid = str(item.get("rule_id") or "").strip().upper()
            if rid not in rule_map or rid in seen:
                continue
            if self._last_candidate_rule_ids and rid not in self._last_candidate_rule_ids:
                continue
            reason = str(item.get("risk_reason") or "").strip()
            action = str(item.get("required_action") or "").strip()
            seen.add(rid)
            item_no += 1
            lines.append(f"{item_no}. {rule_map[rid]}")
            if reason:
                lines.append(f"   Risk: {reason}")
            if action:
                lines.append(f"   Required action: {action}")
        if lines:
            summary = str(review.get("review_summary") or "").strip()
            if summary:
                return summary + "\n" + "\n".join(lines)
            return "\n".join(lines)
        return ""

    def _fallback_selected_rules(self, exc: Exception, bird_readme: str) -> str:
        return ""

    @staticmethod
    def _compact_text(text: str, max_chars: int) -> str:
        text = str(text or "").strip()
        if len(text) <= max_chars:
            return text
        head = max_chars // 2
        tail = max_chars - head - 80
        return (
            text[:head].rstrip()
            + "\n...[omitted for compact release review]...\n"
            + text[-tail:].lstrip()
        )

    def _extract_task(self, messages: list[dict]) -> dict[str, str]:
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            text = str(msg.get("content") or "")
            if text.startswith("BIRD README reviewer"):
                continue
            if "问题：" in text:
                question = self._extract_between(text, "问题：", "\n\n提示：")
                evidence = self._extract_after(text, "提示：").strip()
                return {"question": question.strip(), "evidence": evidence.strip()}
            question = self._extract_line_value(text, "Question:")
            if question:
                return {
                    "question": question,
                    "evidence": self._extract_line_value(text, "Evidence:"),
                }
        return {}

    @staticmethod
    def _extract_between(text: str, start: str, end: str) -> str:
        s = text.find(start)
        if s < 0:
            return ""
        s += len(start)
        e = text.find(end, s)
        if e < 0:
            return text[s:]
        return text[s:e]

    @staticmethod
    def _extract_after(text: str, marker: str) -> str:
        idx = text.find(marker)
        if idx < 0:
            return ""
        return text[idx + len(marker):]

    @staticmethod
    def _extract_line_value(text: str, marker: str) -> str:
        match = re.search(rf"^{re.escape(marker)}\s*(.*)$", text, re.MULTILINE)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _recent_grounded_context(tool_history: list) -> str:
        items = []
        for name, args, result in tool_history[-30:]:
            if name == "meta":
                ref = args.get("ref") or args.get("path") or ""
                if ref:
                    items.append(f"- meta {ref}")
            elif name == "query":
                sql = str(args.get("sql") or "").strip()
                preview = str(result or "").strip().replace("\n", " ")
                if len(preview) > 500:
                    preview = preview[:500] + "..."
                if sql:
                    items.append(f"- query: {sql}\n  result_preview: {preview}")
        return "\n".join(items) if items else "(none)"

    def _combined_grounded_context(self, ctx: GuardrailContext) -> str:
        parts = []
        recent = self._recent_grounded_context(ctx.tool_history)
        if recent and recent != "(none)":
            parts.append(recent)
        preserved = self._schema_challenge_grounded_context(ctx)
        if preserved:
            parts.append(
                "[Preserved schema-challenge grounded observations]\n"
                + self._compact_text(preserved, 3000)
            )
        return "\n".join(parts) if parts else "(none)"

    @staticmethod
    def _schema_challenge_grounded_context(ctx: GuardrailContext) -> str:
        agent = getattr(ctx, "agent", None)
        text = str(getattr(agent, "_bird_schema_challenge_grounded_context", "") or "").strip()
        return text

    def _approval_key(self, ctx: GuardrailContext, sql_key: str) -> tuple[str, int, str]:
        context = self._combined_grounded_context(ctx)
        digest = hashlib.sha1(context.encode("utf-8", errors="ignore")).hexdigest()
        return (sql_key, len(ctx.tool_history or []), digest)

    @staticmethod
    def _normalize_sql(sql: str) -> str:
        return re.sub(r"\s+", " ", sql.strip()).lower()

    @staticmethod
    def _extract_sql_from_text(text: str) -> str:
        match = re.search(r"```sql\s*(.*?)\s*```", text or "", re.DOTALL | re.IGNORECASE)
        return match.group(1).strip() if match else ""
