"""连续调用同一工具时提醒 — 防止模型反复 query 试错。"""
from typing import Optional
from agent.guardrail import Guardrail, AgentState


class ToolAbuse(Guardrail):
    """连续调用同一工具时提醒。

    检查 tool_history 中最近 N 次调用是否全部为同一工具。
    """

    def __init__(self, tool_name: str, consecutive_limit: int = 3,
                 message: str = None):
        self.tool_name = tool_name
        self.limit = consecutive_limit
        self.message = message or (
            f"你已连续{consecutive_limit}次调用 {tool_name}，请回顾已获取的信息，"
            "换一个思路或直接基于已有信息输出结论。"
        )

    def check(self, state: AgentState, pending_calls: list) -> Optional[str]:
        # 累积历史 + 即将执行的调用
        past_names = [name for name, _, _ in state.tool_history]
        pending_names = [name for name, _ in pending_calls]
        all_names = past_names + pending_names

        if len(all_names) < self.limit:
            return None

        tail = all_names[-self.limit:]
        if all(n == self.tool_name for n in tail):
            return self.message
        return None
