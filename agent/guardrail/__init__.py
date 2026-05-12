"""Guardrail 脚手架注册。

API 类型定义在 agent.guardrail_api，此处仅 re-export + 构建。
"""
from agent.guardrail_api import CallVerdict, GuardrailContext, Guardrail  # noqa: F401


def build_guardrails(spec, builder_names: list = None) -> list:
    """根据 builder 名称列表构建 guardrail 列表。"""
    from agent.guardrail.round_limit import RoundLimit
    from agent.guardrail.tool_abuse import ToolAbuse
    from agent.guardrail.exploration_check import ExplorationCheck
    from agent.guardrail.sql_check import SQLEntityCheck
    from agent.guardrail.sql_join_check import BridgeTableCheck
    from agent.guardrail.sql_disambig_check import SQLDisambigCheck
    from agent.prompt._effort import get_effort_max_rounds

    _builders = {
        "round_limit": lambda: RoundLimit(
            spec.max_rounds or get_effort_max_rounds(spec.effort)
        ),
        "query_abuse": lambda: ToolAbuse("query", total_limit=5, consecutive_limit=3),
        "exploration_check": lambda: ExplorationCheck(),
        "sql_check": lambda: SQLEntityCheck(),
        "bridge_check": lambda: BridgeTableCheck(),
        "disambig_check": lambda: SQLDisambigCheck(),
    }

    guardrails = []
    for name in (builder_names or []):
        builder = _builders.get(name)
        if builder:
            guardrail = builder()
            setattr(guardrail, "builder_name", name)
            guardrails.append(guardrail)
    return guardrails
