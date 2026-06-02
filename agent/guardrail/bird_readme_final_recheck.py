"""Final BIRD README reviewer before releasing BIRD text output."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from openai import OpenAI

from agent.guardrail_api import CallVerdict, Guardrail, GuardrailContext
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

Your job is not to solve the task from scratch and not to output a replacement
SQL. Your job is to decide whether the current candidate SQL is acceptable under
the provided README rules, question, evidence, SQL, and grounded tool
observations.

Approval is very strict. Approve only when the SQL follows the README rules and
no selected rule would require changing the SQL. If an action-style README
rule's trigger matches the candidate SQL and the SQL contains the forbidden
action without explicit question/evidence support, reject it. If there is a
material doubt about a README violation, reject instead of approving; it is
better to block one extra revision than to release a likely invalid SQL. Select
rules by exact rule id such as R09 and quote the original rule text. Do not use
any rule that is not present in the README.

The candidate SQL may violate more than one README rule. Before deciding,
review the whole SQL against the README, including output target, filters,
joins, aggregation, deduplication, ordering, limits, and any clause whose
presence changes the result shape. Select every material README rule whose
required action would change the candidate SQL; do not stop after finding only
one issue.

When choosing among multiple violations, prefer rules that change the output
target, target grain, candidate rows, or aggregate value over rules that only
remove presentation details. Presentation-only issues should not crowd out a
remaining output-target or row-grain issue.

In a multi-turn release review, previously selected rules are not an exhaustive
checklist. After the main agent revises the SQL, review the revised SQL again
against the full README. If a previous issue is fixed but another material
README violation remains, reject again and select the remaining rule or rules.

Return JSON only:
{
  "approved": false,
  "previous_required_actions_satisfied": true,
  "selected_rules": [
    {
      "rule_id": "Rxx",
      "rule_text": "Original README rule text.",
      "risk_reason": "Why this candidate SQL violates or may violate this rule.",
      "required_action": "Concrete edit the main agent must make to satisfy the rule, without writing the full SQL."
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
"""


_BLOCK_MESSAGE_TEMPLATE = """\
BIRD README reviewer rejected the current final SQL.

Re-read the question, evidence, schema/tool observations, and candidate SQL.
You must revise the SQL to satisfy every selected README rule and required action below.
Do not explain your reasoning; output only the revised final SQL.

Required output format:
```sql
-- final SQLite SELECT
```

Selected README rules:
{selected_rules}
"""


class BirdReadmeFinalRecheck(Guardrail):
    """Keep reviewing final BIRD SQL until the reviewer approves it."""

    def __init__(self) -> None:
        self._config: Optional[dict[str, Any]] = None
        self._client: Optional[OpenAI] = None
        self._approved_sql: set[str] = set()
        self._feedback_count = 0
        self._review_messages: list[dict[str, str]] = []
        self._last_blocked_sql_key: Optional[str] = None
        self._last_selected_rules_text = ""

    def check(self, ctx: GuardrailContext) -> dict:
        if ctx.pending_calls:
            return {}
        if build_bird_readme_system_prompt is None:
            return {}
        if _review_mode() == "off" or not ENABLE_FINAL_RECHECK:
            return {}

        previous_answer = (ctx.last_response or "").strip()
        if not previous_answer:
            return {}
        sql = get_sql_from_messages(ctx.messages)
        if not sql:
            return {}
        sql_key = self._normalize_sql(sql)
        if sql_key in self._approved_sql:
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
        self._ensure_review_messages(bird_readme)
        packet = self._build_review_packet(
            ctx,
            sql,
            previous_answer,
            require_rule_selection=(self._feedback_count == 0),
        )
        try:
            review = self._review(packet, ctx)
            if (
                self._feedback_count > 0
                and bool(review.get("approved"))
                and not bool(review.get("previous_required_actions_satisfied"))
            ):
                selected_rules = self._previous_actions_not_satisfied_rules(review, bird_readme)
            elif self._feedback_count > 0 and bool(review.get("approved")):
                self._approved_sql.add(sql_key)
                return {}
            else:
                selected_rules = self._format_selected_rules(review, bird_readme)
        except Exception as exc:
            selected_rules = self._fallback_selected_rules(exc, bird_readme)

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
        return self._parse_review(content)

    def _ensure_review_messages(self, bird_readme: str) -> None:
        if self._review_messages:
            return
        self._review_messages = [
            {"role": "system", "content": _REVIEWER_SYSTEM_PROMPT},
            {"role": "system", "content": bird_readme},
        ]

    def _build_review_packet(
        self,
        ctx: GuardrailContext,
        sql: str,
        previous_answer: str,
        require_rule_selection: bool = False,
    ) -> str:
        task = self._extract_task(ctx.messages)
        recent_context = self._recent_grounded_context(ctx.tool_history)
        previous_actions = (
            f"[Previous reviewer required actions]\n{self._last_selected_rules_text}\n\n"
            if self._last_selected_rules_text
            else ""
        )
        review_instruction = (
            "This is the first release check for this answer. Do not approve on this pass; broadly inspect the whole candidate SQL against the full README and select every material README rule that the main agent must reconsider before release. Return at least one rule, with no upper limit."
            if require_rule_selection
            else "Continue the same release review thread. First verify every previous required action listed above. If any one is not fully executed, reject and include that still-unmet rule in selected_rules. Then re-check the whole revised SQL against the full README for any remaining material violation. Prior selected rules were not exhaustive. Approve only when the current SQL satisfies both all previous required actions and the full README."
        )
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
{previous_answer.strip()}

[Recent grounded tool observations]
{recent_context}

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
            match = re.match(r"^(R\d{2})\.\s+(.*)$", line.strip())
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

    def _format_selected_rules(self, review: dict[str, Any], bird_readme: str) -> str:
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
            seen.add(rid)
            item_no += 1
            reason = str(item.get("risk_reason") or "").strip()
            lines.append(f"{item_no}. {rule_map[rid]}")
            if reason:
                lines.append(f"   Risk: {reason}")
            action = str(item.get("required_action") or "").strip()
            if action:
                lines.append(f"   Required action: {action}")
        if lines:
            summary = str(review.get("review_summary") or "").strip()
            if summary:
                return summary + "\n" + "\n".join(lines)
            return "\n".join(lines)
        return self._fallback_selected_rules(
            ValueError("reviewer selected no valid README rules"), bird_readme
        )

    def _fallback_selected_rules(self, exc: Exception, bird_readme: str) -> str:
        rule_map = self._readme_rule_map(bird_readme)
        fallback_ids = ["R07", "R16", "R30"]
        lines = [f"Reviewer failed to select rules ({type(exc).__name__}: {exc}). Use these high-risk release-check rules:"]
        for idx, rid in enumerate(fallback_ids, 1):
            if rid in rule_map:
                lines.append(f"{idx}. {rule_map[rid]}")
        return "\n".join(lines)

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
        for name, args, result in tool_history[-12:]:
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

    @staticmethod
    def _normalize_sql(sql: str) -> str:
        return re.sub(r"\s+", " ", sql.strip()).lower()
