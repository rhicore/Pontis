#!/usr/bin/env python3
"""BIRD Text-to-SQL Benchmark — 评测 Pontis agent 在 BIRD 数据集上的准确率。

流程：
  1. 加载 dev.json，按 --db 筛选
  2. 对每个数据库：调用 bird_extract.extract_one() 提取（除非 --skip-extract）
  3. 创建 readonly PontisAgent，逐条发送问题
  4. 从 agent 回复提取 SQL，执行并与 golden SQL 比对
  5. 记录日志 + 计算准确率

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
import time
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
#  路径
# ═══════════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIRD_DIR = PROJECT_ROOT / "example_data" / "bird"
DEV_JSON = BIRD_DIR / "dev.json"
DB_BASE = BIRD_DIR / "dev_databases"

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
- 先用工具（glob、meta 等）了解数据库结构，再生成 SQL
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
    """构建 benchmark 模式的系统提示词（readonly + SQL + benchmark 专用指令）。"""
    from agent.prompt import build_prompt
    return build_prompt("benchmark", project_path)


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

def setup_query_log(log_path: str) -> logging.FileHandler:
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
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
    for r in results:
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
#  主流程
# ═══════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="BIRD Text-to-SQL Benchmark")
    parser.add_argument("--db", help="只测试指定数据库")
    parser.add_argument("--skip-extract", action="store_true", help="跳过提取，直接测试")
    parser.add_argument("--force-extract", action="store_true", help="强制重新提取（删除旧 .pontis）")
    parser.add_argument("--extract-only", action="store_true", help="只提取不测试")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s | %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if not DEV_JSON.exists():
        print(f"Error: {DEV_JSON} not found")
        sys.exit(1)

    dev_data = json.loads(DEV_JSON.read_text(encoding="utf-8"))
    if args.db:
        dev_data = [q for q in dev_data if q['db_id'] == args.db]

    by_db = defaultdict(list)
    for q in dev_data:
        by_db[q['db_id']].append(q)

    print(f"=== BIRD Benchmark ===")
    print(f"Databases: {len(by_db)}, Queries: {len(dev_data)}\n")

    all_results = []

    for db_id, queries in sorted(by_db.items()):
        db_dir = DB_BASE / db_id
        print(f"[{db_id}] {len(queries)} queries")

        if not db_dir.exists():
            print(f"  Error: {db_dir} not found, skipping")
            continue

        # Phase 1: 提取（交给 bird_extract）
        if not args.skip_extract:
            from extractor.bird_extract import extract_one
            t0 = time.time()
            r = extract_one(str(db_dir), force=args.force_extract)
            elapsed_extract = time.time() - t0
            parts = []
            if r["static"]: parts.append(f"Static {r['static']:.0f}s")
            if r["ai_columns"]: parts.append(f"AI Cols {r['ai_columns']:.0f}s")
            if r["agent"]: parts.append(f"Agent {r['agent']:.0f}s")
            print(f"  Extract: {', '.join(parts)}")

        if args.extract_only:
            continue

        # Phase 2: 找数据库文件
        db_path = find_db_file(db_dir)
        if not db_path:
            print(f"  Error: no database file found")
            continue

        # Phase 3: 测试
        bench_dir = db_dir / ".pontis" / "benchmark"
        bench_dir.mkdir(parents=True, exist_ok=True)

        from agent.agent import PontisAgent
        agent = PontisAgent(str(db_dir), system_prompt=build_benchmark_system_prompt(str(db_dir)))

        db_results = []
        correct_count = 0

        for q in queries:
            qid = q['question_id']
            q_log = bench_dir / f"q{qid}.log"

            fh = setup_query_log(str(q_log))

            prompt = build_query_prompt(q['question'], q.get('evidence', ''))
            t0 = time.time()
            response = agent.chat(prompt)
            elapsed = time.time() - t0

            predicted_sql = extract_sql(response)

            golden_result = execute_sql(db_path, q['SQL'])
            predicted_result = execute_sql(db_path, predicted_sql) if predicted_sql else "PARSE_ERROR"

            correct = is_correct(predicted_result, golden_result)
            if correct:
                correct_count += 1

            result_str = (
                "CORRECT" if correct
                else "PARSE_ERROR" if predicted_sql is None
                else "EXEC_ERROR" if isinstance(predicted_result, str)
                else "WRONG"
            )
            pred_rows = len(predicted_result) if isinstance(predicted_result, set) else 0
            gold_rows = len(golden_result) if isinstance(golden_result, set) else 0

            teardown_log(fh)
            append_query_result(q_log, q, response, predicted_sql, result_str,
                                pred_rows, gold_rows, elapsed)

            status = "OK" if correct else "FAIL"
            print(f"  Q{qid} [{q.get('difficulty', '?')}] {status} {result_str} ({elapsed:.1f}s)")

            r = {'db_id': db_id, 'question_id': qid, 'difficulty': q.get('difficulty', '?'),
                 'correct': correct, 'result': result_str, 'elapsed': elapsed}
            db_results.append(r)
            all_results.append(r)

            agent.reset_conversation()

        write_db_summary(bench_dir, db_id, db_results)
        pct = correct_count / len(queries) * 100 if queries else 0
        print(f"  => {correct_count}/{len(queries)} ({pct:.1f}%)\n")

    if all_results and not args.extract_only:
        write_total_summary(all_results)


if __name__ == "__main__":
    main()
