#!/usr/bin/env python3
"""Run Pontis agents on KDDCup public tasks and write prediction.csv files."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger(__name__)

DEFAULT_PUBLIC_ROOT = REPO_ROOT / "example_data" / "KDDCUP" / "public"
DEFAULT_RUN_ROOT = DEFAULT_PUBLIC_ROOT / ".pontis_test_runs"

# ═══════════════════════════════════════════════════════════
#  KDD Test Prompt
# ═══════════════════════════════════════════════════════════

KDD_TEST_PROMPT_TEMPLATE = """\
你正在解 KDD Cup 2026 DataAgent-Bench public task。目标是产出最终答案表，不写过程说明。

最终回复只包含一个 JSON 代码块：

```json
{{
  "columns": ["column_1"],
  "rows": [[123]]
}}
```

## Task

- task_id: {task_id}
- difficulty: {difficulty}
- question: {question}

## Output Contract

- JSON 顶层只有 `columns` 和 `rows`；`columns` 是字符串数组，`rows` 是二维数组。
- 每行长度等于列数；多条结果用多行表达，行顺序通常不重要。
- 列只包含题目直接要求的字段。验证用的中间值、解释列、辅助 join 字段留在推理中。
- 列名贴近源字段或题面原词；列数必须严格，避免输出中间列导致额外列惩罚。
- 数值结果用 JSON number，例如 `[4]`、`[178.14]`；字符串只用于真实文本值。
- 平均值、比例和百分数保留足够精度；题目要求位数时再按要求取舍。
- 字符串值保持原始大小写、拼写和格式；时间/日期优先使用数据中的有效展示形式。
- 空值输出 `""`。跨源 join 的输出行需要所有必需字段都有证据支撑。

## Evidence Route

1. 先找数据入口：`find({{"ref":"*:file"}})`，再按需要进入 DB/CSV/JSON/Markdown、table、col、pattern、chunk。
2. `meta` 读字段说明、样例、topk、null_count、统计摘要；列 ref 直接复制 path-style 结果。
3. JSON 结构用 `jd`；文本/Markdown 用 `grep` 定位、`read` 回读行段。
4. DB/CSV/TSV/JSON records 的精确过滤、聚合、join 用 `query`。单文件表名可用 `this`；跨源 join 用 `query(ref=".", sql=...)`。
5. `bash` 只承接工具难以表达的一次性只读计算或抽样验证；路径使用当前 task 内相对路径，public `output/gold.csv` 不属于可用证据。

## Public Reflection Rules

- 先判断列结构：`Which X has Y` 输出 X；`What is Y of X` 输出 Y；只有题目明确要求 `with/and their` 时输出多个字段。
- `Give/Show their Z` 只输出 Z；`Identify X. Name Y` 中 X 是定位条件，输出只取 Y。
- `What is the [entity]` 或 `Which [entity] has ...` 是实体识别题，通常输出该实体的主键/标识列，不输出正文或说明字段。
- `How many X` 的列名优先贴近被计数实体 X；不要机械使用 `count`，除非题面或源查询表达已经明确。
- `Tally X of each Y` 没有 `count/number/how many` 时优先逐项列出 X；有计数词时才输出聚合计数列。
- 复合概念按数据原子字段输出：`full name` 遇到 `first_name` + `last_name` 时分两列；schema/knowledge.md 的字段使用说明优先。
- 源字段能直接回答题目时输出源字段原始值。时间、日期、编号、状态先读字段说明，区分展示字段和计算字段。
- 时间文本必须精确匹配格式和数值；只有题目说 closest/nearest/approximate 时才选近似值。
- `number/id/name/type/time/track/status/category` 这类通用词先消歧；至少比较两个合理字段解释的证据和结果规模。
- `X-related` 先取名称或字段中直接包含 X 的最小集合，再扩展到区域、上级或宽泛关联。
- 有预聚合字段时先判断题目粒度是否停在该层，例如 budget `spent/category`，再决定是否展开明细。
- 聚合前明确粒度：行、实体、患者、事件、分子、账户等。`AVG` 默认排除 NULL 和空字符串；0 是有效值，除非权威文档说明它代表缺失。
- CSV 数值列可能把空字符串当成 0 参与计算；聚合前用 `meta` 或 `query` 查空值分布，必要时加 `col != ''` 和显式 `CAST`。
- 阈值题先找 normal range 和边界包含关系；`among A, how many have B` 要区分同一条记录同时满足，还是同一实体任意记录满足。
- 叙事文本先按章节和行段确认。`from old to new` 取 new；`corrected/revised/adjusted/verified` 后的最终陈述优先。
- SQL `LIKE` 中 `_` 是单字符通配符；匹配含下划线 ID 时用精确等值、拼接或转义。

