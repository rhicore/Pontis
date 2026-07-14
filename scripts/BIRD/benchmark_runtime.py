"""Runtime helpers for the BIRD benchmark runner.

This module contains side-effect-light helpers used by
``scripts.BIRD.run_bird_benchmark``: SQL execution/comparison, trace logging,
token efficiency aggregation, and progress tracking.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from scripts.BIRD.result_match import ExecutionResult


DB_EXTS = (".sqlite", ".db", ".sqlite3", ".duckdb")

_SQL_BLOCK_RE = re.compile(r"```sql\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
_SELECT_RE = re.compile(r"(SELECT\s.+?)(?:;|$)", re.DOTALL | re.IGNORECASE)


def extract_sql(text: str) -> str | None:
    if not text:
        return None
    blocks = _SQL_BLOCK_RE.findall(text)
    if blocks:
        sql = blocks[-1].strip()
        if sql:
            return sql
    matches = _SELECT_RE.findall(text)
    if matches:
        return matches[-1].strip()
    return None


def execute_sql(db_path: str, sql: str) -> ExecutionResult | str:
    timeout_sec = float(os.environ.get("PONTIS_BIRD_SQL_TIMEOUT_SEC", "30") or 30)
    first = _execute_sql_once(db_path, sql, timeout_sec)
    if not _is_sql_timeout(first):
        return first

    optimized_sql = _reorder_independent_aggregate_join(sql)
    if optimized_sql and _normalize_sql_text(optimized_sql) != _normalize_sql_text(sql):
        retry = _execute_sql_once(db_path, optimized_sql, timeout_sec)
        if not _is_sql_timeout(retry):
            return retry
    return first


def _execute_sql_once(db_path: str, sql: str, timeout_sec: float) -> ExecutionResult | str:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        if timeout_sec > 0:
            start = time.time()

            def progress() -> int:
                return 1 if time.time() - start > timeout_sec else 0

            conn.set_progress_handler(progress, 10000)
        cursor = conn.execute(sql)
        rows = tuple(tuple(r) for r in cursor.fetchall())
        columns = tuple(item[0] for item in cursor.description or ())
        conn.close()
        return ExecutionResult(columns=columns, rows=rows)
    except Exception as e:
        return f"ERROR: {e}"


def _is_sql_timeout(result: ExecutionResult | str) -> bool:
    return isinstance(result, str) and "interrupted" in result.lower()


def _normalize_sql_text(sql: str) -> str:
    return re.sub(r"\s+", " ", (sql or "").strip()).lower()


_JOIN_AGG_SUBQUERY_RE = re.compile(
    r"""
    (?P<prefix>\bFROM\s+)
    (?P<src1>[A-Za-z_][\w."]*(?:\s+(?:AS\s+)?(?P<a1>[A-Za-z_][\w]*))?)
    \s+INNER\s+JOIN\s+
    (?P<src2>[A-Za-z_][\w."]*(?:\s+(?:AS\s+)?(?P<a2>[A-Za-z_][\w]*))?)
    \s+ON\s+(?P<on12>.*?)
    \s+INNER\s+JOIN\s+
    \(\s*(?P<subquery>SELECT\s+[^()]*?\b(?:MAX|MIN|SUM|AVG|COUNT)\s*\([^()]*\)[^()]*?\bFROM\b[^()]*?)\s*\)
    \s+(?:AS\s+)?(?P<subalias>[A-Za-z_][\w]*)
    \s+ON\s+(?P<onagg>.*?)
    (?P<tail>\s+(?:WHERE|GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT)\b|$)
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)


def _reorder_independent_aggregate_join(sql: str) -> str | None:
    """Move an independent aggregate subquery earlier to avoid bad SQLite plans."""

    match = _JOIN_AGG_SUBQUERY_RE.search(sql or "")
    if not match:
        return None

    a1 = match.group("a1") or _source_alias(match.group("src1"))
    a2 = match.group("a2") or _source_alias(match.group("src2"))
    subalias = match.group("subalias")
    onagg = match.group("onagg").strip()
    on12 = match.group("on12").strip()
    src1 = match.group("src1").strip()
    src2 = match.group("src2").strip()
    subquery = match.group("subquery").strip()

    if re.search(rf"\b{re.escape(a1)}\.", onagg) and re.search(rf"\b{re.escape(subalias)}\.", onagg):
        reordered_from = (
            f"{match.group('prefix')}({subquery}) {subalias} "
            f"INNER JOIN {src1} ON {onagg} "
            f"INNER JOIN {src2} ON {on12}"
        )
    elif re.search(rf"\b{re.escape(a2)}\.", onagg) and re.search(rf"\b{re.escape(subalias)}\.", onagg):
        reordered_from = (
            f"{match.group('prefix')}({subquery}) {subalias} "
            f"INNER JOIN {src2} ON {onagg} "
            f"INNER JOIN {src1} ON {on12}"
        )
    else:
        return None

    return (sql[: match.start()] + reordered_from + match.group("tail") + sql[match.end() :]).strip()


def _source_alias(source: str) -> str:
    parts = source.strip().split()
    return parts[-1].strip('"') if parts else ""


def format_execution_result(result: ExecutionResult | str, limit: int = 20) -> str:
    """Compact execution result for reflection prompts."""
    if isinstance(result, str):
        return result
    rows = sorted(result.rows, key=lambda row: tuple(str(item) for item in row))
    shown = rows[:limit]
    text = json.dumps(shown, ensure_ascii=False, default=str)
    if len(rows) > limit:
        text += f"\n... ({len(rows) - limit} more rows; total {len(rows)})"
    else:
        text += f"\n(total {len(rows)})"
    return text


class TraceCollector:
    """Collect agent events and write per-query benchmark logs."""

    def __init__(self):
        self._next_round = 1
        self._entries = []
        self._pending_by_id = {}

    def callback(self, event: dict):
        etype = event.get("type")

        if etype == "tool_call":
            entry = {
                "type": "call",
                "round": self._next_round,
                "name": event["name"],
                "args": event.get("arguments", {}),
                "result": None,
            }
            self._entries.append(entry)
            if event.get("id"):
                self._pending_by_id[event["id"]] = entry
            self._next_round += 1
        elif etype == "tool_result":
            result = event.get("result", "")
            entry = None
            event_id = event.get("id")
            if event_id:
                entry = self._pending_by_id.pop(event_id, None)
            if entry is None:
                for item in reversed(self._entries):
                    if (
                        item["type"] == "call"
                        and item["name"] == event.get("name")
                        and item["result"] is None
                    ):
                        entry = item
                        break
            if entry is not None:
                entry["result"] = result
        elif etype == "blocked":
            self._entries.append({
                "type": "block",
                "round": self._next_round,
                "source": event.get("guardrail", ""),
                "msg": event.get("content", ""),
                "name": event.get("name"),
                "args": event.get("arguments", {}),
            })
            self._next_round += 1
        elif etype in {"warning", "sidechain", "append", "trace", "context_rewrite", "finalize"}:
            self._entries.append({
                "type": etype,
                "round": self._next_round,
                "source": event.get("guardrail", ""),
                "msg": event.get("content", "")
                or f"context rewritten to {event.get('message_count', '?')} messages",
                "name": event.get("name"),
                "args": event.get("arguments", {}),
                "call_index": event.get("call_index"),
                "trace_only": bool(event.get("trace_only")),
            })
        elif etype == "done":
            self._pending_by_id.clear()

    def write_logs(self, bench_dir: Path, qid: int, q: dict,
                   response: str, predicted_sql: str | None,
                   result_str: str, elapsed: float,
                   efficiency: dict | None = None):
        efficiency = efficiency or empty_efficiency_metrics()
        header = "\n".join([
            f"Q{qid} [{q.get('difficulty', '?')}] {result_str} {elapsed:.1f}s",
            f"Question: {q['question']}",
            f"Evidence: {q.get('evidence', '') or '(无)'}",
            f"Predicted SQL: {predicted_sql or 'PARSE_ERROR'}",
            f"Golden SQL: {q['SQL']}",
            (
                "LLM Efficiency: "
                f"rounds={efficiency.get('llm_rounds', 0)}, "
                f"cached_input_tokens={efficiency.get('cached_input_tokens', 0)}, "
                f"uncached_input_tokens={efficiency.get('uncached_input_tokens', 0)}, "
                f"output_tokens={efficiency.get('output_tokens', 0)}, "
                f"total_tokens={efficiency.get('total_tokens', 0)}"
            ),
        ])

        detail_lines = [header, "---"]
        for entry in self._entries:
            if entry["type"] == "call":
                args_full = json.dumps(entry["args"], ensure_ascii=False) if entry["args"] else "{}"
                detail_lines.append(f"Round {entry['round']} | {entry['name']}({args_full})")
                result = entry["result"] or "(no result)"
                detail_lines.append(f"  {result}")
            else:
                detail_lines.append(self._format_event_header(entry))
                detail_lines.append(f"  {_format_event_message(entry)}")
            detail_lines.append("---")

        if response:
            detail_lines.append(f"Agent response:\n{response[-1000:]}")
        detail_lines.append("")
        (bench_dir / f"q{qid}.log").write_text("\n".join(detail_lines), encoding="utf-8")

    def summarize_calls(self) -> str:
        parts = []
        for entry in self._entries:
            if entry["type"] == "call":
                parts.append(f"{entry['name']}({_args_brief(entry['args'])})")
            elif entry.get("name"):
                parts.append(f"{entry['name']}({_args_brief(entry['args'])})(blocked)")
        return " → ".join(parts) if parts else "(no calls)"

    def summarize_blocks(self) -> str:
        parts = []
        for entry in self._entries:
            if entry["type"] != "block":
                continue
            label = f"{entry['name']}({_args_brief(entry['args'])})" if entry.get("name") else "text response"
            msg = _normalize_block_message(entry["msg"])
            parts.append(f"[{entry['source']}] {label}: {msg}")
        return "\n".join(parts) if parts else "(none)"

    def detailed_trace_text(self) -> str:
        lines = []
        for entry in self._entries:
            if entry["type"] == "call":
                args_full = json.dumps(entry["args"], ensure_ascii=False) if entry["args"] else "{}"
                lines.append(f"Round {entry['round']} | {entry['name']}({args_full})")
                result = entry["result"] or "(no result)"
                lines.append(f"  {result}")
            else:
                lines.append(self._format_event_header(entry))
                lines.append(f"  {_format_event_message(entry)}")
            lines.append("---")
        return "\n".join(lines) if lines else "(empty trace)"

    @staticmethod
    def _format_event_header(entry: dict) -> str:
        kind_by_type = {
            "block": "BLOCKED",
            "warning": "WARNING",
            "sidechain": "SIDECHAIN",
            "append": "APPEND",
            "trace": "TRACE",
        }
        kind = kind_by_type.get(entry.get("type"), entry.get("type", "EVENT").upper())
        source = entry.get("source", "")
        suffix = " trace-only" if entry.get("trace_only") else ""
        if entry.get("name"):
            args_full = json.dumps(entry["args"], ensure_ascii=False) if entry["args"] else "{}"
            return f"Round {entry['round']} | [{kind} by {source}{suffix}] {entry['name']}({args_full})"
        call_index = entry.get("call_index")
        label = f"call#{call_index}" if call_index is not None else "agent event"
        return f"Round {entry['round']} | [{kind} by {source}{suffix}] {label}"


def _args_brief(args: dict) -> str:
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        sv = str(v)
        if len(sv) > 40:
            sv = sv[:40] + "..."
        parts.append(f"{k}={sv}")
    return ", ".join(parts)


def _normalize_block_message(msg: str) -> str:
    return " ".join((msg or "").split())


def _format_event_message(entry: dict) -> str:
    msg = entry.get("msg", "")
    if entry.get("type") != "block":
        return msg or "(empty event)"
    return _normalize_block_message(msg)


EFFICIENCY_FIELDS = (
    "llm_rounds",
    "cached_input_tokens",
    "uncached_input_tokens",
    "output_tokens",
    "total_tokens",
    "embedding_calls",
    "embedding_documents",
    "embedding_tokens",
    "preprocess_llm_input_tokens",
    "preprocess_llm_cached_input_tokens",
    "preprocess_llm_uncached_input_tokens",
    "preprocess_llm_output_tokens",
    "preprocess_llm_total_tokens",
    "preprocess_embedding_tokens",
)


def empty_efficiency_metrics() -> dict:
    metrics = {field: 0 for field in EFFICIENCY_FIELDS}
    metrics["cache_accounting_source"] = "unknown"
    return metrics


def get_agent_efficiency_metrics(agent) -> dict:
    if hasattr(agent, "llm_metrics"):
        metrics = agent.llm_metrics()
        out = {field: int(metrics.get(field, 0) or 0) for field in EFFICIENCY_FIELDS}
        out["cache_accounting_source"] = str(metrics.get("cache_accounting_source") or "unknown")
        return out
    return empty_efficiency_metrics()


def aggregate_efficiency(rows: list[dict]) -> dict:
    count = len(rows)
    totals = {
        field: sum(int(row.get(field, 0) or 0) for row in rows)
        for field in EFFICIENCY_FIELDS
    }
    averages = {
        f"{field}_per_query": round(totals[field] / count, 3) if count else 0.0
        for field in EFFICIENCY_FIELDS
    }
    return {"totals": totals, "averages": averages}


def load_preprocess_metrics(summary_path: Path | None, total_queries: int) -> dict:
    if summary_path is None:
        return {}
    if not summary_path.exists():
        print(f"Warning: preprocess summary not found: {summary_path}")
        return {}
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    tokens = data.get("preprocess_tokens", {}) if isinstance(data.get("preprocess_tokens"), dict) else {}
    llm_total = int(tokens.get("llm_total_tokens", 0) or 0)
    embedding_total = int(tokens.get("embedding_total_tokens", 0) or 0)
    per_db = data.get("per_database", []) if isinstance(data.get("per_database"), list) else []
    llm_input = int(tokens.get("llm_input_tokens", 0) or 0)
    llm_cached_input = int(tokens.get("llm_cached_input_tokens", 0) or 0)
    llm_uncached_input = int(tokens.get("llm_uncached_input_tokens", 0) or 0)
    llm_output = int(tokens.get("llm_output_tokens", 0) or 0)
    if not llm_input:
        llm_input = sum(int(row.get("preprocess_llm_input_tokens", 0) or 0) for row in per_db)
    if not llm_cached_input:
        llm_cached_input = sum(int(row.get("preprocess_llm_cached_input_tokens", 0) or 0) for row in per_db)
    if not llm_uncached_input:
        llm_uncached_input = sum(int(row.get("preprocess_llm_uncached_input_tokens", 0) or 0) for row in per_db)
    if not llm_output:
        llm_output = sum(int(row.get("preprocess_llm_output_tokens", 0) or 0) for row in per_db)
    embedding_calls = sum(int(row.get("preprocess_embedding_calls", 0) or 0) for row in per_db)
    embedding_documents = sum(int(row.get("preprocess_embedding_documents", 0) or 0) for row in per_db)
    metrics = {
        "preprocess_llm_input_tokens": llm_input,
        "preprocess_llm_cached_input_tokens": llm_cached_input,
        "preprocess_llm_uncached_input_tokens": llm_uncached_input,
        "preprocess_llm_output_tokens": llm_output,
        "preprocess_llm_total_tokens": llm_total,
        "preprocess_embedding_tokens": embedding_total,
        "embedding_calls": embedding_calls,
        "embedding_documents": embedding_documents,
        "embedding_tokens": 0,
    }
    return metrics


def attach_preprocess_metrics(rows: list[dict], metrics: dict) -> None:
    if not rows or not metrics:
        return
    count = len(rows)
    for key, value in metrics.items():
        if key not in EFFICIENCY_FIELDS:
            continue
        total = int(value or 0)
        if total == 0:
            continue
        base, remainder = divmod(total, count)
        for index, row in enumerate(rows):
            row[key] = int(row.get(key, 0) or 0) + base + (1 if index < remainder else 0)


def format_efficiency_line(rows: list[dict], indent: str = "") -> str:
    eff = aggregate_efficiency(rows)
    avg = eff["averages"]
    totals = eff["totals"]
    return (
        f"{indent}Efficiency: "
        f"LLM rounds/q={avg['llm_rounds_per_query']:.2f}, "
        f"cached input tokens/q={avg['cached_input_tokens_per_query']:.1f}, "
        f"uncached input tokens/q={avg['uncached_input_tokens_per_query']:.1f}, "
        f"output tokens/q={avg['output_tokens_per_query']:.1f}, "
        f"total tokens/q={avg['total_tokens_per_query']:.1f}, "
        f"total tokens={totals['total_tokens']}"
    )


def find_db_file(db_dir: Path) -> str | None:
    for ext in DB_EXTS:
        matches = list(db_dir.glob(f"*{ext}"))
        if matches:
            return str(matches[0])
    return None


class ProgressTracker:
    """Thread-safe benchmark progress file writer."""

    def __init__(self, db_map: dict[str, list], progress_path: Path):
        self._lock = threading.Lock()
        self._path = progress_path
        self._states: dict[str, dict] = {
            db_id: {
                "total": len(qs), "status": "pending",
                "done": 0, "correct": 0,
                "started_at": None, "finished_at": None,
            }
            for db_id, qs in db_map.items()
        }
        self._write()

    def start_test(self, db_id: str):
        with self._lock:
            self._states[db_id]["status"] = "testing"
            if self._states[db_id]["started_at"] is None:
                self._states[db_id]["started_at"] = time.time()
            self._write()

    def update(self, db_id: str, done: int, correct: int):
        with self._lock:
            self._states[db_id]["done"] = done
            self._states[db_id]["correct"] = correct
            self._write()

    def finish(self, db_id: str, correct: int, total: int):
        with self._lock:
            self._states[db_id]["status"] = "done"
            self._states[db_id]["done"] = total
            self._states[db_id]["correct"] = correct
            self._states[db_id]["finished_at"] = time.time()
            self._write()

    def _write(self):
        lines = [f"=== Progress — {time.strftime('%Y-%m-%d %H:%M:%S')} ===", ""]
        total_done = sum(s["done"] for s in self._states.values())
        total_queries = sum(s["total"] for s in self._states.values())
        total_correct = sum(s["correct"] for s in self._states.values())
        lines.append(f"Overall: {total_done}/{total_queries} queries, {total_correct} correct")
        lines.append("")
        for db_id in sorted(self._states.keys()):
            s = self._states[db_id]
            pct = s["done"] / s["total"] * 100 if s["total"] else 0
            elapsed = ""
            if s["started_at"] and s["status"] != "done":
                elapsed = f" ({time.time() - s['started_at']:.0f}s)"
            elif s["started_at"] and s["finished_at"]:
                elapsed = f" ({s['finished_at'] - s['started_at']:.0f}s)"
            lines.append(
                f"  [{s['status']:>10}] {db_id:25s} "
                f"{s['done']:>4}/{s['total']:<4} ({pct:5.1f}%) "
                f"correct={s['correct']}{elapsed}"
            )
        lines.append("")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text("\n".join(lines) + "\n", encoding="utf-8")
