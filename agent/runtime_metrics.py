"""Runtime token accounting helpers for benchmark agents."""

from __future__ import annotations

import json
from typing import Any


def estimate_tokens(value: Any) -> int:
    """Estimate model tokens for static prompt pieces.

    Runtime input/output totals still come from provider usage. This estimator is
    only used to split prompt tokens into cacheable pre-input and dynamic runtime
    input when the provider does not expose cache-hit token details.
    """
    if value is None:
        return 0
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if not value:
        return 0
    try:
        import tiktoken  # type: ignore

        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(value))
    except Exception:
        # Conservative cross-language approximation. Chinese text is often
        # closer to one char per token, ASCII prose is closer to four chars.
        non_ascii = sum(1 for ch in value if ord(ch) > 127)
        ascii_chars = len(value) - non_ascii
        return max(1, non_ascii + (ascii_chars + 3) // 4)


def estimate_messages_tokens(messages: list[dict]) -> int:
    total = 0
    for message in messages:
        total += estimate_tokens(message.get("role", ""))
        total += estimate_tokens(message.get("content", "") or "")
        if message.get("tool_calls"):
            total += estimate_tokens(message.get("tool_calls"))
        if message.get("name"):
            total += estimate_tokens(message.get("name"))
        total += 4
    return total


def split_prompt_tokens(total_prompt_tokens: int, static_prompt_tokens: int) -> dict[str, int]:
    pre_input_tokens = min(max(0, int(total_prompt_tokens or 0)), max(0, int(static_prompt_tokens or 0)))
    runtime_input_tokens = max(0, int(total_prompt_tokens or 0) - pre_input_tokens)
    return {
        "pre_input_tokens": pre_input_tokens,
        "runtime_input_tokens": runtime_input_tokens,
    }
