"""连续调用同一工具时提醒 — 防止模型反复 query 试错。"""
from agent.guardrail_api import Guardrail, GuardrailContext, CallVerdict


class ToolAbuse(Guardrail):
    """连续调用同一工具时提醒（不阻止执行，仅追加提醒）。"""

    def __init__(self, tool_name: str, consecutive_limit: int = 3,
                 message: str = None):
        self.tool_name = tool_name
        self.limit = consecutive_limit
        self.message = message or (
            f"你已连续{consecutive_limit}次调用 {tool_name}，请回顾已获取的信息，"
            "换一个思路或直接基于已有信息输出结论。"
        )

    def check(self, ctx: GuardrailContext) -> dict:
        names = [n for n, _, _ in ctx.tool_history] + [n for n, _ in ctx.pending_calls]
        if len(names) < self.limit:
            return {}
        if not all(n == self.tool_name for n in names[-self.limit:]):
            return {}
        return {i: CallVerdict("warn", self.message)
                for i, (name, _) in enumerate(ctx.pending_calls)
                if name == self.tool_name}
