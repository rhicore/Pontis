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

from scripts.BIRD.common import PROJECT_ROOT, get_data_dir, get_db_base

logger = logging.getLogger(__name__)

DB_EXTS = (".sqlite", ".db", ".sqlite3", ".duckdb")


def get_bird_shared_workflow() -> str:
    """BIRD 场景下共享的解题/审题入口。"""
    return """\
1. 当前打开项目的 README 正文已经在系统提示词中给出；先按这些 README 理解项目角色、schema 探索纪律和 `bird` 的使用方式。
2. 对数据库项目，先理解 schema、列语义、关系和消歧信息；`query` 只用于验证，不要拿来代替 schema 探索。
3. 在输出最终 SQL 之前，至少浏览一次 `bird` 的知识实体总表；推荐先用 `glob("bird::*:knowledge")`，但把它当索引页，不要靠翻很多页硬扫。
4. 优先读抽象知识实体：`knowledge:convention`、`knowledge:pattern`、`knowledge:lesson`、`knowledge:term`；只有当这些抽象知识仍不足以支持判断时，才继续看 `knowledge:example`。
5. 如果 `bird` 总表候选很多，不要随机开 example，也不要顺着 offset 一页页扫；先用 `search(ref="bird::*:knowledge", query="...")` 缩到 1-3 个最相关实体，再用 `meta` 深读。搜索词优先用题目里的核心名词、evidence 里的公式词、以及你怀疑的错误模式词。
6. 其余解题流程、审题约束和常见错误，统一遵循系统提示词中的项目 README，尤其是 `bird::README`。"""


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
    m = _SQL_BLOCK_RE.search(text)
    if m:
        sql = m.group(1).strip()
        if sql:
            return sql
    m = _SELECT_RE.search(text)
    if m:
        return m.group(1).strip()
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
#  Query Prompt
# ═══════════════════════════════════════════════════════════

QUERY_PROMPT_TEMPLATE = """\
请根据以下信息生成一条 SQLite SQL 查询。

问题：{question}

提示：{evidence}

解题工作流：
{shared_workflow}

本题要求：
- 只输出一条 SELECT 语句，用 ```sql ``` 代码块包裹
- 不要解释，只输出 SQL
- 注意 SQLite 语法：字符串用单引号，列名有特殊字符时用反引号或双引号；如果复合查询的每个分支都需要各自 `ORDER BY / LIMIT`，先放进 CTE / 子查询，再在外层 `UNION / UNION ALL`

严格遵循 evidence：
- evidence 给出的列名映射 → 优先使用
- evidence 给出的计算公式 → **严格翻译为 SQL**，不要简化或改写
- evidence 给出的条件值 → 直接使用，不要猜测其他值
- 若题目出现 `the X which is cited / used / ordered ... most/least` 这类限制性定语从句，先把候选集合限制为真正参与该关系的实体；不要为了找最少值而把 `0` 次实体拉进候选，除非题目明确要求包含 zero / none / never
- `majority` / `most of` 这类“多数/大多数”表达，默认先理解为分布/占比，而不是单一极值；优先 `GROUP BY`，不要机械加 `ORDER BY COUNT(*) DESC LIMIT 1`
- 其余审题与纠错规则，统一遵循系统提示词中的项目 README，尤其是 `bird::README`

关于 `bird` 经验的使用：
- 最终输出 SQL 前，至少先浏览一次 `bird` 的知识实体总表，确认是否存在相关经验
- 优先读取抽象知识实体，也就是：`knowledge:convention` / `knowledge:pattern` / `knowledge:lesson` / `knowledge:term`
- 其中：`convention` 表示应遵循或避免的规则，`pattern` 表示可复用的通用解法，`lesson` 表示已经总结出的错误模式，`term` 表示辅助理解题意或知识节点的术语/概念说明
- 如果总表过长，先用 `search(ref="bird::*:knowledge", query="...")` 缩小候选，再读 `meta`；不要把翻页扫完整个 `bird` 当成默认动作
- `knowledge:example` 放在后面；只有当抽象知识仍不足以支持判断时，才把它当作解释型案例阅读
- 不要机械照抄 example 里的 SQL、表名、列名或字面值
- 如果先看到某个 example，也要回头优先查看它相连的抽象知识，再决定是否参考这个案例

输出协议：
- 回复中只包含一个 ```sql``` 代码块和一条 SELECT 语句，代码块前后不要有任何文字
- 多个值用单列多行输出，不要横向展开为多列，不要用 GROUP_CONCAT 合并
"""


