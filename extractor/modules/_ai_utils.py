"""AI Summary 共享工具 — 分两次调用 LLM 分别生成 detail 和 brief

所有 ai_*_summary 模块共用：
1. 第一次调用：生成 detail（不限字数，越详细越好）
2. 第二次调用：基于 detail 生成 brief（<= 50 字符）

彻底避免 LLM 把 detail/brief 搞混或 brief 过长的问题。
"""
import re
import logging

logger = logging.getLogger(__name__)

MAX_BRIEF_CHARS = 50


def generate_detail_and_brief(llm, prompt: str, max_tokens: int = 300) -> tuple:
    """分两次调用 LLM 生成 detail 和 brief（单 prompt 接口）。

    Args:
        llm: LLM 客户端
        prompt: 用户的分析 prompt（数据描述部分）
        max_tokens: detail 调用的 max_tokens

    Returns:
        (detail, brief)
    """
    # 第一次调用：生成 detail
    detail_prompt = prompt + """

Generate a comprehensive description. Be as detailed as possible — no word limit, no character limit.
Focus on semantics, purpose, and stable characteristics rather than exact counts.
Output ONLY the description text, no labels, no markdown formatting."""

    detail = ""
    try:
        raw = llm.complete(detail_prompt, max_tokens=max_tokens)
        if raw:
            detail = _clean(raw)
    except Exception as e:
        logger.debug(f"Detail generation failed: {e}")
        return "", ""

    if not detail:
        return "", ""

    # 第二次调用：基于 detail 生成 brief
    brief = _generate_brief(llm, detail)

    return detail, brief


def generate_with_prefix(llm, prefix_messages: list, max_tokens: int = 300) -> tuple:
    """分两次调用 LLM 生成 detail 和 brief（messages 接口，支持 prompt caching）。

    Args:
        llm: LLM 客户端（需要有 complete_messages 方法）
        prefix_messages: 共享前缀消息列表（system + table context）
        max_tokens: detail 调用的 max_tokens

    Returns:
        (detail, brief)
    """
    # 第一次调用：生成 detail
    detail_messages = prefix_messages + [{
        "role": "user",
        "content": "Generate a comprehensive description. Be as detailed as possible — "
                    "no word limit, no character limit. "
                    "Focus on semantics, purpose, and stable characteristics rather than exact counts. "
                    "Output ONLY the description text, no labels, no markdown formatting.",
    }]

    detail = ""
    try:
        raw = llm.complete_messages(detail_messages, max_tokens=max_tokens)
        if raw:
            detail = _clean(raw)
    except Exception as e:
        logger.debug(f"Detail generation failed: {e}")
        return "", ""

    if not detail:
        return "", ""

    # 第二次调用：基于 detail 生成 brief（复用前缀）
    brief = _generate_brief_from_messages(llm, prefix_messages, detail)

    return detail, brief


def _generate_brief(llm, detail: str) -> str:
    """基于 detail 生成 brief（单 prompt 接口）。"""
    brief_prompt = f"""Summarize the following text into exactly one short line, strictly under {MAX_BRIEF_CHARS} characters.
Be extremely concise — abbreviate, omit articles, compress aggressively.
Output ONLY the summary text, nothing else.

Text:
{detail}"""

    try:
        raw = llm.complete(brief_prompt, max_tokens=80)
        if raw:
            brief = _clean(raw)
            if len(brief) > MAX_BRIEF_CHARS:
                brief = brief[:MAX_BRIEF_CHARS].rstrip(" .,;:;") + "..."
            return brief
    except Exception as e:
        logger.debug(f"Brief generation failed: {e}")
    return ""


def _generate_brief_from_messages(llm, prefix_messages: list, detail: str) -> str:
    """基于 detail 生成 brief（messages 接口，复用缓存前缀）。"""
    messages = prefix_messages + [
        {"role": "assistant", "content": detail},
        {"role": "user", "content":
            f"Summarize the following text into exactly one short line, strictly under {MAX_BRIEF_CHARS} characters. "
            f"Be extremely concise — abbreviate, omit articles, compress aggressively. "
            f"Output ONLY the summary text, nothing else.\n\nText:\n{detail}"},
    ]

    try:
        raw = llm.complete_messages(messages, max_tokens=80)
        if raw:
            brief = _clean(raw)
            if len(brief) > MAX_BRIEF_CHARS:
                brief = brief[:MAX_BRIEF_CHARS].rstrip(" .,;:;") + "..."
            return brief
    except Exception as e:
        logger.debug(f"Brief generation failed: {e}")
    return ""


def _clean(text: str) -> str:
    """清理文本：去除 markdown 残留、多余空白。"""
    text = text.strip()
    # 去掉 markdown 前缀（**detail:**, **Detail:**, # Detail 等）
    text = re.sub(r'\*+\s*(detail|brief)\s*:?\s*\**\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^#+\s*(detail|brief)\s*\n*', '', text, flags=re.IGNORECASE)
    # 去掉开头的 label
    text = re.sub(r'^(detail|brief)\s*:\s*', '', text, flags=re.IGNORECASE)
    # 去掉 markdown 加粗
    text = re.sub(r'\*+([^*]+)\*+', r'\1', text)
    # 去掉开头 #
    text = re.sub(r'^#+\s*', '', text)
    # 去掉可能的引号包裹
    if (text.startswith('"') and text.endswith('"')) or \
       (text.startswith("'") and text.endswith("'")):
        text = text[1:-1]
    return text.strip()
