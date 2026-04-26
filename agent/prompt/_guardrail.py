"""Guardrail 策略指导 — 工具使用限制与 guardrail 机制说明。"""

_GUARDRAIL_GUIDANCE = r"""## 工具使用限制

- query 工具总共最多调用 **5 次**，超出后会被强制终止
- 连续 3 次调用 query 会触发提醒，建议回顾已有信息
- guardrail 会检查你的工具调用：query 时如发现未读取的实体会**提醒**你，最终输出 SQL 时未读取的实体会被**拦截**——必须先 meta 读取才能输出
"""

def get_guardrail_guidance() -> str:
    return _GUARDRAIL_GUIDANCE
