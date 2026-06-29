"""Reflection classification for BIRD SQL failures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openai import OpenAI

from agent.utils import load_agent_config
from scripts.BIRD.benchmark_runtime import format_execution_result
from scripts.BIRD.models import BirdCase, BirdRunResult


REFLECTION_CATEGORIES = {
    "DB_EXPLORATION_FIXABLE",
    "DB_PRIOR_REQUIRED",
    "BIRD_STYLE",
}


REFLECTION_SYSTEM_PROMPT = """\
你是 BIRD Text-to-SQL 错误反思分类器。任务是给失败样本做错误归因。

primary_category 从以下三类中选择一个：
- DB_EXPLORATION_FIXABLE：通过更充分的数据库探索、字段比较、值验证、连接路径检查可以修复。
- DB_PRIOR_REQUIRED：需要 BIRD 数据集隐含标注偏好或题面没有给出的先验；数据库证据下存在多个合理解释。
- BIRD_STYLE：主要违反 BIRD 输出口径或 SQL 风格，包括多余/缺失 SELECT 列、列顺序、DISTINCT、额外聚合、额外排序、额外过滤、数值格式化、题面公式尺度。

输出 JSON：
{
  "primary_category": "...",
  "secondary_category": "...或空字符串",
  "reason": "一句话说明",
  "fix_hint": "一句话说明最可能的修复方向"
}
"""


def reflect_result(result: BirdRunResult, db_dir: Path, *, rounds: int = 1) -> dict[str, Any]:
    prompt = _build_result_prompt(result)
    return _call_reflection_llm(db_dir, prompt, rounds=rounds)


def reflect_error(case: BirdCase, db_dir: Path, error: BaseException, *, rounds: int = 1) -> dict[str, Any]:
    prompt = "\n".join(
        [
            f"Database: {case.db_id}",
            f"Question ID: {case.question_id}",
            f"Question: {case.question}",
            f"Evidence: {case.evidence or '(none)'}",
            "",
            f"Golden SQL: {case.golden_sql or '(none)'}",
            "",
            f"Runtime error: {type(error).__name__}: {error}",
        ]
    )
    return _call_reflection_llm(db_dir, prompt, rounds=rounds)


def _build_result_prompt(result: BirdRunResult) -> str:
    case = result.case
    return "\n".join(
        [
            f"Database: {case.db_id}",
            f"Question ID: {case.question_id}",
            f"Difficulty: {case.difficulty}",
            f"Question: {case.question}",
            f"Evidence: {case.evidence or '(none)'}",
            "",
            "Predicted SQL:",
            result.candidate.predicted_sql or "PARSE_ERROR",
            "",
            "Golden SQL:",
            case.golden_sql or "(none)",
            "",
            "Predicted execution result:",
            format_execution_result(result.predicted_execution, limit=8),
            "",
            "Golden execution result:",
            format_execution_result(result.golden_execution, limit=8)
            if result.golden_execution is not None
            else "(none)",
        ]
    )


def _call_reflection_llm(db_dir: Path, prompt: str, *, rounds: int = 1) -> dict[str, Any]:
    cfg = load_agent_config(str(db_dir))
    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["provider"], timeout=120.0)
    messages = [
        {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    kwargs: dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": cfg.get("temperature", 0.2),
    }
    if cfg.get("thinking", False):
        kwargs.pop("temperature", None)
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        kwargs["reasoning_effort"] = cfg.get("thinking_effort", "high")

    last_text = ""
    for _ in range(max(1, rounds)):
        response = client.chat.completions.create(**kwargs)
        last_text = response.choices[0].message.content or ""
        parsed = _parse_reflection_json(last_text)
        if parsed:
            return parsed
        messages.append({"role": "assistant", "content": last_text})
        messages.append({"role": "user", "content": "输出符合要求的 JSON。"})
        kwargs["messages"] = messages

    return _fallback_reflection(last_text)


def _parse_reflection_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
        except Exception:
            return None
    category = str(data.get("primary_category") or "").strip().upper()
    if category not in REFLECTION_CATEGORIES:
        return None
    secondary = str(data.get("secondary_category") or "").strip().upper()
    if secondary and secondary not in REFLECTION_CATEGORIES:
        secondary = ""
    return {
        "reflection_primary_error_category": category,
        "reflection_secondary_error_category": secondary,
        "reflection_reason": str(data.get("reason") or "").strip(),
        "reflection_fix_hint": str(data.get("fix_hint") or "").strip(),
    }


def _fallback_reflection(text: str) -> dict[str, Any]:
    return {
        "reflection_primary_error_category": "DB_EXPLORATION_FIXABLE",
        "reflection_secondary_error_category": "",
        "reflection_reason": "reflection JSON parsing failed; defaulted to database exploration fixable.",
        "reflection_fix_hint": "inspect predicted/golden SQL and database evidence manually.",
        "reflection_raw": text or "",
    }
