"""Agent 配置 — AgentSpec、create_agent 工厂和默认 agent 配置。"""
from dataclasses import dataclass, field
from typing import List, Optional

from agent.tools import build_registry
from agent.prompt import build_prompt_messages
from agent.guardrail_api import Guardrail
from agent.guardrail import build_guardrails

DEFAULT_READONLY_TOOLS = ["find", "grep", "read", "jd", "meta", "bash", "query", "agent"]
DEFAULT_READONLY_PROMPTS = [
    "base", "tool", "ontology", "sql",
    "guardrail", "project", "readme",
]
DEFAULT_READONLY_GUARDRAILS = [
    "round_limit", "exploration_check", "query_abuse",
    "sql_check", "bridge_check", "disambig_check", "meta_disambig_prefetch",
]


@dataclass
class AgentSpec:
    """Agent 创建的完整参数包。

    tools/prompts/guardrails 都由创建方显式设置；这里不再通过 mode 推导。
    """
    project_path: str = ""
    projects: List[str] = field(default_factory=list)  # 本次开启的项目名列表，空则只开启 project_path
    effort: str = "mid"
    max_rounds: Optional[int] = None

    tools: List[str] = field(default_factory=list)
    prompts: List[str] = field(default_factory=list)
    guardrails: List[Guardrail] = field(default_factory=list)


def create_agent(project_path: str, spec: AgentSpec = None,
                 logger_name: Optional[str] = None,
                 trace_callback=None) -> "PontusAgent":
    """工厂：根据 spec 自动组装 prompt + tools + guardrails。"""
    # 延迟导入避免循环
    from agent.agent import PontusAgent

    if spec is None:
        spec = default_spec(project_path)
    spec.project_path = project_path
    _validate_explicit_spec(spec)

    prompt = build_prompt_messages(spec)
    tools = build_registry(spec)

    return PontusAgent(project_path, tools=tools, system_prompt=prompt,
                       guardrails=spec.guardrails, logger_name=logger_name,
                       trace_callback=trace_callback,
                       active_projects=spec.projects or None)


def default_spec(project_path: str) -> AgentSpec:
    """创建默认 readonly spec。"""
    spec = AgentSpec(
        project_path=project_path,
        tools=list(DEFAULT_READONLY_TOOLS),
        prompts=list(DEFAULT_READONLY_PROMPTS),
    )
    spec.guardrails = build_guardrails(spec, DEFAULT_READONLY_GUARDRAILS)
    return spec


def _validate_explicit_spec(spec: AgentSpec) -> None:
    if not spec.tools:
        raise ValueError("AgentSpec.tools must be set explicitly")
    if not spec.prompts:
        raise ValueError("AgentSpec.prompts must be set explicitly")
