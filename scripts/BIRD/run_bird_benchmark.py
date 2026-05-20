#!/usr/bin/env python3
"""BIRD Text-to-SQL Benchmark / Train Runner。

支持 dev 和 train 两种数据集：
  dev:   11 DB, 1534 queries
  train: 69 DB, 9428 queries (--train flag)

每个 query 生成一个主日志：
  q{id}.log        详细版（含每轮工具调用的完整参数和返回值）
启用 `--reflection` 时，还会额外生成：
  q{id}.reflection.log  题后复盘结果

Usage:
    python -m scripts.BIRD.run_bird_benchmark
    python -m scripts.BIRD.run_bird_benchmark --train
    python -m scripts.BIRD.run_bird_benchmark --db toxicology
    python -m scripts.BIRD.run_bird_benchmark --train --skip-extract
    python -m scripts.BIRD.run_bird_benchmark --train --limit 10
    python -m scripts.BIRD.run_bird_benchmark --train --db citeseer --qids 4141 --reflection
"""
import json
import logging
import re
import sqlite3
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.BIRD.common import (
    PROJECT_ROOT,
    get_benchmark_dir,
    get_data_dir,
    get_db_base,
    get_preprocess_dir,
    get_progress_path,
    get_results_dir,
)

logger = logging.getLogger(__name__)

# BIRD 求解阶段不使用专门的 agent mode。
# 这里显式声明脚本需要的工具、prompt 段和 guardrail，避免通用 agent 配置
# 被 benchmark 特例污染。
BIRD_BENCHMARK_TOOLS = ["find", "grep", "meta", "query"]
BIRD_BENCHMARK_PROMPTS = [
    "base", "tool", "ontology", "meta", "sql",
    "guardrail", "project", "readme",
]
BIRD_BENCHMARK_GUARDRAILS = [
    "round_limit", "exploration_check",
    "sql_check", "bridge_check", "disambig_check",
]

# ═══════════════════════════════════════════════════════════
#  Prompts
# ═══════════════════════════════════════════════════════════

# 主 benchmark 求解 prompt。
# 输入时机：
# - 每道题创建 agent 后
# - 在第一次 `agent.chat(prompt)` 时输入
# - 这是生成最终 SQL 之前的主用户提示词
QUERY_PROMPT_BASE_TEMPLATE = """\
你正在 BIRD 数据集的 benchmark 测试中。
{project_scope}

输出格式：一个 ```sql``` 代码块，代码块内是一条 SQLite SELECT 语句。多值答案用单列多行表示。

{bird_global_section}

请根据以下信息生成一条 SQLite SQL 查询。

问题：{question}

提示：{evidence}

"""

BIRD_PROJECT_SCOPE = """\
本次运行会打开两个 project：
- 当前数据库项目用于探索 schema、执行查询，并最终回答用户 query。
- `bird` 项目：BIRD 数据集的全局知识库，存储 SQL 生成任务的抽象知识和经验总结。
当前库 schema 以当前数据库项目为准；`bird` 提供跨库 SQL 经验参考。
项目 ref 入口：当前库 `{current_project}::*:file:db`，全局经验 `bird::*:example`。
"""

LOCAL_ONLY_PROJECT_SCOPE = """\
本次运行只打开当前数据库项目：`{current_project}`。
"""

BIRD_GLOBAL_PROMPT_SECTION = """\
关于 `bird` 经验的使用：
- 在 `bird` 项目的 example 知识中检索相近题型，迁移 SQL 写作风格和输出习惯。
- 检索词使用问题意图、SQL 形态、输出契约和 evidence 口径，例如 percentage、conditional aggregation、return id、multiply by 100。
- 最终 SQL 按当前数据库 schema 生成，并用检索到的 BIRD 偏好检查输出列、聚合粒度、排序、limit、distinct 和比例公式。

"""

QUERY_PROMPT_MINIMAL_TEMPLATE = """\
请根据以下 BIRD 问题生成一条 SQLite SQL 查询。
{project_scope}
{bird_global_section}

输出格式：一个 ```sql``` 代码块，代码块内是一条 SQLite SELECT 语句。

问题：{question}

提示：{evidence}

"""

PROMPT_PROFILES = ("full", "minimal")

# SQL 兜底 prompt。
# 输入时机：
# - 主求解阶段结束后，如果 agent 最后一轮回复里没有可解析的 SQL
# - 此时会临时禁用所有工具，再追加一次 `agent.chat(...)`
# - 目标是强制 agent 基于已有上下文直接收敛出最终 SQL
SQL_FALLBACK_PROMPT = """\
上一轮没有输出可解析的最终 SQL。

现在进入收敛阶段。基于当前对话里已经获得的 schema、样例、知识和查询结果，给出当前最佳 SQLite 查询。

输出格式：一个 ```sql``` 代码块，代码块内是一条 SELECT 语句。
"""

