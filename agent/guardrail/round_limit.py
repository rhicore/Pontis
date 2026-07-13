"""轮次上限硬性拦截 — 到达上限要求模型直接输出。"""
from agent.guardrail_api import Guardrail, GuardrailContext, CallVerdict

_STOP_TEMPLATE = """\
⚠️ 已达到工具调用上限（{max_rounds} 轮）。现在进入最终输出阶段。

基于你目前已掌握的信息，直接完成你的任务：
- 如果你正在生成 SQL，直接输出你当前最佳理解的 SQL
- 如果你正在分析数据，总结你已发现的内容
- 如果你正在写总结，直接输出已有的总结内容

直接输出结果。"""


class RoundLimit(Guardrail):
    """轮次上限硬性拦截。"""

    def __init__(self, max_rounds: int, stop_template: str = None):
        self.max_rounds = max_rounds
        self.stop_template = stop_template or _STOP_TEMPLATE

    def check(self, ctx: GuardrailContext) -> dict:
        if self.max_rounds <= 0:
            return {}
        if ctx.rounds < self.max_rounds:
            return {}
        msg = self.stop_template.format(max_rounds=self.max_rounds)
        return {i: CallVerdict("block", msg, finalize=True)
                for i in range(len(ctx.pending_calls))}
