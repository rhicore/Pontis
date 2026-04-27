#!/usr/bin/env python3
"""BIRD Benchmark 错误分析 — DeepSeek 逐条生成文字总结。

Phase 1: 解析所有 log，提取关键信息
Phase 2: 对每条 log（正确+错误）调用 DeepSeek 生成一段文字总结
Phase 3: 将所有总结写入一个文件，供人工通读分析

Usage:
    python -m scripts.analyze_benchmark_errors --db formula_1
    python -m scripts.analyze_benchmark_errors --db formula_1 --workers 15
"""
import logging
import os
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIRD_DIR = PROJECT_ROOT / "example_data" / "bird"
DB_BASE = BIRD_DIR / "dev_databases"
DEV_JSON = BIRD_DIR / "dev.json"

# ═══════════════════════════════════════════════════════════
#  Prompts
# ═══════════════════════════════════════════════════════════

LOG_SUMMARY_PROMPT = """\
你是一个 Text-to-SQL 评测分析专家。请分析以下 benchmark 测试案例的完整日志，写一段简要总结。

基本信息：
- 数据库: {db_id}
- 问题: {question}
- 提示: {evidence}
- 难度: {difficulty}
- 结果: {result}
- 工具调用: {tool_count} 次
- Guardrail 拦截: {block_count} 次
- 耗时: {elapsed:.1f}s

标准 SQL:
{golden_sql}

预测 SQL:
{predicted_sql}

工具调用序列:
{tool_calls_summary}

请用 3-5 句话总结，包括：
1. 这个问题的核心难点是什么
2. agent 的探索过程是否高效、是否走了弯路
3. 如果结果错误，具体错在哪里（对比标准 SQL 和预测 SQL 的关键差异）
4. guardrail 拦截是否合理、是否导致了不必要的绕路或过度探索
5. 如果要改进，最关键的一点是什么
"""


# ═══════════════════════════════════════════════════════════
#  Log Parsing
# ═══════════════════════════════════════════════════════════

def parse_log(path: str) -> dict | None:
    """解析单个 query log，提取关键字段。"""
    with open(path, encoding="utf-8", errors="replace") as f:
        content = f.read()

    fields = {}
    lines = content.split("\n")
    i = 0
    multi_line_keys = {"Golden SQL", "Predicted SQL"}
    while i < len(lines):
        for key in ["Question", "Evidence", "Difficulty", "Golden SQL", "Predicted SQL",
                     "Result", "Predicted rows", "Gold rows", "Time"]:
            if lines[i].startswith(key + ":"):
                value = lines[i].split(":", 1)[1].strip()
                if key in multi_line_keys:
                    while i + 1 < len(lines):
                        next_line = lines[i + 1]
                        if next_line.startswith(("Question:", "Evidence:", "Difficulty:",
                                                  "Golden SQL:", "Predicted SQL:", "Result:",
                                                  "Predicted rows:", "Gold rows:", "Time:", "===")):
                            break
                        i += 1
                        value += "\n" + next_line
                fields[key] = value
                break
        i += 1

    if "Result" not in fields:
        return None

    # Agent done SQL (more reliable than the appended summary)
    agent_done = re.search(r"Agent done: ```sql\n(.*?)```", content, re.DOTALL)
    if agent_done:
        fields["Predicted SQL"] = agent_done.group(1).strip()

    # Tool calls
    tool_calls = re.findall(r"Tool call: (\w+)\((.+?)\)$", content, re.MULTILINE)
    tool_summary_lines = []
    for tool_name, args_str in tool_calls:
        tool_summary_lines.append(f"  {tool_name}({args_str[:120]})")
    fields["tool_calls_summary"] = "\n".join(tool_summary_lines)
    fields["tool_count"] = len(tool_calls)

    # Guardrail blocks
    fields["block_count"] = len(re.findall(r"Guardrail block", content))

    return fields


