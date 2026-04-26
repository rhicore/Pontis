#!/usr/bin/env python3
"""BIRD Benchmark 错误分类 — DeepSeek 二分类分析。

将每条 WRONG/ERROR 记录分为两类：
  1. 模型未充分探索数据库结构导致的错误（可改进）
  2. 查询本身的模糊性和歧义性导致的错误（难以改进）

Usage:
    python -m scripts.classify_benchmark_errors --db formula_1
    python -m scripts.classify_benchmark_errors --db formula_1 --workers 15
"""
import json
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
#  Prompt
# ═══════════════════════════════════════════════════════════

CLASSIFY_PROMPT = """\
你是一个 Text-to-SQL 错误分析专家。请严格分析以下 benchmark 测试失败的完整日志，判断错误原因属于以下哪一类。

【类别1】模型未充分探索数据库结构导致的错误
特征：
- 模型不知道某张表/列的存在，或误解了列的含义
- 模型对数据格式理解错误（如时间格式、NULL 含义）
- 模型 JOIN 条件写错或漏掉了必要的表
- 如果给模型一个"完美 Schema 文档"，它就能写出正确的 SQL

【类别2】查询本身的模糊性和歧义性导致的错误
特征（满足任意一条即归为类别2）：
- 标准 SQL 和预测 SQL 回答的是**不同的但各自合理的问题**
- 问题中的自然语言存在多种公认的理解方式（如 "前10年" 包不包含第10年、"participated" 指报名还是完赛）
- COUNT 记录数 vs COUNT DISTINCT 实体数（如"多少人"可以指"多少个不同的人"也可以被理解为"多少次出现"）
- 标准 SQL 和预测 SQL 在语义上都通顺，只是对问题的理解角度不同
- 核心判断标准：如果有一个数据库专家看了完整 schema 后，仍然可能写出预测 SQL 那样的答案，那就是类别2

以下是常见的类别2例子，供你参考：
- 标准 SQL 从 A 表取 url，预测 SQL 从 B 表取 url，但问题和 evidence 没有明确说清是哪个 url → 类别2
- 标准 SQL 用 COUNT(*)，预测 SQL 用 COUNT(DISTINCT driverId)，两者对"how many"的理解不同 → 类别2
- 标准 SQL 从 driverStandings 统计参赛人数，预测 SQL 从 results 统计，两者对"participated"定义不同 → 类别2

请输出一个 JSON 对象，不要包含任何其他文字：
{{
  "category": 1 或 2,
  "reason": "简短理由（1-2句话），必须明确说明是'模型没理解schema'还是'问题本身有歧义'",
  "fixable": true 或 false
}}

以下是该测试案例的完整日志，请仔细阅读后再做判断：

{full_log}
"""


# ═══════════════════════════════════════════════════════════
#  Log Parsing (reused from analyze_benchmark_errors)
# ═══════════════════════════════════════════════════════════

def parse_log(path: str) -> dict | None:
    """解析单个 query log，提取关键字段并保留完整日志。"""
    with open(path, encoding="utf-8", errors="replace") as f:
        content = f.read()

    fields = {"full_log": content}
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


def scan_wrong_logs(db_filter: str | None = None) -> list[dict]:
    """扫描所有 query log，只返回 WRONG/ERROR 的记录。"""
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

            result = fields.get("Result", "UNKNOWN")
            if result not in ("WRONG", "ERROR", "PARSE_ERROR"):
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
                "result": result,
                "elapsed": elapsed,
                "tool_calls_summary": fields.get("tool_calls_summary", ""),
                "tool_count": fields.get("tool_count", 0),
                "block_count": fields.get("block_count", 0),
                "full_log": fields.get("full_log", ""),
            })

    return records


# ═══════════════════════════════════════════════════════════
#  DeepSeek API
# ═══════════════════════════════════════════════════════════

def get_client():
    from openai import OpenAI
    from agent.config import AGENT_API_KEY
    return OpenAI(api_key=AGENT_API_KEY, base_url="https://api.deepseek.com")


