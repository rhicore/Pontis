#!/usr/bin/env python3
"""BIRD Text-to-SQL Benchmark — 评测 Pontis agent 在 BIRD 数据集上的准确率。

流程：
  1. 加载 dev.json，按 --db 筛选
  2. 对每个数据库：调用 bird_extract.extract_one() 提取（除非 --skip-extract）
  3. 创建 readonly PontisAgent，逐条发送问题
  4. 从 agent 回复提取 SQL，执行并与 golden SQL 比对
  5. 记录日志 + 计算准确率

并行策略：
  - 数据库级别并行：--db-workers 个数据库同时跑（提取+测试）
  - Query 级别并行：每个库内 --workers 个 query 同时跑
  - 总线程数 ≈ db_workers × query_workers，注意 API 限流

Usage:
    python -m utils.run_bird_benchmark                       # 全量
    python -m utils.run_bird_benchmark --db toxicology        # 指定库
    python -m utils.run_bird_benchmark --skip-extract         # 跳过提取
    python -m utils.run_bird_benchmark --force-extract        # 强制重新提取
    python -m utils.run_bird_benchmark --extract-only         # 只提取不测试
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

# ═══════════════════════════════════════════════════════════
#  路径
# ═══════════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIRD_DIR = PROJECT_ROOT / "example_data" / "bird"
DEV_JSON = BIRD_DIR / "dev.json"
DB_BASE = BIRD_DIR / "dev_databases"
PROGRESS_LOG = BIRD_DIR / "progress.log"

DB_EXTS = (".sqlite", ".db", ".sqlite3", ".duckdb")

# ═══════════════════════════════════════════════════════════
#  SQL 提取与执行
# ═══════════════════════════════════════════════════════════

_SQL_BLOCK_RE = re.compile(r"```sql\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
_SELECT_RE = re.compile(r"(SELECT\s.+?)(?:;|$)", re.DOTALL | re.IGNORECASE)


def extract_sql(text: str) -> str | None:
    """从 agent 回复中提取 SQL。"""
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
    """执行 SQL，返回结果集合或错误字符串。"""
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
#  Prompt
# ═══════════════════════════════════════════════════════════

# Benchmark addon 已移至 agent/prompt/_benchmark.py，通过 build_prompt("benchmark") 组装

QUERY_PROMPT_TEMPLATE = """\
请根据以下信息生成一条 SQLite SQL 查询。

问题：{question}

提示：{evidence}