REFLECTION_CASE_PROMPT_TEMPLATE = """\
你现在仍在同一个对话上下文里：刚刚的 benchmark 消息、工具调用和最终 SQL 都还在。
你不是新开一个会话，而是继续复盘这条已经完成并已验证结果的 benchmark case。

先按 benchmark 的解题工作流回放这道题，再判断做对/做错的真正根因，然后只在必要时更新 `bird`。解题/审题工作流统一以系统提示词中的项目 README 为准，不在这里重复展开。

回放信息：
{shared_workflow}

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
1. 先在 `bird` 里找最相关的已有知识，优先看抽象知识实体；不要一上来就新建。
2. 若找到相关实体，优先判断它是应保持不动、补充更新，还是局部修正；默认优先 `update`，不要重复 `create`。
3. 只有在确认不存在合适的已有实体时，且结论明显可跨库迁移，才新建 `bird::<short_name>:knowledge:<type>`。
4. 只允许写 `knowledge:convention` / `knowledge:pattern` / `knowledge:lesson` / `knowledge:example`；不要新建 `knowledge:term`。
5. 若写 `knowledge:example`，它必须是“解释型 benchmark case”，不是裸 few-shot，也不是只存 question + golden SQL。
6. `knowledge:example` 至少应包含：`question`、必要 `evidence`、`golden_sql`、`db_id`、`question_id`、`difficulty`、`schema_background`、`bird_bias`、`why_this_case_matters`、`transfer_hint`。
7. 如果该题本轮做错了，但你仍决定沉淀为 `knowledge:example`，还应补：`predicted_sql`、`error_type`、`mistake_summary`、`wrong_assumption`、`fix_hint`。
8. `knowledge:example` 允许包含具体数据库/表/列信息，但必须服务于“解释 BIRD 偏好”；不要让它退化成无解释的样题堆积。
9. 不要写入完整推理过程、原始 chain-of-thought 或逐轮自言自语；如果需要保留思路，只保留高密度摘要，例如 `decision_summary`、`mistake_summary`、`verification_note`、`rejected_alternatives`。
10. `knowledge:example` 不能作为孤立案例存在。只要决定保留或新建 example，就必须把它与对应的抽象知识实体建立普通图边。
11. 这里的“对应抽象知识实体”明确指与该 example 对应的 `knowledge:convention` / `knowledge:pattern` / `knowledge:lesson` / `knowledge:term`。如果对应抽象知识不存在，可以先补抽象知识，再连边；但新建类型仍然只允许 `convention / pattern / lesson / example`。
12. `knowledge:convention` / `knowledge:pattern` / `knowledge:lesson` 仍应尽量去 schema 化，不要把具体字段名直接写成通用规则。
13. 如果只是执行流程失误、没有新的跨库经验缺口，也没有值得沉淀的 BIRD 偏好案例，明确说明“不写入任何知识实体”。
14. 写任何新实体前，先明确说明你检查过哪些最相关的已有实体、为什么它们不够、为什么必须新建；如果这个论证不成立，就不要新建。
"""


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


