"""Explorer-specific agent configuration helpers.

Explorer agents are preprocessing writers, not benchmark SQL solvers. They use
explicit prompts/tools/guardrails instead of the generic default agent config.
"""
from __future__ import annotations

import os
from typing import Iterable

from agent.config import AgentSpec
from agent.guardrail import build_guardrails
from storage.workspace import Workspace


EXPLORER_BASE_PROMPTS = [
    "base",
    "tool",
    "ontology",
    "project",
]


def explorer_writer_spec(
    workspace: Workspace,
    *,
    tools: Iterable[str],
    effort: str = "max",
    max_rounds: int | None = None,
    include_readme: bool = False,
    query_mode: str = "",
) -> AgentSpec:
    """Build an explicit writer spec for explorer preprocessing agents."""
    tool_list = list(tools)
    prompts = list(EXPLORER_BASE_PROMPTS)
    if include_readme:
        prompts.append("readme")

    spec = AgentSpec(
        effort=effort,
        max_rounds=max_rounds,
        tools=tool_list,
        prompts=prompts,
        query_mode=query_mode,
    )
    project_name = os.path.basename(os.path.abspath(workspace.project_path))
    spec.projects = [project_name]
    spec.guardrails = build_guardrails(spec, ["round_limit"]) if max_rounds else []
    return spec
