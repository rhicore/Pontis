"""Thread-safe token accounting for preprocessing modules."""

from __future__ import annotations

import threading
from collections import Counter
from contextlib import contextmanager
from typing import Iterator


_LOCAL = threading.local()


def _current() -> Counter | None:
    return getattr(_LOCAL, "counter", None)


@contextmanager
def token_counter() -> Iterator[Counter]:
    previous = _current()
    counter: Counter = Counter()
    _LOCAL.counter = counter
    try:
        yield counter
    finally:
        if previous is None:
            try:
                delattr(_LOCAL, "counter")
            except AttributeError:
                pass
        else:
            _LOCAL.counter = previous


def snapshot() -> dict:
    counter = _current()
    return dict(counter or {})


def add_usage(prefix: str, *, input_tokens: int = 0, output_tokens: int = 0,
              total_tokens: int | None = None, calls: int = 1,
              cached_input_tokens: int = 0, uncached_input_tokens: int = 0,
              cache_hit_input_tokens: int = 0, cache_miss_input_tokens: int = 0,
              cache_unknown_input_tokens: int = 0, fresh_input_tokens: int = 0) -> None:
    counter = _current()
    if counter is None:
        return
    input_tokens = int(input_tokens or 0)
    output_tokens = int(output_tokens or 0)
    if total_tokens is None:
        total_tokens = input_tokens + output_tokens
    total_tokens = int(total_tokens or 0)

    counter[f"{prefix}_calls"] += int(calls or 0)
    counter[f"{prefix}_input_tokens"] += input_tokens
    counter[f"{prefix}_output_tokens"] += output_tokens
    counter[f"{prefix}_total_tokens"] += total_tokens
    counter[f"{prefix}_cached_input_tokens"] += int(cached_input_tokens or cache_hit_input_tokens or 0)
    counter[f"{prefix}_uncached_input_tokens"] += int(uncached_input_tokens or 0)
    counter[f"{prefix}_cache_hit_input_tokens"] += int(cache_hit_input_tokens or cached_input_tokens or 0)
    counter[f"{prefix}_cache_miss_input_tokens"] += int(cache_miss_input_tokens or 0)
    counter[f"{prefix}_cache_unknown_input_tokens"] += int(cache_unknown_input_tokens or 0)
    counter[f"{prefix}_fresh_input_tokens"] += int(fresh_input_tokens or 0)
