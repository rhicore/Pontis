"""Prompt builder — 声明式分层组装 agent 提示词。

PROMPT_PROVIDERS: name → (spec) -> str 的查找表。
build_prompt: 根据 spec.prompts 列表组装（由 resolve_mode 填充）。
"""
from agent.prompt._base import _STATIC_PROMPT
from agent.prompt._namespace import get_namespace_prompt
from agent.prompt._entities import get_entities_prompt
from agent.prompt._effort import get_effort_prompt, VALID_EFFORTS
from agent.prompt._sql import get_sql_rules
from agent.prompt._guardrail import get_guardrail_guidance
from agent.prompt._readonly import get_readonly_additions
from agent.prompt._writer import get_writer_additions
from agent.prompt._sub_agent import get_sub_agent_additions
from agent.prompt._benchmark import get_benchmark_additions
from agent.prompt._reflection import get_reflection_prompt
from agent.prompt._project import build_project_context
from agent.prompt._debug import get_debug_additions

# ──────────────────────────────────────────────────────────
#  Prompt Provider 注册表
# ──────────────────────────────────────────────────────────

PROMPT_PROVIDERS = {
    "base":       lambda s: _STATIC_PROMPT,
    "namespace":  lambda s: get_namespace_prompt(),
    "entities":   lambda s: get_entities_prompt(),
    "effort":     lambda s: get_effort_prompt(s.effort),
    "sql":        lambda s: get_sql_rules(),
    "guardrail":  lambda s: get_guardrail_guidance(),
    "readonly":   lambda s: get_readonly_additions(),
    "writer":     lambda s: get_writer_additions(),
    "sub_agent":  lambda s: get_sub_agent_additions(),
    "benchmark":  lambda s: get_benchmark_additions(),
    "reflection": lambda s: get_reflection_prompt(),
    "project":    lambda s: build_project_context(s.project_path),
    "debug":      lambda s: get_debug_additions(),
}


def build_prompt(spec) -> str:
    """根据 AgentSpec.prompts 组装完整系统提示词。

    spec.prompts 由 resolve_mode() 填充（含条件追加的 effort/debug）。
    """
    if spec.effort not in VALID_EFFORTS:
        raise ValueError(f"Unknown effort {spec.effort!r}; expected one of {VALID_EFFORTS}")

    parts = []
    for name in spec.prompts:
        provider = PROMPT_PROVIDERS.get(name)
        if provider:
            parts.append(provider(spec))

    return "\n\n".join(parts)
