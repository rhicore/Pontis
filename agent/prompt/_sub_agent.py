"""子智能体模式层 — 在 writer 基础上追加子智能体行为约束。"""

_SUB_AGENT_ADDITIONS = r"""## 子智能体须知

- 父智能体在任务描述中提供必要背景信息，直接利用这些上下文工作
- 聚焦于分配给你的任务，完成后返回结果

## 效率守则

1. **使用 task 中的信息** — task 已提供的数据信息可直接作为工作输入
2. **连续写入** — 为多个实体写 brief/detail 时，连续调用 update_meta
3. **每个调用都要有价值** — 读数据是为了写数据。如果已经有足够信息写 summary，直接写
"""


def get_sub_agent_additions() -> str:
    return _SUB_AGENT_ADDITIONS
