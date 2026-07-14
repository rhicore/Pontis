"""Explorer-specific agent configuration helpers.

Explorer agents are preprocessing writers, not benchmark SQL solvers. They use
explicit prompts/tools/guardrails instead of the generic default agent config.
"""
from __future__ import annotations

from typing import Iterable

from agent.config import AgentSpec
from agent.guardrail import build_guardrails
from storage.workspace import Workspace


_DATABASE_SOURCE_TYPES = {
    "sqlite",
    "postgresql",
    "postgres",
    "snowflake",
    "spider2_snow",
}


def _explorer_prompts(workspace: Workspace) -> list[str]:
    source_types = {
        str(workspace.config.projects[name].source.type or "").lower()
        for name in workspace.active_projects
        if name in workspace.config.projects
    }
    ontology = (
        "database_ontology"
        if source_types and source_types.issubset(_DATABASE_SOURCE_TYPES)
        else "ontology"
    )
    return ["base", "tool", ontology, "project"]


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
    projects = list(workspace.active_projects)
    if not projects:
        raise ValueError("Explorer workspace must have at least one active project")

    tool_list = list(tools)
    prompts = _explorer_prompts(workspace)
    if include_readme:
        prompts.append("readme")

    spec = AgentSpec(
        projects=projects,
        effort=effort,
        max_rounds=max_rounds,
        tools=tool_list,
        prompts=prompts,
        query_mode=query_mode,
    )
    spec.guardrails = build_guardrails(spec, ["round_limit"]) if max_rounds else []
    return spec
