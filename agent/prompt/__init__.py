"""Prompt builder — 声明式分层组装 agent 提示词。

每层定义为 (name, condition, provider) 三元组：
  - name: 层名（用于调试）
  - condition: (AgentSpec) -> bool，决定是否包含
  - provider:   (AgentSpec) -> str，返回提示词文本

新增提示词层只需在 PROMPT_LAYERS 末尾加一行即可。
"""
from agent.prompt._base import _STATIC_PROMPT
from agent.prompt._entities import get_entities_prompt
from agent.prompt._effort import get_effort_prompt, VALID_EFFORTS
from agent.prompt._sql import get_sql_rules
from agent.prompt._readonly import get_readonly_additions
from agent.prompt._writer import get_writer_additions
from agent.prompt._sub_agent import get_sub_agent_additions
from agent.prompt._benchmark import get_benchmark_additions
from agent.prompt._project import build_project_context
from agent.prompt._debug import get_debug_additions

_VALID_MODES = ("readonly", "writer", "sub_agent", "benchmark")
_MODES_WITH_SQL = {"readonly", "writer", "benchmark"}
_MODES_WITH_PROJECT = {"readonly", "writer", "benchmark"}

# ──────────────────────────────────────────────────────────
#  提示词层注册表
# ──────────────────────────────────────────────────────────
# 格式: (name, condition, provider)
# condition 接收 AgentSpec，返回 bool
# provider  接收 AgentSpec，返回 str

PROMPT_LAYERS = [
    # ── 基础层（所有模式共享）──
    ("base",     lambda s: True,
                 lambda s: _STATIC_PROMPT),

    ("entities", lambda s: True,
                 lambda s: get_entities_prompt()),

    # ── 策略层 ──
    ("effort",   lambda s: s.effort != "mid",
                 lambda s: get_effort_prompt(s.effort)),

    ("sql",      lambda s: s.mode in _MODES_WITH_SQL,
                 lambda s: get_sql_rules()),

    # ── 模式层 ──
    ("readonly", lambda s: s.mode == "readonly",
                 lambda s: get_readonly_additions()),

    ("writer",   lambda s: s.mode in ("writer", "sub_agent"),
                 lambda s: get_writer_additions()),

    ("sub_agent",lambda s: s.mode == "sub_agent",
                 lambda s: get_sub_agent_additions()),

    ("benchmark",lambda s: s.mode == "benchmark",
                 lambda s: get_benchmark_additions()),

    # ── 动态层 ──
    ("project",  lambda s: s.mode in _MODES_WITH_PROJECT,
                 lambda s: build_project_context(s.project_path)),

    # ── 调试层 ──
    ("debug",    lambda s: s.debug,
                 lambda s: get_debug_additions()),
]


def build_prompt(spec) -> str:
    """根据 AgentSpec 组装完整系统提示词。

    遍历 PROMPT_LAYERS，对 condition(spec) 为 True 的层调用 provider(spec)，
    用双换行拼接返回。
    """
    if spec.mode not in _VALID_MODES:
        raise ValueError(f"Unknown mode {spec.mode!r}; expected one of {_VALID_MODES}")
    if spec.effort not in VALID_EFFORTS:
        raise ValueError(f"Unknown effort {spec.effort!r}; expected one of {VALID_EFFORTS}")

    parts = [
        provider(spec)
        for _, cond, provider in PROMPT_LAYERS
        if cond(spec)
    ]
    return "\n\n".join(parts)
