"""探索纪律检查 — 阻止低质量的全图枚举式起手。"""

from agent.guardrail_api import CallVerdict, Guardrail, GuardrailContext


class ExplorationCheck(Guardrail):
    """阻止把 find('*') 当作起手式探索。"""

    def check(self, ctx: GuardrailContext) -> dict:
        result = {}
        history_names = [name for name, _, _ in ctx.tool_history]

        for i, (name, args) in enumerate(ctx.pending_calls):
            if name != "find":
                continue

            ref = str(args.get("ref", "")).strip()
            broad_project_inventory = ref.endswith("::*")
            if ref != "*" and not broad_project_inventory:
                continue

            if history_names:
                result[i] = CallVerdict(
                    "warn",
                    "避免使用项目级全图枚举。请优先改用更定向的查询，例如数据库文件、表、列、关系或已知邻居。"
                )
                continue

            result[i] = CallVerdict(
                "block",
                "不要把项目级全图枚举作为第一步。请先从更定向的入口开始，例如 "
                "`*.sqlite`、`*:file:db/*:table`、`financial.sqlite/*:table` 或某个已知实体的邻居。"
            )

        return result