def build_reflection_case_prompt(db_id: str, q: dict, collector: TraceCollector,
                                 predicted_sql: str | None, result_str: str,
                                 elapsed: float) -> str:
    return REFLECTION_CASE_PROMPT_TEMPLATE.format(
        shared_workflow=get_bird_shared_workflow(),
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
                            elapsed: float, bench_dir: Path) -> None:
    from agent.config import AgentSpec, resolve_mode
    from agent.prompt import build_prompt
    from agent.tools import build_registry

    reflection_spec = AgentSpec(mode="reflection", effort="max")
    reflection_spec.projects = [db_id, "bird"]
    resolve_mode(reflection_spec)

    # 方案 1：沿用同一个 agent 会话，只在反思阶段切到 reflection 配置。
    agent.tools = build_registry(reflection_spec)
    agent.system_prompt = build_prompt(reflection_spec)
    agent.guardrails = reflection_spec.guardrails
    agent.messages[0]["content"] = agent.system_prompt

    prompt = build_reflection_case_prompt(
        db_id=db_id,
        q=q,
        collector=agent._reflection_collector,
        predicted_sql=predicted_sql,
        result_str=result_str,
        elapsed=elapsed,
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


def cleanup_all(db_base: Path, db_map: dict[str, list], force_extract: bool = False):
    print("=== Cleanup ===")
    for db_id in sorted(db_map.keys()):
        db_dir = db_base / db_id
        pontis_dir = db_dir / ".pontis"
        bench_dir = pontis_dir / "benchmark"

        if bench_dir.exists():
            count = 0
            for old_log in bench_dir.glob("*.log"):
                old_log.unlink(missing_ok=True)
                count += 1
            if count:
                print(f"  [{db_id}] Cleared {count} logs")

        if force_extract and pontis_dir.exists():
            import shutil
            shutil.rmtree(pontis_dir, ignore_errors=True)
            print(f"  [{db_id}] Removed .pontis for re-extract")
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
        r = extract_one(str(db_dir), force=args.force_extract)
        parts = []
        if r["static"]: parts.append(f"Static {r['static']:.0f}s")
        if r["ai_columns"]: parts.append(f"AI Cols {r['ai_columns']:.0f}s")
        if r["agent"]: parts.append(f"Agent {r['agent']:.0f}s")
        print(f"[{db_id}] Extract done: {', '.join(parts)}")

    if args.extract_only:
        return []

    # Phase 2: 找数据库文件
    db_path = find_db_file(db_dir)
    if not db_path:
        print(f"[{db_id}] Error: no database file found")
        return []

    # Phase 3: 测试
    bench_dir = db_dir / ".pontis" / "benchmark"
    bench_dir.mkdir(parents=True, exist_ok=True)

    tracker.start_test(db_id)

    def run_one(q: dict) -> dict:
        from agent.agent import create_agent, AgentSpec

        qid = q.get('question_id', 0)
        collector = TraceCollector()

        spec = AgentSpec(mode="benchmark", effort="max")
        spec.projects = [db_id, "bird"]
        agent = create_agent(
            str(db_dir),
            spec,
            trace_callback=collector.callback,
        )
        agent._reflection_collector = collector

        prompt = QUERY_PROMPT_TEMPLATE.format(
            question=q['question'],
            evidence=q.get('evidence', '') or "(无额外提示)",
            shared_workflow=get_bird_shared_workflow(),
        )

        t0 = time.time()
        try:
            response = agent.chat(prompt)
            elapsed = time.time() - t0
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  Q{qid} [{q.get('difficulty', '?')}] ERROR: {e}")
            collector.write_logs(bench_dir, qid, q, "", None, "ERROR", elapsed)
            return {'db_id': db_id, 'question_id': qid, 'difficulty': q.get('difficulty', '?'),
                    'correct': False, 'result': "ERROR", 'elapsed': round(elapsed, 1)}

        predicted_sql = extract_sql(response)
        golden_result = execute_sql(db_path, q['SQL'])
        predicted_result = execute_sql(db_path, predicted_sql) if predicted_sql else "PARSE_ERROR"
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
                )
            except Exception as e:
                print(f"  Q{qid} reflection ERROR: {e}")

        status = "OK" if correct else "FAIL"
        print(f"  Q{qid} [{q.get('difficulty', '?')}] {status} {result_str} ({elapsed:.1f}s)")

        return {'db_id': db_id, 'question_id': qid, 'difficulty': q.get('difficulty', '?'),
                'correct': correct, 'result': result_str, 'elapsed': round(elapsed, 1)}

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
            if done_so_far % 5 == 0 or done_so_far == len(queries):
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
    parser.add_argument("--db", help="只测试指定数据库")
    parser.add_argument("--skip-extract", action="store_true", help="跳过提取")
    parser.add_argument("--force-extract", action="store_true", help="强制重新提取")
    parser.add_argument("--extract-only", action="store_true", help="只提取不测试")
    parser.add_argument("--workers", type=int, default=4, help="每库并行 worker（默认 4）")
    parser.add_argument("--db-workers", type=int, default=3, help="并行数据库数（默认 3）")
    parser.add_argument("--qids", help="只测试指定 question_id，逗号分隔")
    parser.add_argument("--limit", type=int, help="每库最多测试 N 条")
    parser.add_argument("--reflection", action="store_true", help="每题验证后立即运行 reflection，不再读日志二次分析")
    parser.add_argument(
        "--clear-bird-knowledge",
        action="store_true",
        help="运行前清空 bird 全局知识库中除 README 外的所有节点",
    )
    args = parser.parse_args()

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
        data = [q for q in data if q['db_id'] == args.db]
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

    cleanup_all(db_base, by_db, force_extract=args.force_extract)
    cleanup_bird_global(clear_bird_knowledge=args.clear_bird_knowledge)

    progress_path = data_dir / "progress.log"
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
        write_total_summary(data_dir, all_results)


if __name__ == "__main__":
    main()