要求：
- 第一步先用 glob("*") 查看项目有哪些文件，找到数据库文件（.sqlite/.db/.duckdb 等），不要假设文件名
- 然后依次探索：glob 数据库内的 .table/.col/.fk/.rel/.overlap 实体，read meta 确认列语义
- 充分理解 schema 和关系后再写 SQL，不要盲猜列名或 JOIN 条件
- 只输出一条 SELECT 语句，用 ```sql ``` 代码块包裹
- 不要解释，只输出 SQL
- 注意 SQLite 语法：字符串用单引号，列名有特殊字符时用反引号或双引号
"""


def build_query_prompt(question: str, evidence: str) -> str:
    return QUERY_PROMPT_TEMPLATE.format(
        question=question,
        evidence=evidence or "(无额外提示)",
    )


def build_benchmark_system_prompt(project_path: str) -> str:
    """构建 benchmark 模式的系统提示词（max effort + benchmark 专用指令）。"""
    from agent.agent import AgentSpec
    from agent.prompt import build_prompt
    return build_prompt(AgentSpec(mode="benchmark", effort="max", project_path=project_path))


def create_benchmark_agent(project_path: str, logger_name: str = None) -> "PontisAgent":
    """创建 benchmark 专用的 agent（readonly + max effort = 充分探索，无轮次限制）。"""
    from agent.agent import create_agent, AgentSpec
    return create_agent(project_path, AgentSpec(mode="benchmark", effort="max"),
                        logger_name=logger_name)


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
#  日志
# ═══════════════════════════════════════════════════════════

class _ThreadFilter(logging.Filter):
    """只放行创建时所在线程的日志记录，避免并行时日志串文件。"""
    def __init__(self):
        super().__init__()
        self._tid = threading.get_ident()

    def filter(self, record):
        return threading.get_ident() == self._tid


def setup_query_log(log_path: str) -> logging.FileHandler:
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.addFilter(_ThreadFilter())
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(name)s | %(message)s", "%H:%M:%S"))
    logging.getLogger().addHandler(fh)
    return fh


def teardown_log(fh: logging.FileHandler) -> None:
    logging.getLogger().removeHandler(fh)
    fh.close()


def append_query_result(log_path: Path, q: dict, response: str, predicted_sql: str | None,
                        result: str, pred_rows: int, gold_rows: int, elapsed: float):
    lines = [
        "",
        "=" * 60,
        f"Question: {q['question']}",
        f"Evidence: {q.get('evidence', '')}",
        f"Difficulty: {q.get('difficulty', '?')}",
        f"Golden SQL: {q['SQL']}",
        f"Predicted SQL: {predicted_sql or 'PARSE_ERROR'}",
        f"Agent response (last 500 chars): {response[-500:] if response else 'EMPTY'}",
        f"Result: {result}",
        f"Predicted rows: {pred_rows}",
        f"Golden rows: {gold_rows}",
        f"Time: {elapsed:.1f}s",
        "=" * 60,
    ]
    log_path.write_text(log_path.read_text(encoding="utf-8") + "\n".join(lines) + "\n", encoding="utf-8")


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


def write_total_summary(all_results: list[dict]):
    summary_path = BIRD_DIR / "benchmark_summary.log"
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
#  全局清理 + 进度追踪
# ═══════════════════════════════════════════════════════════

class ProgressTracker:
    """线程安全的进度记录器，写入 bird/progress.log。"""

    def __init__(self, db_map: dict[str, list]):
        self._lock = threading.Lock()
        self._states: dict[str, dict] = {
            db_id: {
                "total": len(qs),
                "status": "pending",
                "done": 0,
                "correct": 0,
                "started_at": None,
                "finished_at": None,
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
        lines = [f"=== BIRD Benchmark Progress — {time.strftime('%Y-%m-%d %H:%M:%S')} ===", ""]
        total_done = sum(s["done"] for s in self._states.values())
        total_queries = sum(s["total"] for s in self._states.values())
        total_correct = sum(s["correct"] for s in self._states.values())
        lines.append(f"Overall: {total_done}/{total_queries} queries, {total_correct} correct ({total_correct/total_queries*100:.1f}% if all done)")
        lines.append("")
        for db_id in sorted(self._states.keys()):
            s = self._states[db_id]
            pct = s["done"] / s["total"] * 100 if s["total"] else 0
            elapsed = ""
            if s["started_at"] and s["status"] != "done":
                elapsed = f" ({time.time() - s['started_at']:.0f}s elapsed)"
            elif s["started_at"] and s["finished_at"]:
                elapsed = f" ({s['finished_at'] - s['started_at']:.0f}s)"
            lines.append(
                f"  [{s['status']:>10}] {db_id:25s} "
                f"{s['done']:>4}/{s['total']:<4} ({pct:5.1f}%) "
                f"correct={s['correct']}{elapsed}"
            )
        lines.append("")
        PROGRESS_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cleanup_all(db_map: dict[str, list], force_extract: bool = False):
    """在最开始统一清理所有旧日志和旧提取数据。"""
    print("=== Cleanup ===")
    for db_id in sorted(db_map.keys()):
        db_dir = DB_BASE / db_id
        pontis_dir = db_dir / ".pontis"
        bench_dir = pontis_dir / "benchmark"

        # 清理 benchmark 旧日志
        if bench_dir.exists():
            count = 0
            for old_log in bench_dir.glob("*.log"):
                old_log.unlink(missing_ok=True)
                count += 1
            if count:
                print(f"  [{db_id}] Cleared {count} benchmark logs")

        # 清理旧的提取数据（强制重新提取）
        if force_extract and pontis_dir.exists():
            import shutil
            shutil.rmtree(pontis_dir, ignore_errors=True)
            print(f"  [{db_id}] Removed .pontis for re-extract")

    print("Cleanup done\n")


# ═══════════════════════════════════════════════════════════
#  单库完整流程
# ═══════════════════════════════════════════════════════════

def run_database(db_id: str, queries: list[dict], args, tracker: ProgressTracker) -> list[dict]:
    """单个数据库的完整流程：提取 + 测试。可在独立线程中运行。

    Returns:
        该库的所有 query 结果列表
    """
    db_dir = DB_BASE / db_id
    print(f"[{db_id}] {len(queries)} queries — start")

    if not db_dir.exists():
        print(f"[{db_id}] Error: directory not found, skipping")
        return []

    # Phase 1: 提取
    if not args.skip_extract:
        tracker.start_extract(db_id)
        from extractor.bird_extract import extract_one
        t0 = time.time()
        r = extract_one(str(db_dir), force=args.force_extract)
        elapsed_extract = time.time() - t0
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

    # Phase 3: 并行测试
    bench_dir = db_dir / ".pontis" / "benchmark"
    bench_dir.mkdir(parents=True, exist_ok=True)

    tracker.start_test(db_id)

    def run_one(q: dict) -> dict:
        """单条 query 测试（每个线程独立 agent + 独立 logger）。"""
        qid = q['question_id']
        q_log = bench_dir / f"q{qid}.log"

        # 每条 query 用独立的 logger + handler，彻底隔离
        logger_name = f"benchmark.q{qid}"
        q_logger = logging.getLogger(logger_name)
        fh = logging.FileHandler(str(q_log), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(name)s | %(message)s", "%H:%M:%S"))
        q_logger.addHandler(fh)
        q_logger.setLevel(logging.DEBUG)

        try:
            agent = create_benchmark_agent(str(db_dir), logger_name=logger_name)

            prompt = build_query_prompt(q['question'], q.get('evidence', ''))
            t0 = time.time()
            response = agent.chat(prompt)
            elapsed = time.time() - t0

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
            pred_rows = len(predicted_result) if isinstance(predicted_result, set) else 0
            gold_rows = len(golden_result) if isinstance(golden_result, set) else 0

            append_query_result(q_log, q, response, predicted_sql, result_str,
                                pred_rows, gold_rows, elapsed)

            status = "OK" if correct else "FAIL"
            print(f"  Q{qid} [{q.get('difficulty', '?')}] {status} {result_str} ({elapsed:.1f}s)")

            return {'db_id': db_id, 'question_id': qid, 'difficulty': q.get('difficulty', '?'),
                    'correct': correct, 'result': result_str, 'elapsed': elapsed}
        except Exception as e:
            print(f"  Q{qid} [{q.get('difficulty', '?')}] ERROR: {e}")
            append_query_result(q_log, q, "", None, "ERROR", 0, 0, 0)
            return {'db_id': db_id, 'question_id': qid, 'difficulty': q.get('difficulty', '?'),
                    'correct': False, 'result': "ERROR", 'elapsed': 0}
        finally:
            q_logger.removeHandler(fh)
            fh.close()
            logging.getLogger(logger_name).handlers.clear()

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

    # 按 question_id 排序
    db_results.sort(key=lambda r: r['question_id'])

    # 写库级 summary
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
    parser.add_argument("--db", help="只测试指定数据库")
    parser.add_argument("--skip-extract", action="store_true", help="跳过提取，直接测试")
    parser.add_argument("--force-extract", action="store_true", help="强制重新提取（删除旧 .pontis）")
    parser.add_argument("--extract-only", action="store_true", help="只提取不测试")
    parser.add_argument("--workers", type=int, default=4, help="每个库内并行 query worker 数量（默认 4）")
    parser.add_argument("--db-workers", type=int, default=3, help="并行数据库数量（默认 3）")
    parser.add_argument("--qids", help="只测试指定 question_id，逗号分隔（如 847,849,851）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s | %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if not DEV_JSON.exists():
        print(f"Error: {DEV_JSON} not found")
        sys.exit(1)

    dev_data = json.loads(DEV_JSON.read_text(encoding="utf-8"))
    if args.db:
        dev_data = [q for q in dev_data if q['db_id'] == args.db]
    if args.qids:
        qid_set = {int(x.strip()) for x in args.qids.split(",")}
        dev_data = [q for q in dev_data if q['question_id'] in qid_set]

    by_db = defaultdict(list)
    for q in dev_data:
        by_db[q['db_id']].append(q)

    total_queries = sum(len(qs) for qs in by_db.values())
    print(f"=== BIRD Benchmark ===")
    print(f"Databases: {len(by_db)}, Queries: {total_queries}")
    print(f"DB workers: {args.db_workers}, Query workers/db: {args.workers}")
    print(f"Max concurrent threads: ~{args.db_workers * args.workers}\n")

    # 全局清理：在最开始就删除所有旧日志和旧提取数据
    cleanup_all(by_db, force_extract=args.force_extract)

    # 创建进度追踪器
    tracker = ProgressTracker(by_db)

    all_results = []

    # 数据库级别并行
    with ThreadPoolExecutor(max_workers=args.db_workers) as db_pool:
        futures = {
            db_pool.submit(run_database, db_id, queries, args, tracker): db_id
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
        # 按 db_id + question_id 排序
        all_results.sort(key=lambda r: (r['db_id'], r['question_id']))
        write_total_summary(all_results)


if __name__ == "__main__":
    main()
