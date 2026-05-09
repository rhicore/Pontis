#!/usr/bin/env python3
"""BIRD Text-to-SQL Benchmark / Train Runner。

支持 dev 和 train 两种数据集：
  dev:   11 DB, 1534 queries
  train: 69 DB, 9428 queries (--train flag)

每个 query 生成两个日志：
  q{id}.brief.log  简洁版（问题、结果、调用链摘要、guardrail 拦截）
  q{id}.log        详细版（含每轮工具调用的完整参数和返回值）

Usage:
    python -m scripts.BIRD.run_bird_benchmark                       # dev 全量
    python -m scripts.BIRD.run_bird_benchmark --train               # train 全量
    python -m scripts.BIRD.run_bird_benchmark --db toxicology       # 指定库
    python -m scripts.BIRD.run_bird_benchmark --train --skip-extract
    python -m scripts.BIRD.run_bird_benchmark --train --limit 10    # 每库限 10 条
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

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DB_EXTS = (".sqlite", ".db", ".sqlite3", ".duckdb")

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

要求：
- 第一步先用 glob("*") 查看项目有哪些文件，找到数据库文件（.sqlite/.db/.duckdb 等），不要假设文件名
- 然后依次探索：glob 数据库内的 .table/.col/.fk/.rel/.overlap 实体，meta 确认列语义
- 充分理解 schema 和关系后再写 SQL，不要盲猜列名或 JOIN 条件
- 只输出一条 SELECT 语句，用 ```sql ``` 代码块包裹
- 不要解释，只输出 SQL
- 注意 SQLite 语法：字符串用单引号，列名有特殊字符时用反引号或双引号
"""


# ═══════════════════════════════════════════════════════════
#  Trace 收集 + 两级日志
# ═══════════════════════════════════════════════════════════

class TraceCollector:
    """收集 agent 事件，生成简洁版和详细版日志。"""

    def __init__(self):
        self._round = 0
        self._rounds = []  # [{round, calls: [{name, args, result}], blocks: [{source, msg}]}]
        self._current_calls = []
        self._current_blocks = []

    def callback(self, event: dict):
        etype = event.get("type")

        if etype == "tool_call":
            self._current_calls.append({
                "name": event["name"],
                "args": event.get("arguments", {}),
                "result": None,
            })
        elif etype == "tool_result":
            result = event.get("result", "")
            # 填入最近的同名无结果调用
            for tc in reversed(self._current_calls):
                if tc["name"] == event.get("name") and tc["result"] is None:
                    tc["result"] = result
                    break
            # 这轮结束，记录并推进
            self._rounds.append({
                "round": self._round,
                "calls": self._current_calls,
                "blocks": self._current_blocks,
            })
            self._current_calls = []
            self._current_blocks = []
            self._round += 1
        elif etype == "blocked":
            self._current_blocks.append({
                "source": event.get("guardrail", ""),
                "msg": event.get("content", ""),
                "tool": event.get("call_index"),
            })
        elif etype == "warning":
            pass
        elif etype == "done":
            # 如果还有未关闭的调用
            if self._current_calls:
                self._rounds.append({
                    "round": self._round,
                    "calls": self._current_calls,
                    "blocks": self._current_blocks,
                })

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

        # ── 调用链摘要（简洁版用） ──
        call_summary_parts = []
        block_summary_parts = []
        for rd in self._rounds:
            for tc in rd["calls"]:
                name = tc["name"]
                args_str = _args_brief(tc["args"])
                suffix = "(blocked)" if any(
                    b["tool"] is not None for b in rd["blocks"]
                ) else ""
                call_summary_parts.append(f"{name}({args_str}){suffix}")
            for bl in rd["blocks"]:
                block_summary_parts.append(f"[{bl['source']}] {bl['msg'][:80]}")

        call_summary = " → ".join(call_summary_parts) if call_summary_parts else "(no calls)"
        block_summary = "\n".join(block_summary_parts) if block_summary_parts else ""

        # ── 简洁版 ──
        brief_lines = [header, f"Calls: {call_summary}"]
        if block_summary:
            brief_lines.append(f"Blocks:\n{block_summary}")
        brief_lines.append("")  # trailing newline
        (bench_dir / f"q{qid}.brief.log").write_text("\n".join(brief_lines), encoding="utf-8")

        # ── 详细版 ──
        detail_lines = [header, "---"]
        for rd in self._rounds:
            for tc in rd["calls"]:
                args_full = json.dumps(tc["args"], ensure_ascii=False) if tc["args"] else "{}"
                detail_lines.append(f"Round {rd['round']} | {tc['name']}({args_full})")
                result = tc["result"] or "(no result)"
                # 截断过长结果
                if len(result) > 500:
                    result = result[:500] + "..."
                detail_lines.append(f"  {result}")
            for bl in rd["blocks"]:
                detail_lines.append(f"  [BLOCKED by {bl['source']}] {bl['msg']}")
            detail_lines.append("---")

        if response:
            detail_lines.append(f"Agent response:\n{response[-1000:]}")
        detail_lines.append("")
        (bench_dir / f"q{qid}.log").write_text("\n".join(detail_lines), encoding="utf-8")


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


# ═══════════════════════════════════════════════════════════
#  辅助
# ═══════════════════════════════════════════════════════════

def find_db_file(db_dir: Path) -> str | None:
    for ext in DB_EXTS:
        matches = list(db_dir.glob(f"*{ext}"))
        if matches:
            return str(matches[0])
    return None


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

        agent = create_agent(
            str(db_dir),
            AgentSpec(mode="benchmark", effort="max"),
            trace_callback=collector.callback,
        )

        prompt = QUERY_PROMPT_TEMPLATE.format(
            question=q['question'],
            evidence=q.get('evidence', '') or "(无额外提示)",
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
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s | %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # 根据数据集选路径
    if args.train:
        data_dir = PROJECT_ROOT / "example_data" / "bird_train"
        json_path = data_dir / "train.json"
        db_base = data_dir / "train_databases"
    else:
        data_dir = PROJECT_ROOT / "example_data" / "bird"
        json_path = data_dir / "dev.json"
        db_base = data_dir / "dev_databases"

    if not json_path.exists():
        print(f"Error: {json_path} not found")
        sys.exit(1)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    if args.db:
        data = [q for q in data if q['db_id'] == args.db]
    if args.qids:
        qid_set = {int(x.strip()) for x in args.qids.split(",")}
        data = [q for q in data if q.get('question_id') in qid_set]

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