SQL_REPAIR_PROMPT_TEMPLATE = """\
当前最终 SQL 在 SQLite 中执行失败。

执行错误：
{error}

原 SQL：
```sql
{sql}
```

基于当前对话里已经获得的 schema、样例、知识和查询结果，给出修正后的 SQLite SELECT 查询。

输出格式：一个 ```sql``` 代码块，代码块内只放修正后的 SELECT 语句。
"""

# 题后反思 prompt。
# 输入时机：
# - 只有开启 `--reflection` 时才会使用
# - 每道题完成、SQL 已执行并得到 correct / wrong / error 结果之后输入
# - 复用同一个 agent 会话，让 agent 基于刚才的完整执行轨迹复盘，并决定是否更新 `bird` 知识
REFLECTION_CASE_PROMPT_TEMPLATE = """\
你现在仍在同一个对话上下文里：刚刚的 benchmark 消息、工具调用和最终 SQL 都还在。
你不是新开一个会话，而是继续复盘这条已经完成并已验证结果的 benchmark case。

按 benchmark 的解题工作流回放这道题，判断做对/做错的根因，并在存在可迁移经验时更新 `bird`。

本轮复盘对象：
- 数据库项目：{db_id}
- Question ID: {question_id}
- Difficulty: {difficulty}
- Result: {result}
- Elapsed: {elapsed:.1f}s

题目：
{question}

Evidence：
{evidence}

Predicted SQL：
{predicted_sql}

Golden SQL：
{golden_sql}

Benchmark 调用链摘要：
{calls_summary}

Guardrail / blocks：
{blocks_summary}

详细执行轨迹：
{trace_detail}

你的任务：
1. 在 `bird` 中检索最相关的已有知识，先判断已有实体是否可以补充或修正。
2. 可迁移经验写入 `knowledge:convention` / `knowledge:pattern` / `knowledge:lesson` / `knowledge:example`。
3. `knowledge:example` 写成解释型 benchmark case，包含 question、evidence、golden_sql、db_id、question_id、difficulty、schema_background、bird_bias、why_this_case_matters、transfer_hint；错误案例补充 predicted_sql、error_type、mistake_summary、wrong_assumption、fix_hint。
4. 知识内容使用高密度摘要字段，例如 `decision_summary`、`mistake_summary`、`verification_note`、`rejected_alternatives`。
5. example 与对应的抽象知识实体建立普通图边；抽象知识使用去 schema 化表达。
6. 没有新的跨库经验时，明确说明本轮不写入知识实体。
7. 新建实体前说明已检查的相关实体、已有实体的缺口和新实体的必要性。
"""

REFLECTION_CASE_NO_BIRD_PROMPT_TEMPLATE = """\
你现在仍在同一个对话上下文里：刚刚的 benchmark 消息、工具调用和最终 SQL 都还在。
你不是新开一个会话，而是继续复盘这条已经完成并已验证结果的 benchmark case。

本次运行使用当前数据库项目、工具调用轨迹、预测 SQL 和 golden SQL 做复盘，输出高密度错误归因与可复用改进建议。

本轮复盘对象：
- 数据库项目：{db_id}
- Question ID: {question_id}
- Difficulty: {difficulty}
- Result: {result}
- Elapsed: {elapsed:.1f}s

题目：
{question}

Evidence：
{evidence}

Predicted SQL：
{predicted_sql}

Golden SQL：
{golden_sql}

Benchmark 调用链摘要：
{calls_summary}

Guardrail / blocks：
{blocks_summary}

详细执行轨迹：
{trace_detail}

你的任务：
1. 判断错误是否来自 schema linking、值定位、join 路径、聚合粒度、排序/limit、SQL 组织、输出契约或执行语法。
2. 若结果正确，指出最关键的成功条件和仍可能脆弱的地方。
3. 若结果错误，给出最小修正方向。
4. 输出只包含复盘结论。
"""

DB_EXTS = (".sqlite", ".db", ".sqlite3", ".duckdb")

def assign_question_ids(questions: list[dict]) -> list[dict]:
    """为没有 question_id 的数据集补一个稳定 id。"""
    normalized = []
    for idx, q in enumerate(questions):
        item = dict(q)
        if item.get("question_id") is None:
            item["question_id"] = idx
        normalized.append(item)
    return normalized

# ═══════════════════════════════════════════════════════════
#  SQL 提取与执行
# ═══════════════════════════════════════════════════════════

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


def execute_sql(db_path: str, sql: str) -> set | str:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.execute(sql)
        rows = cursor.fetchall()
        conn.close()
        return set(tuple(r) for r in rows)
    except Exception as e:
        return f"ERROR: {e}"


def is_correct(predicted: set | str, golden: set | str) -> bool:
    if isinstance(predicted, str) or isinstance(golden, str):
        return False
    return predicted == golden


