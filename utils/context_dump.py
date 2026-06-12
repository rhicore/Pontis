"""Dump exact LLM request contexts for benchmark debugging."""

from __future__ import annotations

import contextvars
import json
import os
import threading
import time
from pathlib import Path
from typing import Any
import re


_context_meta: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "pontis_context_meta",
    default={},
)
_lock = threading.Lock()
_DUMP_TIMESTAMP = time.strftime("%Y%m%d_%H%M%S")
_TIMESTAMP_PREFIX_RE = re.compile(r"^\d{8}_\d{6}(?:_|$)")


def set_context_dump_meta(**meta: Any):
    """Set per-task metadata used in dumped request filenames."""
    return _context_meta.set({k: v for k, v in meta.items() if v is not None})


def reset_context_dump_meta(token) -> None:
    _context_meta.reset(token)


def _default_context_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "workspace" / "baselines" / "pontis" / "context"


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(exclude_none=True))
    if hasattr(value, "to_dict"):
        return _jsonable(value.to_dict())
    return repr(value)


def dump_llm_context(agent_name: str, request: dict[str, Any]) -> None:
    """Write the latest full LLM request context for this case and agent."""
    enabled = os.environ.get("PONTIS_CONTEXT_DUMP", "1").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return

    base = Path(os.environ.get("PONTIS_CONTEXT_DIR") or _default_context_dir())
    meta = dict(_context_meta.get() or {})
    run_id = str(meta.get("run_id") or os.environ.get("PONTIS_CONTEXT_RUN_ID") or "manual")
    if not _TIMESTAMP_PREFIX_RE.match(run_id):
        run_id = f"{_DUMP_TIMESTAMP}_{run_id}"
    db_id = str(meta.get("db_id") or "unknown_db")
    qid = str(meta["question_id"]) if "question_id" in meta else "unknown_q"

    target_dir = base / run_id / db_id
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_agent_name = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in agent_name)
    path = target_dir / f"q{qid}_{safe_agent_name}_context.json"

    payload = {
        "agent": agent_name,
        "meta": meta,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "request": _jsonable(request),
    }
    with _lock:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
