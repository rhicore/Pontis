"""Final BIRD README self-check before releasing text output."""
from __future__ import annotations

import os

from agent.guardrail_api import CallVerdict, Guardrail, GuardrailContext

try:
    from scripts.BIRD.bird_readme import build_bird_readme_system_prompt
except Exception:  # pragma: no cover - optional benchmark dependency
    build_bird_readme_system_prompt = None

ENABLE_FINAL_RECHECK = True


def _review_mode() -> str:
    mode = os.environ.get("PONTIS_BIRD_README_FINAL_RECHECK_MODE", "direct").strip().lower()
    if mode in {"off", "disabled", "none", "0", "false"}:
        return "off"
    return "direct"


_DIRECT_README_TEMPLATE = """\
Use this as a release checklist:
- Keep the SQL if it already follows the conventions.
- Revise the SQL if any SELECT, WHERE, JOIN, GROUP BY, DISTINCT, ORDER BY, or
  LIMIT clause violates the conventions.
- Do not write a rebuttal or explanatory prose; output only the final SQL.

Required output format:
```sql
-- final SQLite SELECT
```

BIRD SQL writing conventions:
{bird_readme}
"""


class BirdReadmeFinalRecheck(Guardrail):
    """Force one final README self-check before releasing text output."""

    def __init__(self) -> None:
        self._feedback_sent = False

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
        if self._feedback_sent:
            return {}

        self._feedback_sent = True
        bird_readme = build_bird_readme_system_prompt()
        return {
            "text": CallVerdict(
                "block",
                _DIRECT_README_TEMPLATE.format(bird_readme=bird_readme),
            )
        }
