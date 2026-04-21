"""Prompt builder — 分层组合 agent 提示词。

三层架构：
  静态层 (_base)            — 所有模式共享的 Pontis 概念
  模式层 (readonly/writer/sub_agent) — 各模式的专属指令
  动态层 (project)          — 运行时项目上下文
"""
from agent.prompt._base import _STATIC_PROMPT
from agent.prompt._readonly import get_readonly_additions
from agent.prompt._writer import get_writer_additions
from agent.prompt._sub_agent import get_sub_agent_additions
from agent.prompt._project import build_project_context

_VALID_MODES = ("readonly", "writer", "sub_agent")


def build_prompt(mode: str, project_path: str, debug: bool = False) -> str:
    """组合完整的系统提示词。

    Args:
        mode: "readonly" | "writer" | "sub_agent"
        project_path: 项目目录绝对路径
        debug: 是否注入调试模式提示词
    """
    if mode not in _VALID_MODES:
        raise ValueError(f"Unknown mode {mode!r}; expected one of {_VALID_MODES}")

    parts = [_STATIC_PROMPT]

    if mode == "readonly":
        parts.append(get_readonly_additions())
        parts.append(build_project_context(project_path))
    elif mode == "writer":
        parts.append(get_writer_additions())
        parts.append(build_project_context(project_path))
    elif mode == "sub_agent":
        # writer 全部能力 + 子智能体行为约束，不加动态层
        parts.append(get_writer_additions())
        parts.append(get_sub_agent_additions())

    if debug:
        from agent.prompt._debug import get_debug_additions
        parts.append(get_debug_additions())

    return "\n\n".join(parts)
