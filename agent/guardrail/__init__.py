"""Guardrail 脚手架注册。

API 类型定义在 agent.guardrail_api，此处仅 re-export + 构建。
"""
from agent.guardrail_api import CallVerdict, GuardrailContext, Guardrail, PostToolAction  # noqa: F401


def build_guardrails(spec, builder_names: list = None) -> list:
    """根据 builder 名称列表构建 guardrail 列表。"""
    from agent.guardrail.round_limit import RoundLimit
    from agent.guardrail.tool_abuse import ToolAbuse
    from agent.guardrail.exploration_check import ExplorationCheck
    from agent.guardrail.sql_check import SQLEntityCheck
    from agent.guardrail.sql_final_validity_check import FinalSQLValidityCheck
    from agent.guardrail.sql_join_check import BridgeTableCheck
    from agent.guardrail.sql_disambig_check import SQLDisambigCheck
    from agent.guardrail.meta_disambig_prefetch import MetaDisambigPrefetch
    from agent.guardrail.sql_value_grounding_check import SQLValueGroundingCheck
    from agent.guardrail.bird_multi_report_controller import BirdSchemaChallengeController
    from agent.guardrail.bird_readme_final_recheck import BirdReadmeFinalRecheck
    from agent.prompt._effort import get_effort_max_rounds

    _builders = {
        "round_limit": lambda: RoundLimit(
            spec.max_rounds or get_effort_max_rounds(spec.effort)
        ),
        "query_abuse": lambda: ToolAbuse("query", total_limit=5, consecutive_limit=3),
        "exploration_check": lambda: ExplorationCheck(),
        "sql_check": lambda: SQLEntityCheck(),
        "final_sql_validity_check": lambda: FinalSQLValidityCheck(),
        "bridge_check": lambda: BridgeTableCheck(),
        "disambig_check": lambda: SQLDisambigCheck(),
        "meta_disambig_prefetch": lambda: MetaDisambigPrefetch(),
        "value_grounding_check": lambda: SQLValueGroundingCheck(),
        "bird_schema_challenge_controller": lambda: BirdSchemaChallengeController(
            getattr(spec, "bird_report_count", 3)
        ),
        # Backward-compatible alias for older benchmark commands.
        "bird_multi_report_controller": lambda: BirdSchemaChallengeController(
            getattr(spec, "bird_report_count", 3)
        ),
        "bird_readme_final_recheck": lambda: BirdReadmeFinalRecheck(),
    }

    guardrails = []
    for name in (builder_names or []):
        builder = _builders.get(name)
        if builder:
            guardrail = builder()
            setattr(guardrail, "builder_name", name)
            guardrails.append(guardrail)
    return guardrails