# ═══════════════════════════════════════════════════════════
#  Trace 收集 + 两级日志
# ═══════════════════════════════════════════════════════════

class TraceCollector:
    """收集 agent 事件，生成简洁版和详细版日志。"""

    def __init__(self):
        self._next_round = 1
        self._entries = []  # [{type, round, ...}]
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
        elif etype == "warning":
            pass
        elif etype == "done":
            self._pending_by_id.clear()

    def write_logs(self, bench_dir: Path, qid: int, q: dict,
                   response: str, predicted_sql: str | None,
                   result_str: str, elapsed: float):
        """写两个日志文件。"""
        # ── 通用头部 ──
        header = "\n".join([
            f"Q{qid} [{q.get('difficulty', '?')}] {result_str} {elapsed:.1f}s",
            f"Question: {q['question']}",
            f"Evidence: {q.get('evidence', '') or '(无)'}",
            f"Predicted SQL: {predicted_sql or 'PARSE_ERROR'}",
            f"Golden SQL: {q['SQL']}",
        ])

        # ── 详细版 ──
        detail_lines = [header, "---"]
        for entry in self._entries:
            if entry["type"] == "call":
                args_full = json.dumps(entry["args"], ensure_ascii=False) if entry["args"] else "{}"
                detail_lines.append(f"Round {entry['round']} | {entry['name']}({args_full})")
                result = entry["result"] or "(no result)"
                detail_lines.append(f"  {result}")
            else:
                detail_lines.append(self._format_block_header(entry))
                detail_lines.append(f"  {_normalize_block_message(entry['msg'])}")
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
                lines.append(self._format_block_header(entry))
                lines.append(f"  {_normalize_block_message(entry['msg'])}")
            lines.append("---")
        return "\n".join(lines) if lines else "(empty trace)"

    @staticmethod
    def _format_block_header(entry: dict) -> str:
        if entry.get("name"):
            args_full = json.dumps(entry["args"], ensure_ascii=False) if entry["args"] else "{}"
            return f"Round {entry['round']} | [BLOCKED by {entry['source']}] {entry['name']}({args_full})"
        return f"Round {entry['round']} | [BLOCKED by {entry['source']}] text response"


def _args_brief(args: dict) -> str:
    """参数的简洁表示。"""
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


# ═══════════════════════════════════════════════════════════
#  辅助
# ═══════════════════════════════════════════════════════════

def find_db_file(db_dir: Path) -> str | None:
    for ext in DB_EXTS:
        matches = list(db_dir.glob(f"*{ext}"))
        if matches:
            return str(matches[0])
    return None


def build_agent_projects(db_id: str, use_bird_global: bool) -> list[str]:
    projects = [db_id]
    if use_bird_global:
        projects.append("bird")
    return projects


def load_query_prompt_template(args) -> str:
    prompt_file = getattr(args, "prompt_file", None)
    if prompt_file:
        return Path(prompt_file).read_text(encoding="utf-8")

    if getattr(args, "prompt_profile", "full") == "minimal":
        return QUERY_PROMPT_MINIMAL_TEMPLATE

    return QUERY_PROMPT_BASE_TEMPLATE


def build_query_prompt(q: dict, args) -> str:
    question = q["question"]
    evidence = q.get("evidence", "") or "(无额外提示)"
    current_project = q.get("db_id") or "current_project"
    bird_global_note = (
        "本次运行启用 `bird` 全局经验库。"
        if getattr(args, "use_bird_global", True)
        else "本次运行未启用 `bird` 全局经验库。"
    )
    project_scope = (
        BIRD_PROJECT_SCOPE
        if getattr(args, "use_bird_global", True)
        else LOCAL_ONLY_PROJECT_SCOPE
    ).format(current_project=current_project)
    bird_global_section = (
        BIRD_GLOBAL_PROMPT_SECTION
        if getattr(args, "use_bird_global", True)
        else ""
    )
    template = load_query_prompt_template(args)
    if getattr(args, "prompt_file", None):
        return (
            template
            .replace("{question}", question)
            .replace("{evidence}", evidence)
            .replace("{bird_global_note}", bird_global_note)
            .replace("{project_scope}", project_scope)
            .replace("{bird_global_section}", bird_global_section)
            .replace("{current_project}", current_project)
        )
    return template.format(
        question=question,
        evidence=evidence,
        current_project=current_project,
        bird_global_note=bird_global_note,
        project_scope=project_scope,
        bird_global_section=bird_global_section,
    )


