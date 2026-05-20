"""Guardrail 提示词 — 根据当前实际启用的 guardrail 动态生成。"""

from __future__ import annotations


_SECTIONS = {
    "query_abuse": """## Query 限制

- query 工具总共最多调用 **5 次**
- 连续 3 次调用 query 会触发提醒，建议先回顾已有信息
""",
    "sql_check": """## SQL 实体检查

- query 或最终 SQL 如果涉及尚未读取确认的关键实体，guardrail 会提醒或拦截
- 在引用关键表、列、关系前，优先先用 `meta` 读取确认语义
""",
    "bridge_check": """## JOIN 关系检查

- 如果 SQL 使用了尚未确认的 JOIN 关系，guardrail 会提醒
- 在多表查询前，优先读取外键、关系实体或相关表的摘要
""",
    "disambig_check": """## 消歧检查

- 如果 SQL 引用了存在同名/近名歧义的实体，guardrail 会提醒或拦截
- 在歧义字段上，优先读取相关列或消歧实体后再继续
""",
    "value_grounding_check": """## SQL Value Grounding 检查

- 最终 SQL 的过滤值会在对应数据库列中验证
- 0 命中值、低基数列上的模糊 LIKE、可精确匹配却使用 LIKE 的条件会被拦截
""",
    "exploration_check": """## 探索纪律

- exploration_check 会提醒或拦截过宽的全图枚举式探索
- 优先从更定向的入口开始，例如数据库文件、表、列或已知邻居
""",
}


def get_guardrail_guidance(spec=None) -> str:
    enabled = []
    if spec is not None:
        for guardrail in getattr(spec, "guardrails", []) or []:
            name = getattr(guardrail, "builder_name", None)
            if name and name not in enabled:
                enabled.append(name)

    if not enabled:
        return ""

    parts = ["## Guardrail 约束", ""]
    for name in enabled:
        section = _SECTIONS.get(name)
        if section:
            parts.append(section.strip())
            parts.append("")

    return "\n".join(parts).strip()
