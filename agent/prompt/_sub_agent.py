"""子智能体模式层 — 在 writer 基础上追加子智能体行为约束。"""

_SUB_AGENT_ADDITIONS = r"""## 子智能体须知

- 父智能体在任务描述中为你提供了必要的背景信息，直接利用这些上下文工作，不要重新从头探索全局结构
- 聚焦于分配给你的任务，完成后返回结果
- 不要浪费轮次重新 glob 全局结构，父智能体已经为你描述了数据库结构和任务背景
"""


def get_sub_agent_additions() -> str:
    return _SUB_AGENT_ADDITIONS
