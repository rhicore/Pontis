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
    """分两次调用 LLM 生成 detail 和 brief。

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
    brief_prompt = f"""Summarize the following text into exactly one short line, strictly under {MAX_BRIEF_CHARS} characters.
Be extremely concise — abbreviate, omit articles, compress aggressively.
Output ONLY the summary text, nothing else.

Text:
{detail}"""

    brief = ""
    try:
        raw = llm.complete(brief_prompt, max_tokens=80)
        if raw:
            brief = _clean(raw)
            # 硬保 brief 不超限（最后手段）
            if len(brief) > MAX_BRIEF_CHARS:
                brief = brief[:MAX_BRIEF_CHARS].rstrip(" .,;:;") + "..."
    except Exception as e:
        logger.debug(f"Brief generation failed: {e}")

    return detail, brief


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
