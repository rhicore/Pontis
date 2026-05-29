"""Runtime token accounting helpers for benchmark agents."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from token_cache_accounting import (  # noqa: E402
    merge_cache_accounting_sources,
    normalize_cache_accounting,
    serialize_request,
    split_static_prompt_tokens,
)


def estimate_tokens(value: Any) -> int:
    """Estimate model tokens for static prompt pieces.

    Runtime input/output totals still come from provider usage. This estimator is
    only used for the legacy static/dynamic split. Provider cache-hit/cache-miss
    fields, when available, are handled separately by token_cache_accounting.
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
    return split_static_prompt_tokens(total_prompt_tokens, static_prompt_tokens)
