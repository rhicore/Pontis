"""工具调用限制 — 限制 query 工具的总调用次数。"""
from agent.guardrail_api import Guardrail, GuardrailContext, CallVerdict


class ToolAbuse(Guardrail):
    """限制指定工具的总调用次数，超过后 block 并强制模型输出结论。"""

    def __init__(self, tool_name: str, total_limit: int = 5,
                 consecutive_limit: int = 3):
        self.tool_name = tool_name
        self.total_limit = total_limit
        self.consecutive_limit = consecutive_limit

    def check(self, ctx: GuardrailContext) -> dict:
        history_names = [n for n, _, _ in ctx.tool_history]
        total_calls = history_names.count(self.tool_name)
        result = {}

        for i, (name, _) in enumerate(ctx.pending_calls):
            if name != self.tool_name:
                continue

            # 总数限制：超过后直接 block
            if total_calls >= self.total_limit:
                result[i] = CallVerdict(
                    "block",
                    f"query 调用已达上限（{self.total_limit} 次）。"
                    "不要用 shell、脚本或其他外部执行方式绕过 query 限制；"
                    "请基于已有信息、glob/meta/search/grep 的结构化结果继续推理。"
                )
                continue

            remaining = self.total_limit - total_calls - 1  # 扣除本次
            msg_parts = [f"【query 使用统计】本次调用后已用 {total_calls + 1}/{self.total_limit} 次，剩余 {remaining} 次。"]

            # 连续调用提醒
            names = history_names + [n for n, _ in ctx.pending_calls[:i + 1]]
            if len(names) >= self.consecutive_limit and all(
                n == self.tool_name for n in names[-self.consecutive_limit:]
            ):
                msg_parts.append(
                    f"你已连续 {self.consecutive_limit} 次调用 query，"
                    "请先回顾已有 schema/元数据，必要时改用 glob/meta/search/grep 定向补充信息"
                )

            result[i] = CallVerdict("warn", " ".join(msg_parts))
            total_calls += 1

        return result
