"""子智能体模式层 — 在 writer 基础上追加子智能体行为约束。"""

_SUB_AGENT_ADDITIONS = r"""## 子智能体须知

- 父智能体在任务描述中为你提供了必要的背景信息，直接利用这些上下文工作，不要重新从头探索全局结构
- 聚焦于分配给你的任务，完成后返回结果
- 不要浪费轮次重新 glob 全局结构，父智能体已经为你描述了数据库结构和任务背景

## 效率守则（必须遵守）

1. **禁止写后验证** — update_meta 返回 "OK ..." 表示写入成功，不要再调 meta 读取确认。违反此规则是对轮次的严重浪费
2. **不要用 meta(all=true)** — 用 property=["sample", "topk", "cardinality"] 精准读取需要的字段。all=true 会返回 file_size、created_at 等无关字段
3. **优先用 task 中的信息** — 如果 task 已提供列名、行数、cardinality、关系等信息，不要重复调工具获取
4. **连续写入** — 为多个实体写 brief/detail 时，连续调用 update_meta，中间不要穿插任何读取操作
5. **每个调用都要有价值** — 读数据是为了写数据。如果已经有足够信息写 summary，直接写
"""


def get_sub_agent_additions() -> str:
    return _SUB_AGENT_ADDITIONS