def classify_one(record: dict, client) -> dict:
    """单条 log 分类。"""
    prompt = CLASSIFY_PROMPT.format(**record)
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=2048,
        )
        text = resp.choices[0].message.content.strip()
        # 尝试从返回文本中提取 JSON
        # DeepSeek 可能返回 markdown 代码块，也可能直接返回 JSON
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            record["category"] = parsed.get("category", 0)
            record["reason"] = parsed.get("reason", "")
            record["fixable"] = parsed.get("fixable", False)
        else:
            record["category"] = 0
            record["reason"] = f"[无法解析 JSON: {text[:200]}]"
            record["fixable"] = False
    except Exception as e:
        record["category"] = 0
        record["reason"] = f"[API ERROR: {e}]"
        record["fixable"] = False
    return record


# ═══════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="BIRD Benchmark Error Classification")
    parser.add_argument("--db", help="只分析指定数据库")
    parser.add_argument("--workers", type=int, default=10, help="并行 worker 数量")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s | %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    print("=== BIRD Benchmark Error Classification ===\n")

    print("Phase 1: 扫描错误日志...")
    records = scan_wrong_logs(args.db)
    if not records:
        print("  没有找到 WRONG/ERROR 记录")
        return
    print(f"  共 {len(records)} 条错误记录\n")

    print("Phase 2: DeepSeek 逐条分类...")
    t0 = time.time()
    client = get_client()
    results = []
    done = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(classify_one, r, client): r["question_id"] for r in records}
        for future in as_completed(futures):
            done += 1
            try:
                results.append(future.result())
            except Exception as e:
                logger.error(f"Failed for Q{futures[future]}: {e}")
            if done % 10 == 0 or done == len(records):
                print(f"  Progress: {done}/{len(records)}")
    print(f"  完成, 耗时 {time.time() - t0:.0f}s\n")

    # Sort by question_id
    results.sort(key=lambda r: r["question_id"])

    # 统计
    cat1 = [r for r in results if r.get("category") == 1]
    cat2 = [r for r in results if r.get("category") == 2]
    unknown = [r for r in results if r.get("category") not in (1, 2)]

    print("=" * 50)
    print("分类统计")
    print("=" * 50)
    print(f"类别1（探索不足，可改进）: {len(cat1)}/{len(results)} ({len(cat1)/len(results)*100:.1f}%)")
    print(f"类别2（查询歧义，难改进）: {len(cat2)}/{len(results)} ({len(cat2)/len(results)*100:.1f}%)")
    if unknown:
        print(f"未分类/解析失败: {len(unknown)}/{len(results)} ({len(unknown)/len(results)*100:.1f}%)")
    print()

    # 按难度统计
    by_diff = defaultdict(lambda: [0, 0])
    for r in results:
        diff = r.get("difficulty", "?")
        if r.get("category") == 1:
            by_diff[diff][0] += 1
        elif r.get("category") == 2:
            by_diff[diff][1] += 1

    print("按难度分布:")
    for diff in ["simple", "moderate", "challenging"]:
        if diff in by_diff:
            c1, c2 = by_diff[diff]
            total = c1 + c2
            print(f"  {diff}: 类别1={c1}, 类别2={c2} (共{total})")
    print()

    # 写入详细报告
    db_label = args.db or "all"
    out_path = BIRD_DIR / f"error_classification_{db_label}.md"
    lines = [f"# BIRD Benchmark 错误分类 — {db_label}", ""]
    lines.append(f"总计 {len(results)} 条错误记录")
    lines.append(f"- 类别1（探索不足，可改进）: {len(cat1)} ({len(cat1)/len(results)*100:.1f}%)")
    lines.append(f"- 类别2（查询歧义，难改进）: {len(cat2)} ({len(cat2)/len(results)*100:.1f}%)")
    if unknown:
        lines.append(f"- 未分类: {len(unknown)}")
    lines.append("")

    for r in results:
        cat = r.get("category", 0)
        cat_label = {1: "类别1-探索不足", 2: "类别2-查询歧义"}.get(cat, f"未分类({cat})")
        lines.append(f"## Q{r['question_id']} [{r['difficulty']}] {cat_label}")
        lines.append(f"- 问题: {r['question']}")
        lines.append(f"- 标准 SQL: {r['golden_sql'][:150]}")
        lines.append(f"- 预测 SQL: {r['predicted_sql'][:150]}")
        lines.append(f"- 工具调用: {r['tool_count']} 次 | Guardrail block: {r['block_count']} 次")
        lines.append(f"- 判定理由: {r.get('reason', '')}")
        lines.append("")
        lines.append("---")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"详细报告: {out_path}")
    print("\nDone.")


if __name__ == "__main__":
    main()
