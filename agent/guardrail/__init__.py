"""Guardrail 脚本注册。

API 类型定义在 agent.guardrail_api，此处仅 re-export + 构建。
"""
from agent.guardrail_api import CallVerdict, GuardrailContext, Guardrail  # noqa: F401


def build_guardrails(spec) -> list:
    """根据 AgentSpec 构建 guardrail 列表。"""
    from agent.guardrail.round_limit import RoundLimit
    from agent.guardrail.tool_abuse import ToolAbuse
    from agent.guardrail.sql_check import SQLEntityCheck
    from agent.guardrail.sql_join_check import BridgeTableCheck
    from agent.guardrail.sql_disambig_check import SQLDisambigCheck

    guardrails = []

    from agent.prompt._effort import get_effort_max_rounds
    if spec.mode == "writer":
        max_rounds = spec.max_rounds
    else:
        max_rounds = spec.max_rounds or get_effort_max_rounds(spec.effort)
    if max_rounds:
        guardrails.append(RoundLimit(max_rounds))

    guardrails.append(ToolAbuse("query", total_limit=5, consecutive_limit=3))
    guardrails.append(SQLEntityCheck())
    guardrails.append(BridgeTableCheck())
    guardrails.append(SQLDisambigCheck())

    return guardrails