def scan_logs(db_filter: str | None = None) -> list[dict]:
    """扫描所有 query log，返回记录列表。"""
    import json
    dev_data = json.loads(DEV_JSON.read_text(encoding="utf-8"))
    q_difficulty = {q["question_id"]: q.get("difficulty", "?") for q in dev_data}
    q_evidence = {q["question_id"]: q.get("evidence", "") for q in dev_data}

    records = []

    db_dirs = sorted(DB_BASE.iterdir()) if DB_BASE.exists() else []
    for db_dir in db_dirs:
        if not db_dir.is_dir():
            continue
        db_id = db_dir.name
        if db_filter and db_id != db_filter:
            continue

        bench_dir = db_dir / ".pontis" / "benchmark"
        if not bench_dir.exists():
            continue

        for fname in sorted(os.listdir(bench_dir)):
            m = re.match(r"q(\d+)\.log", fname)
            if not m:
                continue
            qid = int(m.group(1))
            fields = parse_log(str(bench_dir / fname))
            if not fields:
                continue

            time_str = fields.get("Time", "0s")
            try:
                elapsed = float(time_str.rstrip("s"))
            except ValueError:
                elapsed = 0.0

            records.append({
                "db_id": db_id,
                "question_id": qid,
                "difficulty": q_difficulty.get(qid, "?"),
                "question": fields.get("Question", ""),
                "evidence": q_evidence.get(qid, ""),
                "golden_sql": fields.get("Golden SQL", ""),
                "predicted_sql": fields.get("Predicted SQL", "PARSE_ERROR"),
                "result": fields.get("Result", "UNKNOWN"),
                "pred_rows": fields.get("Predicted rows", "?"),
                "gold_rows": fields.get("Gold rows", "?"),
                "elapsed": elapsed,
                "tool_calls_summary": fields.get("tool_calls_summary", ""),
                "tool_count": fields.get("tool_count", 0),
                "block_count": fields.get("block_count", 0),
            })

    return records


# ═══════════════════════════════════════════════════════════
#  DeepSeek API
# ═══════════════════════════════════════════════════════════

def get_client():
    from openai import OpenAI
    from global_config import AGENT_API_KEY
    return OpenAI(api_key=AGENT_API_KEY, base_url="https://api.deepseek.com")


def summarize_one(record: dict, client) -> dict:
    """单条 log 总结。"""
    prompt = LOG_SUMMARY_PROMPT.format(**record)
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=512,
        )
        record["summary"] = resp.choices[0].message.content.strip()
    except Exception as e:
        record["summary"] = f"[API ERROR: {e}]"
    return record


# ═══════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="BIRD Benchmark Error Analysis")
    parser.add_argument("--db", help="只分析指定数据库")
    parser.add_argument("--workers", type=int, default=10, help="并行 worker 数量")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s | %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    print("=== BIRD Benchmark Error Analysis ===\n")

    print("Phase 1: 扫描日志...")
    records = scan_logs(args.db)
    correct = sum(1 for r in records if "CORRECT" in r["result"])
    wrong = sum(1 for r in records if "WRONG" in r["result"])
    other = len(records) - correct - wrong
    print(f"  共 {len(records)} 条: {correct} correct, {wrong} wrong, {other} other\n")

    print("Phase 2: DeepSeek 逐条总结...")
    t0 = time.time()
    client = get_client()
    results = []
    done = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(summarize_one, r, client): r["question_id"] for r in records}
        for future in as_completed(futures):
            done += 1
            try:
                results.append(future.result())
            except Exception as e:
                logger.error(f"Failed for Q{futures[future]}: {e}")
            if done % 20 == 0 or done == len(records):
                print(f"  Progress: {done}/{len(records)}")
    print(f"  完成, 耗时 {time.time() - t0:.0f}s\n")

    # Sort by question_id
    results.sort(key=lambda r: r["question_id"])

    # Write summaries
    out_path = BIRD_DIR / "analysis_summaries.md"
    lines = [f"# BIRD Benchmark 逐条分析 — {args.db or 'all'}", ""]
    lines.append(f"总计 {len(results)} 条: {correct} correct, {wrong} wrong, {other} other")
    lines.append("")

    for r in results:
        status = "OK" if "CORRECT" in r["result"] else r["result"]
        lines.append(f"## Q{r['question_id']} [{r['difficulty']}] {status}")
        lines.append(f"- 问题: {r['question']}")
        lines.append(f"- 工具: {r['tool_count']} 次 | 拦截: {r['block_count']} 次 | 耗时: {r['elapsed']:.0f}s")
        lines.append(f"- Golden: {r['golden_sql'][:150]}")
        lines.append(f"- Predicted: {r['predicted_sql'][:150]}")
        lines.append(f"")
        lines.append(r["summary"])
        lines.append("")
        lines.append("---")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Summaries: {out_path}")
    print("\nDone.")


if __name__ == "__main__":
    main()