def build_reflection_case_prompt(db_id: str, q: dict, collector: TraceCollector,
                                 predicted_sql: str | None, result_str: str,
                                 elapsed: float, use_bird_global: bool) -> str:
    template = (
        REFLECTION_CASE_PROMPT_TEMPLATE
        if use_bird_global
        else REFLECTION_CASE_NO_BIRD_PROMPT_TEMPLATE
    )
    return template.format(
        db_id=db_id,
        question_id=q.get("question_id", 0),
        difficulty=q.get("difficulty", "?"),
        result=result_str,
        elapsed=elapsed,
        question=q["question"],
        evidence=q.get("evidence", "") or "(无额外提示)",
        predicted_sql=predicted_sql or "PARSE_ERROR",
        golden_sql=q["SQL"],
        calls_summary=collector.summarize_calls(),
        blocks_summary=collector.summarize_blocks(),
        trace_detail=collector.detailed_trace_text(),
    )


def run_reflection_for_case(db_id: str, q: dict,
                            agent,
                            predicted_sql: str | None, result_str: str,
                            elapsed: float, bench_dir: Path,
                            use_bird_global: bool) -> None:
    from agent.config import AgentSpec, resolve_mode
    from agent.prompt import build_prompt_messages
    from agent.tools import build_registry

    reflection_spec = AgentSpec(mode="reflection", effort="max")
    reflection_spec.projects = build_agent_projects(db_id, use_bird_global)
    resolve_mode(reflection_spec)

    # 方案 1：沿用同一个 agent 会话，只在反思阶段切到 reflection 配置。
    agent.tools = build_registry(reflection_spec)
    agent.set_system_prompt(build_prompt_messages(reflection_spec))
    agent.guardrails = reflection_spec.guardrails
    while agent.messages and agent.messages[0].get("role") == "system":
        agent.messages.pop(0)
    agent.messages = list(agent._system_messages) + agent.messages

    prompt = build_reflection_case_prompt(
        db_id=db_id,
        q=q,
        collector=agent._reflection_collector,
        predicted_sql=predicted_sql,
        result_str=result_str,
        elapsed=elapsed,
        use_bird_global=use_bird_global,
    )
    response = agent.chat(prompt)
    qid = q.get("question_id", 0)
    out = [
        f"Q{qid} [{q.get('difficulty', '?')}] {result_str} {elapsed:.1f}s",
        f"Question: {q['question']}",
        f"Evidence: {q.get('evidence', '') or '(无)'}",
        f"Predicted SQL: {predicted_sql or 'PARSE_ERROR'}",
        f"Golden SQL: {q['SQL']}",
        "",
        response or "",
        "",
    ]
    (bench_dir / f"q{qid}.reflection.log").write_text("\n".join(out), encoding="utf-8")


def force_sql_response(agent, response: str) -> str:
    """When the benchmark agent ends without SQL, force a no-tool final answer."""
    from agent.tools import ToolRegistry

    saved_tools = agent.tools
    agent.tools = ToolRegistry()
    try:
        fallback = agent.chat(SQL_FALLBACK_PROMPT)
    finally:
        agent.tools = saved_tools

    if not fallback:
        return response or ""
    if not response:
        return fallback
    return response.rstrip() + "\n\n" + fallback


def repair_exec_error_response(agent, response: str, sql: str, error: str) -> str:
    """Ask for one no-tool SQL repair when the final SQL does not execute."""
    from agent.tools import ToolRegistry

    prompt = SQL_REPAIR_PROMPT_TEMPLATE.format(sql=sql, error=error)
    saved_tools = agent.tools
    agent.tools = ToolRegistry()
    try:
        repaired = agent.chat(prompt)
    finally:
        agent.tools = saved_tools

    if not repaired:
        return response or ""
    if not response:
        return repaired
    return response.rstrip() + "\n\n" + repaired


# ═══════════════════════════════════════════════════════════
#  汇总日志
# ═══════════════════════════════════════════════════════════

