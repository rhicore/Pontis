"""Prompt builder — 分层组合 agent 提示词。

分层架构：
  静态层 (_base)            — 全局概念和工具策略
  实体层 (_entities)        — 实体命名规则和元数据字段
  Effort 层 (_effort)       — 探索强度策略
  SQL 层 (_sql)             — SQL 生成规范（所有模式共享）
  模式层 (readonly/writer/sub_agent/benchmark) — 各模式的专属指令
  动态层 (project)          — 运行时项目上下文
"""
from agent.prompt._base import _STATIC_PROMPT
from agent.prompt._entities import get_entities_prompt
from agent.prompt._effort import get_effort_prompt, get_effort_max_rounds, VALID_EFFORTS
from agent.prompt._sql import get_sql_rules
from agent.prompt._readonly import get_readonly_additions
from agent.prompt._writer import get_writer_additions
from agent.prompt._sub_agent import get_sub_agent_additions
from agent.prompt._benchmark import get_benchmark_additions
from agent.prompt._project import build_project_context

_VALID_MODES = ("readonly", "writer", "sub_agent", "benchmark")


def build_prompt(mode: str, project_path: str, effort: str = "mid",
                 debug: bool = False) -> str:
    """组合完整的系统提示词。

    Args:
        mode: "readonly" | "writer" | "sub_agent" | "benchmark"
        project_path: 项目目录绝对路径
        effort: "low" | "mid" | "high" — 探索强度，默认 "mid"
        debug: 是否注入调试模式提示词
    """
    if mode not in _VALID_MODES:
        raise ValueError(f"Unknown mode {mode!r}; expected one of {_VALID_MODES}")
    if effort not in VALID_EFFORTS:
        raise ValueError(f"Unknown effort {effort!r}; expected one of {VALID_EFFORTS}")

    parts = [_STATIC_PROMPT, get_entities_prompt()]

    # Effort 层：非默认档注入探索策略提示词
    if effort != "mid":
        parts.append(get_effort_prompt(effort))

    if mode == "readonly":
        parts.append(get_readonly_additions())
        parts.append(get_sql_rules())
        parts.append(build_project_context(project_path))
    elif mode == "writer":
        parts.append(get_writer_additions())
        parts.append(get_sql_rules())
        parts.append(build_project_context(project_path))
    elif mode == "sub_agent":
        # writer 全部能力 + 子智能体行为约束，不加动态层
        parts.append(get_writer_additions())
        parts.append(get_sub_agent_additions())
    elif mode == "benchmark":
        # readonly + SQL 规范 + benchmark 专用指令
        parts.append(get_readonly_additions())
        parts.append(get_sql_rules())
        parts.append(get_benchmark_additions())
        parts.append(build_project_context(project_path))

    if debug:
        from agent.prompt._debug import get_debug_additions
        parts.append(get_debug_additions())

    return "\n\n".join(parts)
