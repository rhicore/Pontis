"""Agent 配置 — ModeConfig 预设、AgentSpec、resolve_mode、create_agent 工厂。"""
import logging
import sys
from dataclasses import dataclass, field
from typing import List, Optional

from agent.utils import load_agent_config
from agent.tools import build_registry
from agent.prompt import build_prompt
from agent.guardrail_api import Guardrail
from agent.guardrail import build_guardrails

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
#  Mode 预设 + AgentSpec
# ═══════════════════════════════════════════════════════════

@dataclass
class ModeConfig:
    """mode 解析结果：一组默认参数。"""
    tools: List[str]
    prompts: List[str]
    guardrail_builders: List[str]


_MODE_PRESETS = {
    "readonly": ModeConfig(
        tools=["glob", "grep", "meta", "search", "bash", "query", "agent"],
        prompts=["base", "tool", "ontology", "meta", "sql", "guardrail", "readonly", "project"],
        guardrail_builders=["round_limit", "query_abuse", "sql_check", "bridge_check", "disambig_check"],
    ),
    "writer": ModeConfig(
        tools=["glob", "grep", "meta", "search", "bash", "query", "agent",
               "create_entity", "update_meta", "add_edge", "delete"],
        prompts=["base", "tool", "ontology", "meta", "sql", "guardrail", "writer", "project"],
        guardrail_builders=["round_limit", "query_abuse", "sql_check", "bridge_check", "disambig_check"],
    ),
    "sub_agent": ModeConfig(
        tools=["glob", "grep", "meta", "search", "bash", "query",
               "create_entity", "update_meta", "add_edge", "delete"],
        prompts=["base", "tool", "ontology", "meta", "sql", "guardrail", "writer", "sub_agent"],
        guardrail_builders=["round_limit", "query_abuse", "sql_check", "bridge_check", "disambig_check"],
    ),
    "benchmark": ModeConfig(
        tools=["glob", "grep", "meta", "search", "bash", "query"],
        prompts=["base", "tool", "ontology", "meta", "sql", "guardrail", "benchmark", "project"],
        guardrail_builders=["round_limit", "query_abuse", "sql_check", "bridge_check", "disambig_check"],
    ),
    "reflection": ModeConfig(
        tools=["glob", "grep", "meta", "search", "bash", "query",
               "create_entity", "update_meta", "add_edge", "delete"],
        prompts=["base", "tool", "ontology", "meta", "reflection"],
        guardrail_builders=["round_limit"],
    ),
}


@dataclass
class AgentSpec:
    """Agent 创建的完整参数包。mode 决定初始值，用户可覆盖各字段。"""
    project_path: str = ""
    projects: List[str] = field(default_factory=list)  # 本次开启的项目名列表，空则只开启 project_path
    mode: str = "readonly"
    effort: str = "mid"
    max_rounds: Optional[int] = None

    # mode 决定初始值，用户可覆盖
    tools: List[str] = field(default_factory=list)
    prompts: List[str] = field(default_factory=list)
    guardrails: List[Guardrail] = field(default_factory=list)


def resolve_mode(spec: AgentSpec) -> None:
    """用 mode 预设填充 spec 中未设置的字段（原地修改）。"""
    preset = _MODE_PRESETS.get(spec.mode)
    if preset is None:
        raise ValueError(f"Unknown mode {spec.mode!r}; expected one of {list(_MODE_PRESETS.keys())}")

    if not spec.tools:
        spec.tools = list(preset.tools)
    if not spec.prompts:
        spec.prompts = list(preset.prompts)
    if not spec.guardrails:
        spec.guardrails = build_guardrails(spec, preset.guardrail_builders)

    # 条件追加
    if spec.effort != "mid" and "effort" not in spec.prompts:
        spec.prompts.append("effort")


def create_agent(project_path: str, spec: AgentSpec = None,
                 logger_name: Optional[str] = None,
                 trace_callback=None) -> "PontusAgent":
    """工厂：根据 spec 自动组装 prompt + tools + guardrails。"""
    # 延迟导入避免循环
    from agent.agent import PontusAgent

    if spec is None:
        spec = AgentSpec()
    spec.project_path = project_path
    resolve_mode(spec)

    prompt = build_prompt(spec)
    tools = build_registry(spec)

    return PontusAgent(project_path, tools=tools, system_prompt=prompt,
                       guardrails=spec.guardrails, logger_name=logger_name,
                       trace_callback=trace_callback,
                       active_projects=spec.projects or None)


def default_spec(project_path: str) -> AgentSpec:
    """创建默认 spec 并 resolve。"""
    spec = AgentSpec(project_path=project_path)
    resolve_mode(spec)
    return spec
