"""Read-only tool progress control for duplicate calls and call budgets."""

import json

import sqlglot

from agent.guardrail_api import CallVerdict, Guardrail, GuardrailContext


class ToolUseCheck(Guardrail):
    """Reuse exact prior facts and end exploration at configured budgets."""

    def __init__(self, limits: dict[str, int] | None = None):
        self.limits = {
            str(name): int(limit)
            for name, limit in (limits or {}).items()
            if int(limit) > 0
        }

    def check(self, ctx: GuardrailContext) -> dict:
        verdicts = {}
        history = list(ctx.tool_history)
        seen = {
            self._signature(name, args): result
            for name, args, result in history
        }
        counts = {}
        for name, _args, _result in history:
            counts[name] = counts.get(name, 0) + 1
        total = len(history)

        for index, (name, args) in enumerate(ctx.pending_calls):
            signature = self._signature(name, args)
            if signature in seen:
                prior = str(seen[signature])
                if len(prior) > 1200:
                    prior = prior[:1200] + "\n... (prior result truncated)"
                verdicts[index] = CallVerdict(
                    "block",
                    "该调用与已完成调用相同，直接沿用已有结果：\n" + prior,
                )
                continue

            total_limit = self.limits.get("*")
            tool_limit = self.limits.get(name)
            if (total_limit is not None and total >= total_limit) or (
                tool_limit is not None and counts.get(name, 0) >= tool_limit
            ):
                verdicts[index] = CallVerdict(
                    "block",
                    "工具探索额度已用完。请基于已经获得的 schema、关系和查询结果输出当前最佳答案。",
                    finalize=True,
                )
                continue

            seen[signature] = "本轮已有相同调用。"
            counts[name] = counts.get(name, 0) + 1
            total += 1

        return verdicts

    @staticmethod
    def _signature(name: str, args: dict) -> tuple[str, str]:
        normalized = dict(args or {})
        if name == "query" and isinstance(normalized.get("sql"), str):
            sql = normalized["sql"].strip().rstrip(";")
            try:
                sql = sqlglot.parse_one(sql, read="sqlite").sql(dialect="sqlite")
            except Exception:
                sql = " ".join(sql.split())
            normalized["sql"] = sql
        return name, json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str)
