"""Guardrail 提示词 — 根据当前实际启用的 guardrail 动态生成。"""

from __future__ import annotations


_SECTIONS = {
    "readme_check": """## README 约束

- 如果当前打开了多个项目，只要其中还有项目的 `README` 未读完，就不要做任何其他操作
- 先把所有相关项目的 `README` 读完，再去读知识节点、schema 节点、跑 query 或做其他探索
- 不要求固定顺序；多个项目里，先读哪个 `README` 都可以
- 读取 README 时不要先 `glob("<project>::README")` 试探；推荐直接用 `meta({"ref": "<project>::README", "property": ["detail"]})` 全量读取正文
- README 不存在时，这条约束不生效
- 一般用 `meta({"ref": "<project>::README", "property": ["detail"]})` 先读，`detail` 就是 README 正文
- 未先读 README 前，不要访问该项目下的其他实体，也不要在该项目上做 search / create / update / delete
""",
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
    "exploration_check": """## 探索纪律

- 不要用 `glob("*")` 做起手式全图枚举
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
