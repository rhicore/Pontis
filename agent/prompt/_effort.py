"""Effort 层 — 探索强度动态提示词。

控制 agent 对数据库的探索深度和工具调用轮次。
"""

EFFORT_PROMPTS = {
    "low": (
        "## 探索策略：快速模式\n"
        "你的工具调用轮次有限（30 轮）。聚焦问题核心，快速定位关键信息，"
        "避免广泛浏览。如果已有线索指向明确答案，直接输出，不要反复验证。"
    ),
    "mid": (
        "## 探索策略：标准模式\n"
        "你有适中的工具调用轮次（50 轮）。先理解问题再定向查询，"
        "注意跨表关系和数据一致性。如果信息已足够回答，及时输出。"
    ),
    "high": (
        "## 探索策略：深度模式\n"
        "你有充足的工具调用轮次（80 轮）。可以全面探索数据库结构，"
        "深入理解表间关系和业务逻辑，发现隐含关联。"
    ),
}

EFFORT_MAX_ROUNDS = {"low": 30, "mid": 50, "high": 80}

VALID_EFFORTS = tuple(EFFORT_PROMPTS.keys())


def get_effort_prompt(effort: str) -> str:
    if effort not in EFFORT_PROMPTS:
        raise ValueError(f"Unknown effort {effort!r}; expected one of {VALID_EFFORTS}")
    return EFFORT_PROMPTS[effort]


def get_effort_max_rounds(effort: str) -> int:
    if effort not in EFFORT_MAX_ROUNDS:
        raise ValueError(f"Unknown effort {effort!r}; expected one of {VALID_EFFORTS}")
    return EFFORT_MAX_ROUNDS[effort]
