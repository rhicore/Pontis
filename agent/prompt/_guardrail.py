"""Guardrail 提示词 — 根据当前实际启用的 guardrail 动态生成。"""

from __future__ import annotations


_SECTIONS = {
    "query_abuse": "- `query_abuse`: query 调用有预算；连续重复查询会提醒收敛。",
    "sql_check": "- `sql_check`: SQL 引用关键表/列/关系前应已通过工具确认。",
    "final_sql_validity_check": "- `final_sql_validity_check`: 最终回复必须只包含一个可解析、只读、可在当前 schema 编译的 SQLite SQL 代码块。",
    "bridge_check": "- `bridge_check`: JOIN 需要图谱中的 `fk` / `rel` / `overlap` 支撑。",
    "disambig_check": "- `disambig_check`: 同名或近义实体需要读取相关消歧信息。",
    "meta_disambig_prefetch": "- `meta_disambig_prefetch`: 读取带相邻消歧或 hint 的 meta 后，可能收到后台 sidechain 返回的上下文辨析；应结合当前证据重新判断表、列、值、JOIN 和聚合选择。",
    "value_grounding_check": "- `value_grounding_check`: 最终 SQL 的过滤值会验证存在性、LIKE 精度和枚举列匹配。",
    "exploration_check": "- `exploration_check`: 优先从 DB/table/col/known neighbor 定向探索。",
    "rule_review": "- `rule_review`: 最终 SQL 会由独立 reviewer 按当前任务注入的 rule context 复审。",
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

    parts = ["## Guardrail 约束"]
    for name in enabled:
        section = _SECTIONS.get(name)
        if section:
            parts.append(section)

    return "\n".join(parts).strip()