def write_db_summary(bench_dir: Path, db_id: str, results: list[dict]):
    correct = sum(1 for r in results if r['correct'])
    total = len(results)
    pct = correct / total * 100 if total else 0

    by_diff = defaultdict(lambda: [0, 0])
    for r in results:
        by_diff[r.get('difficulty', '?')][1] += 1
        if r['correct']:
            by_diff[r.get('difficulty', '?')][0] += 1

    lines = [f"=== {db_id} Summary ===", f"Total: {correct}/{total} ({pct:.1f}%)", "", "By difficulty:"]
    for diff in ["simple", "moderate", "challenging"]:
        c, t = by_diff.get(diff, [0, 0])
        if t > 0:
            lines.append(f"  {diff}: {c}/{t} ({c/t*100:.1f}%)")
    lines += ["", "Per query:"]
    for r in sorted(results, key=lambda r: r['question_id']):
        status = "OK" if r['correct'] else r['result']
        lines.append(f"  Q{r['question_id']} [{r.get('difficulty', '?')}] {status} {r['elapsed']:.1f}s")
    (bench_dir / "summary.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_total_summary(output_dir: Path, all_results: list[dict]):
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "benchmark_summary.log"
    by_db = defaultdict(list)
    for r in all_results:
        by_db[r['db_id']].append(r)

    lines = ["=== BIRD Benchmark Summary ===", ""]
    total_correct = total_count = 0
    for db_id in sorted(by_db.keys()):
        results = by_db[db_id]
        c = sum(1 for r in results if r['correct'])
        t = len(results)
        total_correct += c
        total_count += t
        lines.append(f"Database: {db_id} — {c}/{t} ({c/t*100:.1f}%)")
    pct = total_correct / total_count * 100 if total_count else 0
    lines.append(f"\nTotal: {total_correct}/{total_count} ({pct:.1f}%)")

    by_diff = defaultdict(list)
    for r in all_results:
        by_diff[r.get('difficulty', 'unknown')].append(r)
    lines.append("\nBy difficulty:")
    for diff in ["simple", "moderate", "challenging"]:
        results = by_diff.get(diff, [])
        if not results:
            continue
        c = sum(1 for r in results if r['correct'])
        t = len(results)
        lines.append(f"  {diff}: {c}/{t} ({c/t*100:.1f}%)")

    text = "\n".join(lines) + "\n"
    summary_path.write_text(text, encoding="utf-8")
    print(f"\n{text}")


def write_structured_outputs(output_dir: Path, all_results: list[dict]):
    results_dir = output_dir / "results"
    evaluation_dir = output_dir / "evaluation"
    results_dir.mkdir(parents=True, exist_ok=True)
    evaluation_dir.mkdir(parents=True, exist_ok=True)

    (results_dir / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in all_results),
        encoding="utf-8",
    )
    predictions = {
        str(row["question_id"]): row.get("predicted_sql")
        for row in all_results
        if "question_id" in row
    }
    (results_dir / "predictions.json").write_text(
        json.dumps(predictions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    total = len(all_results)
    correct = sum(1 for row in all_results if row.get("correct"))
    by_db = defaultdict(list)
    by_diff = defaultdict(list)
    for row in all_results:
        by_db[row.get("db_id", "unknown")].append(row)
        by_diff[row.get("difficulty") or "unknown"].append(row)
    summary = {
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else 0.0,
        "by_database": {
            db_id: {
                "total": len(rows),
                "correct": sum(1 for row in rows if row.get("correct")),
                "accuracy": sum(1 for row in rows if row.get("correct")) / len(rows) if rows else 0.0,
            }
            for db_id, rows in sorted(by_db.items())
        },
        "by_difficulty": {
            diff: {
                "total": len(rows),
                "correct": sum(1 for row in rows if row.get("correct")),
                "accuracy": sum(1 for row in rows if row.get("correct")) / len(rows) if rows else 0.0,
            }
            for diff, rows in sorted(by_diff.items())
        },
    }
    (evaluation_dir / "evaluation.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = ["# Pontis BIRD Evaluation", "", f"Total: {correct}/{total} ({summary['accuracy'] * 100:.2f}%)", ""]
    lines.append("## By Database")
    for db_id, item in summary["by_database"].items():
        lines.append(f"- {db_id}: {item['correct']}/{item['total']} ({item['accuracy'] * 100:.2f}%)")
    lines.extend(["", "## By Difficulty"])
    for diff, item in summary["by_difficulty"].items():
        lines.append(f"- {diff}: {item['correct']}/{item['total']} ({item['accuracy'] * 100:.2f}%)")
    (evaluation_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ═══════════════════════════════════════════════════════════
#  进度追踪
# ═══════════════════════════════════════════════════════════

class ProgressTracker:
    """线程安全的进度记录器。"""

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

    def start_extract(self, db_id: str):
        with self._lock:
            self._states[db_id]["status"] = "extracting"
            self._states[db_id]["started_at"] = time.time()
            self._write()

    def start_test(self, db_id: str):
        with self._lock:
            self._states[db_id]["status"] = "testing"
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


def cleanup_all(db_base: Path, db_map: dict[str, list], *, train: bool, force_extract: bool = False):
    print("=== Cleanup ===")
    for db_id in sorted(db_map.keys()):
        db_dir = db_base / db_id
        preprocess_dir = get_preprocess_dir(db_id, train)
        bench_dir = get_benchmark_dir(db_id, train)

        if bench_dir.exists():
            count = 0
            for old_log in bench_dir.glob("*.log"):
                old_log.unlink(missing_ok=True)
                count += 1
            if count:
                print(f"  [{db_id}] Cleared {count} logs")

        if force_extract and preprocess_dir.exists():
            import shutil
            shutil.rmtree(preprocess_dir, ignore_errors=True)
            print(f"  [{db_id}] Removed preprocess output for re-extract")

        legacy_pontis_dir = db_dir / ".pontis"
        if legacy_pontis_dir.exists():
            import shutil
            if not force_extract:
                legacy_extract_log = legacy_pontis_dir / "extract.log"
                if legacy_extract_log.exists():
                    preprocess_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(legacy_extract_log), str(preprocess_dir / "extract.log"))
            shutil.rmtree(legacy_pontis_dir, ignore_errors=True)
            print(f"  [{db_id}] Removed legacy data .pontis")
    print("Cleanup done\n")


def cleanup_bird_global(clear_bird_knowledge: bool = False):
    if not clear_bird_knowledge:
        return

    from storage.workspace import Workspace

    print("=== Cleanup bird ===")
    ws = Workspace(active_projects=["bird"])
    rows = ws.cypher("MATCH (n) WHERE n.name != 'README' RETURN n", project="bird")
    total = len(rows)
    if not total:
        print("  [bird] No non-README knowledge nodes to delete")
        print("Cleanup bird done\n")
        return

    ws.cypher("MATCH (n) WHERE n.name != 'README' DELETE n", project="bird")
    print(f"  [bird] Deleted {total} non-README nodes")
    print("Cleanup bird done\n")


def ensure_bird_global_ready(args) -> None:
    """Make --use-bird-global fail fast or populate bird before benchmark."""
    if not getattr(args, "use_bird_global", True):
        return

    from storage.workspace import Workspace
    from scripts.BIRD.sync_bird_global import (
        count_bird_train_examples,
        resolve_train_json_path,
        sync_bird_global,
    )

    ws = Workspace(active_projects=["bird"])
    count = count_bird_train_examples(ws)
    if count > 0:
        print(f"=== bird global ===\n  Found {count} imported train examples\n")
        return

    train_json = resolve_train_json_path(getattr(args, "bird_train_json", None))
    if not getattr(args, "auto_sync_bird_global", True):
        print(
            "Error: --use-bird-global is enabled but bird has 0 imported train examples.\n"
            f"Run: python Pontis/scripts/BIRD/sync_bird_global.py --train-json {train_json}\n"
            "Or pass --no-bird-global."
        )
        sys.exit(1)

    if not train_json.exists():
        print(
            "Error: --use-bird-global is enabled but bird has 0 imported train examples, "
            f"and train.json was not found: {train_json}"
        )
        sys.exit(1)

    print("=== bird global ===")
    print(f"  Empty bird graph; syncing train examples from {train_json}")
    sync_bird_global(
        sync_readme=True,
        import_train=True,
        embed_train=not getattr(args, "no_bird_global_embedding", False),
        train_json=train_json,
    )
    count = count_bird_train_examples(ws)
    if count <= 0:
        print("Error: bird global sync completed but no train examples were imported")
        sys.exit(1)
    print(f"  Ready: {count} imported train examples\n")


# ═══════════════════════════════════════════════════════════
#  单库完整流程
# ═══════════════════════════════════════════════════════════

def run_database(db_id: str, queries: list[dict], db_base: Path,
                 args, tracker: ProgressTracker) -> list[dict]:
    db_dir = db_base / db_id
    print(f"[{db_id}] {len(queries)} queries — start")

    if not db_dir.exists():
        print(f"[{db_id}] Error: directory not found, skipping")
        return []

    # Phase 1: 提取
    if not args.skip_extract:
        tracker.start_extract(db_id)
        from scripts.BIRD.extract import extract_one
        t0 = time.time()
        r = extract_one(
            str(db_dir),
            preprocess_dir=get_preprocess_dir(db_id, args.train),
            force=args.force_extract,
        )
        parts = []
        if r["static"]: parts.append(f"Static {r['static']:.0f}s")
        if r["ai_columns"]: parts.append(f"AI Cols {r['ai_columns']:.0f}s")
        if r["agent"]: parts.append(f"Agent {r['agent']:.0f}s")
        if r.get("embedding"): parts.append(f"Embedding {r['embedding']:.0f}s")
        print(f"[{db_id}] Extract done: {', '.join(parts)}")

    if args.extract_only:
        return []

    # Phase 2: 找数据库文件
    db_path = find_db_file(db_dir)
    if not db_path:
        print(f"[{db_id}] Error: no database file found")
        return []

    # Phase 3: 测试
    bench_dir = get_benchmark_dir(db_id, args.train)
    bench_dir.mkdir(parents=True, exist_ok=True)

    tracker.start_test(db_id)

    def run_one(q: dict) -> dict:
        from agent.config import create_agent, AgentSpec
        from agent.guardrail import build_guardrails

        qid = q.get('question_id', 0)
        collector = TraceCollector()

        spec = AgentSpec(
            mode="readonly",
            effort="max",
            tools=list(BIRD_BENCHMARK_TOOLS),
            prompts=list(BIRD_BENCHMARK_PROMPTS),
        )
        spec.projects = build_agent_projects(db_id, args.use_bird_global)
        spec.guardrails = build_guardrails(spec, BIRD_BENCHMARK_GUARDRAILS)
        agent = create_agent(
            str(db_dir),
            spec,
            trace_callback=collector.callback,
        )
        agent._reflection_collector = collector

        prompt = build_query_prompt(q, args)

        t0 = time.time()
        try:
            response = agent.chat(prompt)
            if extract_sql(response) is None:
                response = force_sql_response(agent, response)
            elapsed = time.time() - t0
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  Q{qid} [{q.get('difficulty', '?')}] ERROR: {e}")
            collector.write_logs(bench_dir, qid, q, "", None, "ERROR", elapsed)
            return {'db_id': db_id, 'question_id': qid, 'difficulty': q.get('difficulty', '?'),
                    'question': q.get('question'), 'evidence': q.get('evidence', ''),
                    'golden_sql': q.get('SQL'), 'predicted_sql': None,
                    'correct': False, 'result': "ERROR", 'elapsed': round(elapsed, 1),
                    'use_bird_global': args.use_bird_global,
                    'prompt_profile': args.prompt_profile,
                    'prompt_file': str(args.prompt_file) if args.prompt_file else None}

        predicted_sql = extract_sql(response)
        golden_result = execute_sql(db_path, q['SQL'])
        predicted_result = execute_sql(db_path, predicted_sql) if predicted_sql else "PARSE_ERROR"
        if (
            predicted_sql
            and isinstance(predicted_result, str)
            and getattr(args, "exec_repair", True)
        ):
            response = repair_exec_error_response(agent, response, predicted_sql, predicted_result)
            repaired_sql = extract_sql(response)
            if repaired_sql and repaired_sql != predicted_sql:
                predicted_sql = repaired_sql
                predicted_result = execute_sql(db_path, predicted_sql)
        correct = is_correct(predicted_result, golden_result)

        result_str = (
            "CORRECT" if correct
            else "PARSE_ERROR" if predicted_sql is None
            else "EXEC_ERROR" if isinstance(predicted_result, str)
            else "WRONG"
        )

        collector.write_logs(bench_dir, qid, q, response, predicted_sql, result_str, elapsed)

        if args.reflection:
            try:
                run_reflection_for_case(
                    db_id=db_id,
                    agent=agent,
                    q=q,
                    predicted_sql=predicted_sql,
                    result_str=result_str,
                    elapsed=elapsed,
                    bench_dir=bench_dir,
                    use_bird_global=args.use_bird_global,
                )
            except Exception as e:
                print(f"  Q{qid} reflection ERROR: {e}")

        status = "OK" if correct else "FAIL"
        print(f"  Q{qid} [{q.get('difficulty', '?')}] {status} {result_str} ({elapsed:.1f}s)")

        return {'db_id': db_id, 'question_id': qid, 'difficulty': q.get('difficulty', '?'),
                'question': q.get('question'), 'evidence': q.get('evidence', ''),
                'golden_sql': q.get('SQL'), 'predicted_sql': predicted_sql,
                'correct': correct, 'result': result_str, 'elapsed': round(elapsed, 1),
                'use_bird_global': args.use_bird_global,
                'prompt_profile': args.prompt_profile,
                'prompt_file': str(args.prompt_file) if args.prompt_file else None}

    db_results = []
    correct_so_far = 0
    done_so_far = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, q): q['question_id'] for q in queries}
        for future in as_completed(futures):
            result = future.result()
            db_results.append(result)
            done_so_far += 1
            if result['correct']:
                correct_so_far += 1
            tracker.update(db_id, done_so_far, correct_so_far)

    db_results.sort(key=lambda r: r['question_id'])
    write_db_summary(bench_dir, db_id, db_results)

    correct_count = sum(1 for r in db_results if r['correct'])
    pct = correct_count / len(queries) * 100 if queries else 0
    print(f"[{db_id}] => {correct_count}/{len(queries)} ({pct:.1f}%)")

    tracker.finish(db_id, correct_count, len(queries))
    return db_results


# ═══════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="BIRD Text-to-SQL Benchmark")
    parser.add_argument("--train", action="store_true", help="跑 train 集（默认跑 dev）")
    parser.add_argument("--db", help="只测试指定数据库；多个库用逗号分隔")
    parser.add_argument("--skip-extract", action="store_true", help="跳过提取")
    parser.add_argument("--force-extract", action="store_true", help="强制重新提取")
    parser.add_argument("--extract-only", action="store_true", help="只提取不测试")
    parser.add_argument("--workers", type=int, default=1, help="每库并行 worker（默认 1）")
    parser.add_argument("--db-workers", type=int, default=1, help="并行数据库数（默认 1）")
    parser.add_argument("--qids", help="只测试指定 question_id，逗号分隔")
    parser.add_argument("--limit", type=int, help="每库最多测试 N 条")
    parser.add_argument("--reflection", action="store_true", help="每题验证后立即运行 reflection，不再读日志二次分析")
    parser.add_argument(
        "--no-exec-repair",
        dest="exec_repair",
        action="store_false",
        default=True,
        help="最终 SQL 执行失败时不追加一次无工具修复",
    )
    parser.add_argument(
        "--use-bird-global",
        dest="use_bird_global",
        action="store_true",
        default=True,
        help="启用 bird 全局经验库（默认）",
    )
    parser.add_argument(
        "--no-bird-global",
        dest="use_bird_global",
        action="store_false",
        help="禁用 bird 全局经验库，只使用当前数据库项目",
    )
    parser.add_argument(
        "--prompt-profile",
        choices=PROMPT_PROFILES,
        default="full",
        help="主求解 prompt 档位：full 保留完整规则，minimal 只保留最小输出协议",
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        help=(
            "使用自定义主求解 prompt 模板；可包含 {question}、{evidence}、"
            "{bird_global_note}、{project_scope}、{bird_global_section}"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for structured results and evaluation summaries.",
    )
    parser.add_argument(
        "--clear-bird-knowledge",
        action="store_true",
        help="运行前清空 bird 全局知识库中除 README 外的所有节点",
    )
    parser.add_argument(
        "--no-auto-sync-bird-global",
        dest="auto_sync_bird_global",
        action="store_false",
        default=True,
        help="启用 bird 全局库但库为空时直接失败，不自动导入 train examples",
    )
    parser.add_argument(
        "--no-bird-global-embedding",
        action="store_true",
        help="自动同步 bird 全局库时只导入 train examples，不生成语义向量",
    )
    parser.add_argument(
        "--bird-train-json",
        type=Path,
        help="用于同步 bird 全局经验库的 BIRD train.json 路径",
    )
    args = parser.parse_args()

    if args.prompt_file and not args.prompt_file.exists():
        print(f"Error: prompt file not found: {args.prompt_file}")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s | %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # 根据数据集选路径
    data_dir = get_data_dir(args.train)
    json_path = data_dir / ("train.json" if args.train else "dev.json")
    db_base = get_db_base(args.train)

    if not json_path.exists():
        print(f"Error: {json_path} not found")
        sys.exit(1)

    data = assign_question_ids(json.loads(json_path.read_text(encoding="utf-8")))
    if args.db:
        db_filter = {x.strip() for x in args.db.split(",") if x.strip()}
        data = [q for q in data if q['db_id'] in db_filter]
    if args.qids:
        qid_set = {int(x.strip()) for x in args.qids.split(",")}
        data = [q for q in data if q["question_id"] in qid_set]

    by_db = defaultdict(list)
    for q in data:
        by_db[q['db_id']].append(q)

    if args.limit:
        for db_id in by_db:
            by_db[db_id] = by_db[db_id][:args.limit]

    total_queries = sum(len(qs) for qs in by_db.values())
    mode_label = "Train" if args.train else "Dev"
    print(f"=== BIRD {mode_label} Benchmark ===")
    print(f"Databases: {len(by_db)}, Queries: {total_queries}")
    print(f"DB workers: {args.db_workers}, Query workers/db: {args.workers}\n")
    print(
        "Config: "
        f"bird_global={'on' if args.use_bird_global else 'off'}, "
        f"prompt_profile={args.prompt_profile}, "
        f"prompt_file={args.prompt_file or '(none)'}\n"
    )

    if args.output_dir is None:
        args.output_dir = get_results_dir(args.train)

    cleanup_all(db_base, by_db, train=args.train, force_extract=args.force_extract)
    if args.clear_bird_knowledge and not args.use_bird_global:
        print("Skip --clear-bird-knowledge because --no-bird-global is set\n")
    else:
        cleanup_bird_global(clear_bird_knowledge=args.clear_bird_knowledge)
    ensure_bird_global_ready(args)

    progress_path = get_progress_path(args.train)
    tracker = ProgressTracker(by_db, progress_path)

    all_results = []
    with ThreadPoolExecutor(max_workers=args.db_workers) as db_pool:
        futures = {
            db_pool.submit(run_database, db_id, queries, db_base, args, tracker): db_id
            for db_id, queries in sorted(by_db.items())
        }
        for future in as_completed(futures):
            db_id = futures[future]
            try:
                db_results = future.result()
                all_results.extend(db_results)
            except Exception as e:
                print(f"[{db_id}] FATAL: {e}")

    if all_results and not args.extract_only:
        all_results.sort(key=lambda r: (r['db_id'], r['question_id']))
        write_total_summary(args.output_dir / "evaluation", all_results)
        write_structured_outputs(args.output_dir, all_results)


if __name__ == "__main__":
    main()