## Final Check

提交前逐项确认：
- 列数只对应题目要求的输出字段。
- 复合字段的拆分/合并和 schema 一致。
- 数值是 JSON number，精度足够。
- 字段语义、join key、聚合粒度、缺失值规则都有工具证据。
- 每个输出值能追溯到 query/Python 计算结果、meta/jd 信息或 read 的原文行段。
"""

KDD_FINAL_FALLBACK_PROMPT = """\
上一轮答案没有被解析成最终 JSON。

基于当前对话里已经看到的数据、schema、样例和查询结果，直接给出最佳答案表。

输出协议：
- 只输出一个 ```json 代码块
- JSON 顶层只能有 `columns` 和 `rows`
- 不附加解释
"""

KDD_REPAIR_PROMPT_TEMPLATE = """\
你正在修正同一个 KDD public/train task 的上一轮答案。当前只知道本地 proxy 的结构化反馈；
它不会暴露 gold 内容，只能说明值签名匹配、额外列和整体 recall/score。

目标：重新产出更可能正确的最终答案表。最终回复仍然只包含一个 JSON 代码块。

## Task

- task_id: {task_id}
- difficulty: {difficulty}
- question: {question}

## Previous Prediction
{prediction_summary}

## Local Proxy Feedback
{eval_summary}

## Previous Tool Route
{calls_summary}

## Prior Trace
{trace_detail}

## Repair Focus

- 如果 `extra_columns > 0`，优先判断是否输出了题目没有直接要求的标识列、中间值或解释列。
- 如果 `matched_columns = 0`，优先重查答案值和行集；本地 proxy 不按列名匹配，单纯改列名通常不能提分。
- 如果只有部分匹配，检查复合字段拆分、实体识别列、额外列和遗漏列。
- 如果 recall 低，重新确认字段语义、join key、过滤条件、聚合粒度、空字符串/NULL/0、阈值边界和叙事文本最终修正值。
- 不要读取 public `output/gold.csv`；只能使用当前 task 的 input/context 和 Pontis 工具证据。

输出协议：
- 只输出一个 ```json 代码块
- JSON 顶层只能有 `columns` 和 `rows`
- 不附加解释
"""

KDD_REFLECTION_PROMPT_TEMPLATE = """\
你在复盘刚完成的 KDD public task。上下文里已有工具调用、最终 JSON、prediction.csv 和本地 public gold proxy 评估。

Reflection 只写入本地日志，不创建、更新或连接任何图谱实体，不调用工具。

## Case

- task_id: {task_id}
- difficulty: {difficulty}
- result: {result_label}
- elapsed: {elapsed:.1f}s

## Question
{question}

## Prediction
{prediction_summary}

## Proxy Eval
{eval_summary}

## Calls
{calls_summary}

## Guardrail
{blocks_summary}

## Trace
{trace_detail}

## Reflection Task

复盘四件事：
1. 数据源、字段语义、join key、计算口径、输出列结构是否成立。
2. proxy 低分或列匹配不完整时，定位最可能根因：字段语义、join、过滤、聚合、缺失值、输出列数、值类型、叙事文本解析。本地 proxy 比较列值签名，不比较列名；不要把 0 分主要归因于列名。
3. 提炼可迁移规则，但只在 reflection 日志中描述，不写入图谱。不要把 public gold 的具体列名或答案值当成 test 时可见规则；规则必须能从题面、schema、meta、knowledge 或原文证据推出。
4. 如果发现系统性问题，说明应该如何改 prompt、工具或 extractor。

最终回复给简短结论：主要根因、可迁移规则、建议修改点。
"""

KDD_TEST_TOOLS = [
    "find",
    "grep",
    "read",
    "jd",
    "meta",
    "query",
    "bash",
    "agent",
]

KDD_TEST_PROMPTS = [
    "base",
    "tool",
    "ontology",
    "sql",
    "guardrail",
    "project",
    "readme",
]

KDD_TEST_GUARDRAILS = [
    "round_limit",
    "exploration_check",
    "query_abuse",
    "sql_check",
    "bridge_check",
    "disambig_check",
]

_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass
class TaskInfo:
    task_id: str
    difficulty: str
    question: str
    task_dir: Path


@dataclass
class EvalResult:
    has_gold: bool = False
    matched_columns: int = 0
    gold_columns: int = 0
    predicted_columns: int = 0
    extra_columns: int = 0
    recall: float = 0.0
    score_proxy: float = 0.0


@dataclass
class TaskRunResult:
    task_id: str
    difficulty: str
    seconds: float = 0.0
    prediction_csv: str = ""
    trace_log: str = ""
    parse_error: str = ""
    error: str = ""
    columns: list[str] = field(default_factory=list)
    row_count: int = 0
    eval: EvalResult = field(default_factory=EvalResult)


class TraceCollector:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self.pending: dict[str, dict[str, Any]] = {}
        self.round = 1

    def callback(self, event: dict[str, Any]) -> None:
        etype = event.get("type")
        if etype == "tool_call":
            entry = {
                "type": "tool_call",
                "round": self.round,
                "name": event.get("name", ""),
                "arguments": event.get("arguments", {}),
                "result": "",
            }
            self.entries.append(entry)
            if event.get("id"):
                self.pending[event["id"]] = entry
            self.round += 1
        elif etype == "tool_result":
            entry = self.pending.pop(event.get("id", ""), None)
            if entry is None:
                for item in reversed(self.entries):
                    if item.get("type") == "tool_call" and not item.get("result"):
                        entry = item
                        break
            if entry is not None:
                entry["result"] = event.get("result", "")
        elif etype in {"blocked", "warning", "done"}:
            self.entries.append(dict(event, round=self.round))
            if etype != "done":
                self.round += 1

    def summarize_calls(self) -> str:
        parts = []
        for entry in self.entries:
            if entry.get("type") == "tool_call":
                parts.append(f"{entry.get('name')}({_args_brief(entry.get('arguments', {}))})")
            elif entry.get("type") == "blocked" and entry.get("name"):
                parts.append(f"{entry.get('name')}({_args_brief(entry.get('arguments', {}))})(blocked)")
        return " -> ".join(parts) if parts else "(no calls)"

    def summarize_blocks(self) -> str:
        parts = []
        for entry in self.entries:
            if entry.get("type") != "blocked":
                continue
            label = (
                f"{entry.get('name')}({_args_brief(entry.get('arguments', {}))})"
                if entry.get("name")
                else "text response"
            )
            msg = " ".join(str(entry.get("content", "")).split())
            parts.append(f"[{entry.get('guardrail', '')}] {label}: {msg}")
        return "\n".join(parts) if parts else "(none)"

    def detailed_trace_text(self) -> str:
        lines = []
        for entry in self.entries:
            etype = entry.get("type")
            if etype == "tool_call":
                args = json.dumps(entry.get("arguments", {}), ensure_ascii=False)
                lines.append(f"Round {entry.get('round')} | {entry.get('name')}({args})")
                lines.append(_indent(entry.get("result") or "(no result)"))
            elif etype == "done":
                lines.append("Agent final response:")
                lines.append(_indent(entry.get("content", "")))
            else:
                lines.append(f"Round {entry.get('round')} | {etype}: {entry}")
            lines.append("---")
        return "\n".join(lines) if lines else "(empty trace)"

    def write(self, path: Path, *, task: TaskInfo, response: str, result: TaskRunResult) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"task_id: {task.task_id}",
            f"difficulty: {task.difficulty}",
            f"question: {task.question}",
            f"seconds: {result.seconds:.1f}",
            f"prediction_csv: {result.prediction_csv or '-'}",
            f"parse_error: {result.parse_error or '-'}",
            f"error: {result.error or '-'}",
            "---",
        ]
        for entry in self.entries:
            etype = entry.get("type")
            if etype == "tool_call":
                args = json.dumps(entry.get("arguments", {}), ensure_ascii=False)
                lines.append(f"Round {entry.get('round')} | {entry.get('name')}({args})")
                result_text = entry.get("result") or "(no result)"
                lines.append(_indent(result_text))
            elif etype == "done":
                lines.append("Agent final response:")
                lines.append(_indent(entry.get("content", "")))
            else:
                lines.append(f"Round {entry.get('round')} | {etype}: {entry}")
            lines.append("---")
        if response:
            lines.append("Parsed response tail:")
            lines.append(_indent(response[-3000:]))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _indent(text: str) -> str:
    return "\n".join("  " + line for line in str(text).splitlines())


def _args_brief(args: dict[str, Any]) -> str:
    if not args:
        return ""
    parts = []
    for key, value in args.items():
        text = str(value)
        if len(text) > 48:
            text = text[:48] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)


def _normalize_task_id(item: str) -> str:
    item = item.strip()
    if not item:
        return ""
    return item if item.startswith("task_") else f"task_{item}"


def _task_sort_key(task_id: str) -> tuple[int, str]:
    try:
        return (int(task_id.split("_", 1)[1]), task_id)
    except (IndexError, ValueError):
        return (10**9, task_id)


def _load_task(task_dir: Path) -> TaskInfo:
    payload = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    return TaskInfo(
        task_id=str(payload.get("task_id") or task_dir.name),
        difficulty=str(payload.get("difficulty") or ""),
        question=str(payload.get("question") or ""),
        task_dir=task_dir,
    )


def select_tasks(public_root: Path, task_args: list[str], difficulty: str | None, limit: int | None) -> list[TaskInfo]:
    input_root = public_root / "input"
    wanted = {
        _normalize_task_id(part)
        for item in task_args
        for part in item.split(",")
        if _normalize_task_id(part)
    }
    tasks: list[TaskInfo] = []
    for task_dir in sorted(input_root.glob("task_*"), key=lambda p: _task_sort_key(p.name)):
        if not task_dir.is_dir():
            continue
        if wanted and task_dir.name not in wanted:
            continue
        task = _load_task(task_dir)
        if difficulty and task.difficulty != difficulty:
            continue
        tasks.append(task)
        if limit and len(tasks) >= limit:
            break
    return tasks


def parse_answer(text: str) -> tuple[list[str], list[list[Any]], str]:
    if not text:
        return [], [], "empty response"
    match = _JSON_BLOCK_RE.search(text)
    raw = match.group(1).strip() if match else text.strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [], [], f"invalid JSON: line {exc.lineno}, column {exc.colno}: {exc.msg}"
    if not isinstance(payload, dict):
        return [], [], "answer JSON must be an object"
    columns = payload.get("columns")
    rows = payload.get("rows")
    if not isinstance(columns, list) or not columns or not all(isinstance(col, str) for col in columns):
        return [], [], "columns must be a non-empty list of strings"
    if not isinstance(rows, list):
        return [], [], "rows must be a list"
    normalized_rows: list[list[Any]] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, list):
            return [], [], f"row {idx} must be a list"
        if len(row) != len(columns):
            return [], [], f"row {idx} length {len(row)} != column count {len(columns)}"
        normalized_rows.append(row)
    return list(columns), normalized_rows, ""


def force_json_response(agent, response: str) -> str:
    from agent.tools import ToolRegistry

    saved_tools = agent.tools
    agent.tools = ToolRegistry()
    try:
        fallback = agent.chat(KDD_FINAL_FALLBACK_PROMPT)
    finally:
        agent.tools = saved_tools
    if not fallback:
        return response or ""
    return ((response or "").rstrip() + "\n\n" + fallback).strip()


def _preview_rows(rows: list[list[Any]], limit: int = 8) -> str:
    payload = rows[:limit]
    text = json.dumps(payload, ensure_ascii=False)
    if len(rows) > limit:
        text += f"\n... ({len(rows) - limit} more rows)"
    return text


def build_prediction_summary(columns: list[str], rows: list[list[Any]], parse_error: str) -> str:
    if parse_error:
        return f"parse_error: {parse_error}"
    return "\n".join([
        f"columns: {json.dumps(columns, ensure_ascii=False)}",
        f"row_count: {len(rows)}",
        f"row_preview: {_preview_rows(rows)}",
    ])


def build_eval_summary(eval_result: EvalResult) -> str:
    if not eval_result.has_gold:
        return "gold.csv not available for this task"
    return "\n".join([
        "note: local proxy matches column value signatures, not column names",
        f"matched_columns: {eval_result.matched_columns}/{eval_result.gold_columns}",
        f"predicted_columns: {eval_result.predicted_columns}",
        f"extra_columns: {eval_result.extra_columns}",
        f"recall: {eval_result.recall:.4f}",
        f"score_proxy: {eval_result.score_proxy:.4f}",
    ])


def build_reflection_case_prompt(
    task: TaskInfo,
    collector: TraceCollector,
    result: TaskRunResult,
    columns: list[str],
    rows: list[list[Any]],
) -> str:
    if result.error:
        result_label = "ERROR"
    elif result.parse_error:
        result_label = "PARSE_ERROR"
    elif result.eval.has_gold and result.eval.matched_columns == result.eval.gold_columns:
        result_label = "PROXY_MATCH"
    elif result.eval.has_gold:
        result_label = "PROXY_MISMATCH"
    else:
        result_label = "PARSED"
    return KDD_REFLECTION_PROMPT_TEMPLATE.format(
        task_id=task.task_id,
        difficulty=task.difficulty,
        result_label=result_label,
        elapsed=result.seconds,
        question=task.question,
        prediction_summary=build_prediction_summary(columns, rows, result.parse_error),
        eval_summary=build_eval_summary(result.eval),
        calls_summary=collector.summarize_calls(),
        blocks_summary=collector.summarize_blocks(),
        trace_detail=collector.detailed_trace_text(),
    )


def build_repair_prompt(
    task: TaskInfo,
    collector: TraceCollector,
    result: TaskRunResult,
    columns: list[str],
    rows: list[list[Any]],
) -> str:
    trace = collector.detailed_trace_text()
    if len(trace) > 16000:
        trace = trace[-16000:]
    return KDD_REPAIR_PROMPT_TEMPLATE.format(
        task_id=task.task_id,
        difficulty=task.difficulty,
        question=task.question,
        prediction_summary=build_prediction_summary(columns, rows, result.parse_error),
        eval_summary=build_eval_summary(result.eval),
        calls_summary=collector.summarize_calls(),
        trace_detail=trace,
    )


def run_reflection_for_task(
    *,
    task: TaskInfo,
    agent,
    collector: TraceCollector,
    result: TaskRunResult,
    columns: list[str],
    rows: list[list[Any]],
    task_out_dir: Path,
    effort: str,
) -> None:
    from agent.config import AgentSpec
    from agent.guardrail import build_guardrails
    from agent.prompt import build_prompt_messages
    from agent.tools import ToolRegistry

    reflection_prompts = ["base", "tool", "ontology", "project", "readme"]
    if effort != "mid":
        reflection_prompts.append("effort")
    reflection_spec = AgentSpec(
        effort=effort,
        tools=["find", "grep", "read", "jd", "meta", "bash", "query"],
        prompts=reflection_prompts,
    )
    reflection_spec.projects = [task.task_id]
    reflection_spec.guardrails = build_guardrails(reflection_spec, ["round_limit"])

    saved_callback = agent._trace_callback
    reflection_collector = TraceCollector()
    try:
        agent.tools = ToolRegistry()
        agent.guardrails = reflection_spec.guardrails
        agent.set_system_prompt(build_prompt_messages(reflection_spec))
        agent._trace_callback = reflection_collector.callback

        response = agent.chat(build_reflection_case_prompt(task, collector, result, columns, rows))
    finally:
        agent._trace_callback = saved_callback

    out = [
        f"task_id: {task.task_id}",
        f"difficulty: {task.difficulty}",
        f"question: {task.question}",
        f"seconds: {result.seconds:.1f}",
        "--- reflection trace ---",
        reflection_collector.detailed_trace_text(),
        "--- reflection response ---",
        response or "",
        "",
    ]
    (task_out_dir / "reflection.log").write_text("\n".join(out), encoding="utf-8")


def write_prediction_csv(path: Path, columns: list[str], rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(["" if value is None else value for value in row])


def _read_csv_table(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _normalize_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.strip().replace("\r\n", "\n").replace("\r", "\n")
    if text.lower() in {"null", "none", "nan", "nat", "<na>"}:
        return ""
    try:
        decimal = Decimal(text)
    except (InvalidOperation, ValueError):
        return text
    return str(decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _column_signatures(rows: list[list[Any]], width: int) -> Counter[tuple[str, ...]]:
    signatures: Counter[tuple[str, ...]] = Counter()
    for col_idx in range(width):
        values = []
        for row in rows:
            cell = row[col_idx] if col_idx < len(row) else ""
            values.append(_normalize_cell(cell))
        signatures[tuple(sorted(values))] += 1
    return signatures


def evaluate_prediction(prediction_csv: Path, gold_csv: Path, penalty_lambda: float) -> EvalResult:
    if not gold_csv.exists() or not prediction_csv.exists():
        return EvalResult(has_gold=gold_csv.exists())
    pred_cols, pred_rows = _read_csv_table(prediction_csv)
    gold_cols, gold_rows = _read_csv_table(gold_csv)
    pred_sig = _column_signatures(pred_rows, len(pred_cols))
    gold_sig = _column_signatures(gold_rows, len(gold_cols))
    matched = sum(min(count, pred_sig.get(sig, 0)) for sig, count in gold_sig.items())
    predicted = len(pred_cols)
    gold = len(gold_cols)
    extra = max(0, predicted - matched)
    recall = matched / gold if gold else 0.0
    score = max(0.0, recall - penalty_lambda * (extra / predicted if predicted else 0.0))
    return EvalResult(
        has_gold=True,
        matched_columns=matched,
        gold_columns=gold,
        predicted_columns=predicted,
        extra_columns=extra,
        recall=round(recall, 4),
        score_proxy=round(score, 4),
    )


def maybe_extract_task(task: TaskInfo, public_root: Path, summary_dir: Path) -> None:
    """Run full extraction stack for one task. Agent/AI stages stay enabled."""
    summary_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "KDDCUP" / "extract_public.py"),
        "--public-root",
        str(public_root),
        "--task",
        task.task_id,
        "--force",
        "--clear-task-graph",
        "--summary",
        str(summary_dir / f"{task.task_id}.json"),
    ]
    subprocess.run(cmd, cwd=str(REPO_ROOT), check=True)


def run_task(
    task: TaskInfo,
    *,
    public_root: Path,
    run_dir: Path,
    prediction_root: Path,
    max_rounds: int,
    effort: str,
    extract_first: bool,
    reflection: bool,
    penalty_lambda: float,
    repair_low_score: int,
    repair_threshold: float,
) -> TaskRunResult:
    from agent.config import AgentSpec, create_agent
    from agent.guardrail import build_guardrails

    task_dir = task.task_dir.resolve()
    task_out_dir = run_dir / "tasks" / task.task_id
    task_out_dir.mkdir(parents=True, exist_ok=True)
    prediction_csv = prediction_root / task.task_id / "prediction.csv"
    trace_log = task_out_dir / "trace.log"
    result = TaskRunResult(
        task_id=task.task_id,
        difficulty=task.difficulty,
        prediction_csv=str(prediction_csv),
        trace_log=str(trace_log),
    )
    collector = TraceCollector()

    t0 = time.time()
    response = ""
    columns: list[str] = []
    rows: list[list[Any]] = []
    try:
        if extract_first:
            maybe_extract_task(task, public_root, run_dir / "extract_summaries")

        prompts = list(KDD_TEST_PROMPTS)
        if effort != "mid":
            prompts.append("effort")
        spec = AgentSpec(
            effort=effort,
            max_rounds=max_rounds,
            tools=list(KDD_TEST_TOOLS),
            prompts=prompts,
        )
        spec.projects = [task.task_id]
        spec.guardrails = build_guardrails(spec, KDD_TEST_GUARDRAILS)
        agent = create_agent(str(task_dir), spec, trace_callback=collector.callback)
        prompt = KDD_TEST_PROMPT_TEMPLATE.format(
            task_id=task.task_id,
            difficulty=task.difficulty,
            question=task.question,
        )
        response = agent.chat(prompt)
        columns, rows, parse_error = parse_answer(response)
        if parse_error:
            response = force_json_response(agent, response)
            columns, rows, parse_error = parse_answer(response)
        result.parse_error = parse_error
        if not parse_error:
            write_prediction_csv(prediction_csv, columns, rows)
            result.columns = columns
            result.row_count = len(rows)
            gold_csv = public_root / "output" / task.task_id / "gold.csv"
            result.eval = evaluate_prediction(prediction_csv, gold_csv, penalty_lambda)
            if repair_low_score > 0 and result.eval.has_gold and result.eval.score_proxy < repair_threshold:
                best_columns = columns
                best_rows = rows
                best_eval = result.eval
                for repair_idx in range(1, repair_low_score + 1):
                    repair_collector = TraceCollector()
                    repair_prompts = list(KDD_TEST_PROMPTS)
                    if effort != "mid":
                        repair_prompts.append("effort")
                    repair_spec = AgentSpec(
                        effort=effort,
                        max_rounds=max_rounds,
                        tools=list(KDD_TEST_TOOLS),
                        prompts=repair_prompts,
                    )
                    repair_spec.projects = [task.task_id]
                    repair_spec.guardrails = build_guardrails(repair_spec, KDD_TEST_GUARDRAILS)
                    repair_agent = create_agent(str(task_dir), repair_spec, trace_callback=repair_collector.callback)
                    repair_response = repair_agent.chat(
                        build_repair_prompt(task, collector, result, best_columns, best_rows)
                    )
                    repair_columns, repair_rows, repair_parse_error = parse_answer(repair_response)
                    if repair_parse_error:
                        repair_response = force_json_response(repair_agent, repair_response)
                        repair_columns, repair_rows, repair_parse_error = parse_answer(repair_response)

                    repair_csv = task_out_dir / f"repair_{repair_idx}.prediction.csv"
                    repair_result = TaskRunResult(
                        task_id=task.task_id,
                        difficulty=task.difficulty,
                        prediction_csv=str(repair_csv),
                        trace_log=str(task_out_dir / f"repair_{repair_idx}.trace.log"),
                        parse_error=repair_parse_error,
                        columns=repair_columns if not repair_parse_error else [],
                        row_count=len(repair_rows) if not repair_parse_error else 0,
                    )
                    if not repair_parse_error:
                        write_prediction_csv(repair_csv, repair_columns, repair_rows)
                        repair_result.eval = evaluate_prediction(repair_csv, gold_csv, penalty_lambda)
                        if repair_result.eval.score_proxy > best_eval.score_proxy:
                            best_columns = repair_columns
                            best_rows = repair_rows
                            best_eval = repair_result.eval
                            write_prediction_csv(prediction_csv, best_columns, best_rows)
                            result.columns = best_columns
                            result.row_count = len(best_rows)
                            result.eval = best_eval
                    repair_collector.write(
                        task_out_dir / f"repair_{repair_idx}.trace.log",
                        task=task,
                        response=repair_response or "",
                        result=repair_result,
                    )
                    (task_out_dir / f"repair_{repair_idx}.result.json").write_text(
                        json.dumps(asdict(repair_result), ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    if best_eval.score_proxy >= repair_threshold:
                        break
        if reflection:
            result.seconds = time.time() - t0
            try:
                run_reflection_for_task(
                    task=task,
                    agent=agent,
                    collector=collector,
                    result=result,
                    columns=columns,
                    rows=rows,
                    task_out_dir=task_out_dir,
                    effort=effort,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Reflection failed: %s", task.task_id)
                (task_out_dir / "reflection.error.log").write_text(
                    f"{type(exc).__name__}: {exc}\n",
                    encoding="utf-8",
                )
    except Exception as exc:  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"
        logger.exception("Task failed: %s", task.task_id)
    finally:
        result.seconds = time.time() - t0
        collector.write(trace_log, task=task, response=response, result=result)
        (task_out_dir / "result.json").write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return result


def write_run_summary(path: Path, results: list[TaskRunResult]) -> None:
    total = len(results)
    parsed = sum(1 for r in results if not r.parse_error and not r.error)
    scored = [r for r in results if r.eval.has_gold and not r.parse_error and not r.error]
    avg_recall = sum(r.eval.recall for r in scored) / len(scored) if scored else 0.0
    avg_score = sum(r.eval.score_proxy for r in scored) / len(scored) if scored else 0.0
    payload = {
        "total": total,
        "parsed": parsed,
        "scored": len(scored),
        "avg_recall": round(avg_recall, 4),
        "avg_score_proxy": round(avg_score, 4),
        "results": [asdict(result) for result in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Pontis KDDCup public task tests.")
    parser.add_argument("--public-root", default=str(DEFAULT_PUBLIC_ROOT))
    parser.add_argument("--task", action="append", default=[], help="Task id, e.g. task_250 or 250. Repeat or comma-separate.")
    parser.add_argument("--difficulty", choices=["easy", "medium", "hard", "extreme"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--run-id", help="Run directory name. Defaults to current timestamp.")
    parser.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT))
    parser.add_argument("--output-root", help="Where to write official-layout output/task_id/prediction.csv. Defaults to run_dir/output.")
    parser.add_argument("--extract-first", action="store_true", help="Run full extraction before solving each selected task.")
    parser.add_argument("--max-rounds", type=int, default=80)
    parser.add_argument("--effort", default="max", choices=["mid", "high", "max"])
    parser.add_argument("--penalty-lambda", type=float, default=0.1, help="Local proxy penalty for extra columns; official lambda is not public here.")
    parser.add_argument("--reflection", action="store_true", help="Run a post-task reflection pass in the same agent conversation.")
    parser.add_argument("--repair-low-score", type=int, default=0, help="For public/train runs with gold, retry low-scoring tasks this many times and keep only improved predictions.")
    parser.add_argument("--repair-threshold", type=float, default=0.999, help="Run repair while local proxy score is below this threshold.")
    parser.add_argument("--task-workers", type=int, default=1, help="Number of KDD tasks to solve concurrently.")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    public_root = Path(args.public_root).resolve()
    tasks = select_tasks(public_root, args.task, args.difficulty, args.limit)
    if args.list:
        for task in tasks:
            print(f"{task.task_id}\t{task.difficulty}\t{task.question}")
        return
    if not tasks:
        print("No tasks selected", file=sys.stderr)
        raise SystemExit(1)

    run_id = args.run_id or time.strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.run_root).resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    prediction_root = Path(args.output_root).resolve() if args.output_root else run_dir / "output"
    prediction_root.mkdir(parents=True, exist_ok=True)

    print("=== KDDCup Public Test ===")
    print(f"public root: {public_root}")
    print(f"run dir: {run_dir}")
    print(f"prediction root: {prediction_root}")
    print(f"tasks: {len(tasks)}")
    print(f"extract first: {'on' if args.extract_first else 'off'}")
    print(f"reflection: {'on' if args.reflection else 'off'}")
    print(f"repair low score: {args.repair_low_score} (threshold={args.repair_threshold})")
    print(f"task workers: {args.task_workers}")
    print()

    results: list[TaskRunResult] = []
    task_workers = max(1, int(args.task_workers or 1))

    def run_selected_task(idx: int, task: TaskInfo) -> TaskRunResult:
        print(f"[{idx}/{len(tasks)}] {task.task_id} {task.difficulty}")
        result = run_task(
            task,
            public_root=public_root,
            run_dir=run_dir,
            prediction_root=prediction_root,
            max_rounds=args.max_rounds,
            effort=args.effort,
            extract_first=args.extract_first,
            reflection=args.reflection,
            penalty_lambda=args.penalty_lambda,
            repair_low_score=args.repair_low_score,
            repair_threshold=args.repair_threshold,
        )
        status = "OK"
        if result.error:
            status = "ERROR"
        elif result.parse_error:
            status = "PARSE_ERROR"
        eval_text = ""
        if result.eval.has_gold:
            eval_text = (
                f" matched={result.eval.matched_columns}/{result.eval.gold_columns}"
                f" pred_cols={result.eval.predicted_columns}"
                f" recall={result.eval.recall:.3f}"
                f" score≈{result.eval.score_proxy:.3f}"
            )
        print(
            f"  {status} {result.seconds:.1f}s rows={result.row_count} "
            f"cols={len(result.columns)}{eval_text}"
        )
        if result.error:
            print(f"    error: {result.error}")
        if result.parse_error:
            print(f"    parse: {result.parse_error}")
        return result

    if task_workers == 1:
        for idx, task in enumerate(tasks, 1):
            results.append(run_selected_task(idx, task))
    else:
        with ThreadPoolExecutor(max_workers=task_workers) as pool:
            futures = {
                pool.submit(run_selected_task, idx, task): task.task_id
                for idx, task in enumerate(tasks, 1)
            }
            for future in as_completed(futures):
                results.append(future.result())

    results.sort(key=lambda result: _task_sort_key(result.task_id))

    summary_path = run_dir / "summary.json"
    write_run_summary(summary_path, results)
    print()
    print(f"summary: {summary_path}")
    print(f"output: {prediction_root}")

    failed = sum(1 for r in results if r.error or r.parse_error)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
