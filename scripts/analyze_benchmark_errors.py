#!/usr/bin/env python3
"""BIRD Benchmark 错误归因 + 效率分析（DeepSeek 批量推理）。

Phase 1: 对所有错误 query log 调用 DeepSeek 归因分类
Phase 2: 抽样正确案例分析工具调用效率
Phase 3: 生成全局报告

Usage:
    python -m utils.analyze_benchmark_errors                       # 全量
    python -m utils.analyze_benchmark_errors --db toxicology        # 指定库
    python -m utils.analyze_benchmark_errors --workers 10           # 并行数
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
#  Prompts
# ═══════════════════════════════════════════════════════════

ERROR_ANALYSIS_PROMPT = """\
你是一个 Text-to-SQL 评测分析专家。请分析以下错误案例。

数据库: {db_id}
问题: {question}
提示: {evidence}
难度: {difficulty}
标准 SQL: {golden_sql}
预测 SQL: {predicted_sql}
预测行数: {pred_rows}
标准行数: {gold_rows}

请从以下类别中选择**一个**主因：
1. column_selection — 选错列或多加/少加列（如 golden 用 atom_id 但 predicted 用 element）
2. aggregation — 聚合方式不同（COUNT DISTINCT vs COUNT、GROUP BY 粒度、AVG 计算方式）
3. percentage — 百分比计算差异（分子分母来自不同数据集、计算维度不同）
4. join_path — JOIN 路径或条件不同（是否经过桥接表、JOIN 粒度松散 vs 精确）
5. condition — WHERE 条件差异（AND/OR 组合、空值处理、字符串范围）
6. output_format — 输出格式不同（GROUP_CONCAT vs 多行、单列多行 vs 多列一行、文本变换）
7. question_understanding — 问题语义理解偏差（歧义、特殊 SQL 技巧）
8. golden_debatable — golden SQL 本身有争议或写法不合理

严格输出一行 JSON（不要其他文字）：
{{"category":"...","reason":"一句话说明差异","fixable":true/false}}
"""

EFFICIENCY_ANALYSIS_PROMPT = """\
分析以下成功的 Text-to-SQL 查询的工具调用效率。

数据库: {db_id}
问题: {question}
难度: {difficulty}
耗时: {elapsed:.1f}s
最终 SQL: {predicted_sql}
工具调用记录（共 {tool_count} 次）:
{tool_calls_summary}

请分析：
1. 工具调用次数是否合理？（简单问题理想 3-5 次，复杂的 8-12 次）
2. 是否有明显冗余的调用？（重复查同一信息、不必要的验证）
3. 最关键的改进点是什么？

严格输出一行 JSON（不要其他文字）：
{{"tool_calls": {tool_count}, "reasonable": true/false, "redundant_count": 0, "suggestion": "一句话建议"}}
"""


# ═══════════════════════════════════════════════════════════
#  Log Parsing
# ═══════════════════════════════════════════════════════════

def parse_log(path: str) -> dict | None:
    """解析单个 query log，提取关键字段。"""
    with open(path, encoding="utf-8") as f:
        content = f.read()

    fields = {}
    for line in content.split("\n"):
        for key in ["Question", "Evidence", "Difficulty", "Golden SQL", "Predicted SQL",
                     "Result", "Predicted rows", "Gold rows", "Time"]:
            if line.startswith(key + ":"):
                fields[key] = line.split(":", 1)[1].strip()

    if "Result" not in fields:
        return None

    # Extract predicted SQL from Agent done section
    agent_done = re.search(r"Agent done: ```sql\n(.*?)```", content, re.DOTALL)
    if agent_done:
        fields["Predicted SQL"] = agent_done.group(1).strip()

    # Extract tool calls summary
    tool_calls = re.findall(r"Tool call: (\w+)\((.+?)\)$", content, re.MULTILINE)
    tool_summary_lines = []
    for tool_name, args_str in tool_calls:
        tool_summary_lines.append(f"  {tool_name}({args_str[:100]})")

    fields["tool_calls_summary"] = "\n".join(tool_summary_lines)
    fields["tool_count"] = len(tool_calls)

    return fields


def scan_logs(db_filter: str | None = None) -> tuple[list[dict], list[dict]]:
    """扫描所有 query log，返回 (errors, correct_samples)。"""
    dev_data = json.loads(DEV_JSON.read_text(encoding="utf-8"))
    q_difficulty = {q["question_id"]: q.get("difficulty", "?") for q in dev_data}

    errors = []
    correct_by_db = defaultdict(list)

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

            record = {
                "db_id": db_id,
                "question_id": qid,
                "difficulty": fields.get("Difficulty", q_difficulty.get(qid, "?")),
                "question": fields.get("Question", ""),
                "evidence": fields.get("Evidence", ""),
                "golden_sql": fields.get("Golden SQL", ""),
                "predicted_sql": fields.get("Predicted SQL", ""),
                "pred_rows": fields.get("Predicted rows", "?"),
                "gold_rows": fields.get("Gold rows", "?"),
                "elapsed": 0.0,
                "tool_calls_summary": fields.get("tool_calls_summary", ""),
                "tool_count": fields.get("tool_count", 0),
            }
            # Parse elapsed
            time_str = fields.get("Time", "0s")
            try:
                record["elapsed"] = float(time_str.rstrip("s"))
            except ValueError:
                pass

            result = fields.get("Result", "")
            if "CORRECT" in result:
                correct_by_db[db_id].append(record)
            else:
                errors.append(record)

    # Sample correct cases: 5 per db, prefer longer elapsed (more optimization potential)
    samples = []
    for db_id, records in correct_by_db.items():
        records.sort(key=lambda r: -r["elapsed"])
        # Try to cover different difficulties
        by_diff = defaultdict(list)
        for r in records:
            by_diff[r["difficulty"]].append(r)
        sampled = []
        for diff in ["simple", "moderate", "challenging"]:
            if by_diff[diff]:
                sampled.append(by_diff[diff][0])
        # Fill remaining with longest
        for r in records:
            if r not in sampled and len(sampled) < 5:
                sampled.append(r)
        samples.extend(sampled[:5])

    return errors, samples


# ═══════════════════════════════════════════════════════════
#  DeepSeek API
# ═══════════════════════════════════════════════════════════

def get_client():
    from openai import OpenAI
    from agent.config import AGENT_API_KEY
    return OpenAI(api_key=AGENT_API_KEY, base_url="https://api.deepseek.com")


def call_deepseek(client, prompt: str) -> str:
    """调用 DeepSeek，返回回复文本。"""
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=256,
    )
    return resp.choices[0].message.content.strip()


_CATEGORY_NUMBER_MAP = {
    "1": "column_selection", "2": "aggregation", "3": "percentage",
    "4": "join_path", "5": "condition", "6": "output_format",
    "7": "question_understanding", "8": "golden_debatable",
}


def _normalize_category(cat: str) -> str:
    """DeepSeek 有时返回数字而非类别名，做归一化。"""
    if cat in _CATEGORY_NUMBER_MAP:
        return _CATEGORY_NUMBER_MAP[cat]
    return cat


def analyze_one_error(error: dict, client) -> dict:
    """单条错误归因。"""
    prompt = ERROR_ANALYSIS_PROMPT.format(**error)
    try:
        raw = call_deepseek(client, prompt)
        # Extract JSON from response
        m = re.search(r'\{[^}]+\}', raw)
        if m:
            result = json.loads(m.group())
            result["category"] = _normalize_category(result.get("category", "unknown"))
        else:
            result = {"category": "unknown", "reason": raw[:100], "fixable": False}
    except Exception as e:
        result = {"category": "api_error", "reason": str(e)[:100], "fixable": False}

    return {**error, "analysis": result}


def analyze_one_efficiency(sample: dict, client) -> dict:
    """单条成功案例效率分析。"""
    prompt = EFFICIENCY_ANALYSIS_PROMPT.format(**sample)
    try:
        raw = call_deepseek(client, prompt)
        m = re.search(r'\{[^}]+\}', raw)
        if m:
            result = json.loads(m.group())
        else:
            result = {"reasonable": True, "redundant_count": 0, "suggestion": raw[:100]}
    except Exception as e:
        result = {"reasonable": True, "redundant_count": 0, "suggestion": f"API error: {e}"}

    return {**sample, "efficiency": result}


def analyze_parallel(items: list[dict], analyzer, workers: int = 10) -> list[dict]:
    """并行分析。"""
    client = get_client()
    results = []
    done = 0
    total = len(items)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(analyzer, item, client): i for i, item in enumerate(items)}
        for future in as_completed(futures):
            done += 1
            try:
                results.append(future.result())
            except Exception as e:
                idx = futures[future]
                logger.error(f"Analysis failed for index {idx}: {e}")
            if done % 50 == 0 or done == total:
                print(f"  Progress: {done}/{total}")

    return results


# ═══════════════════════════════════════════════════════════
#  Report Generation
# ═══════════════════════════════════════════════════════════

CATEGORY_NAMES = {
    "column_selection": "列选择偏差",
    "aggregation": "聚合逻辑偏差",
    "percentage": "百分比计算偏差",
    "join_path": "JOIN 路径差异",
    "condition": "WHERE 条件差异",
    "output_format": "输出格式差异",
    "question_understanding": "问题理解偏差",
    "golden_debatable": "Golden SQL 有争议",
    "api_error": "API 错误",
    "unknown": "未分类",
}


def generate_global_report(error_results: list[dict], eff_results: list[dict],
                           db_totals: dict) -> str:
    """生成全局报告 markdown。"""
    total_errors = len(error_results)

    # ── Category stats ──
    cat_counts = defaultdict(int)
    cat_examples = defaultdict(list)
    for r in error_results:
        cat = r["analysis"].get("category", "unknown")
        cat_counts[cat] += 1
        if len(cat_examples[cat]) < 5:
            cat_examples[cat].append(r)

    # ── DB stats ──
    db_errors = defaultdict(int)
    db_total = defaultdict(int)
    for r in error_results:
        db_errors[r["db_id"]] += 1
    for db_id, total in db_totals.items():
        db_total[db_id] = total

    # ── Difficulty stats ──
    diff_errors = defaultdict(int)
    diff_total = defaultdict(int)
    for r in error_results:
        diff_errors[r["difficulty"]] += 1
    # Re-count from all logs
    for r in error_results:
        diff_total[r["difficulty"]] += 1

    # ── Build report ──
    lines = ["# BIRD Benchmark 全量错误归因分析", ""]
    lines.append("## 总览")
    lines.append("")
    lines.append(f"- 总测试：{sum(db_total.values())} 题")
    lines.append(f"- 错误：{total_errors} 题")
    lines.append(f"- 准确率：{(1 - total_errors / max(sum(db_total.values()), 1)) * 100:.1f}%")
    lines.append("")

    # DB table
    lines.append("## 各数据库表现")
    lines.append("")
    lines.append("| 数据库 | 错误 | 总数 | 准确率 |")
    lines.append("|---|---|---|---|")
    for db_id in sorted(db_total.keys()):
        errs = db_errors.get(db_id, 0)
        total = db_total[db_id]
        pct = (1 - errs / max(total, 1)) * 100
        lines.append(f"| {db_id} | {errs} | {total} | {pct:.1f}% |")
    lines.append("")

    # Category table
    lines.append("## 错误分类统计")
    lines.append("")
    lines.append("| 类别 | 数量 | 占比 | 说明 |")
    lines.append("|---|---|---|---|")
    for cat in sorted(cat_counts.keys(), key=lambda c: -cat_counts[c]):
        cnt = cat_counts[cat]
        pct = cnt / max(total_errors, 1) * 100
        name = CATEGORY_NAMES.get(cat, cat)
        lines.append(f"| {name} | {cnt} | {pct:.1f}% | {cat} |")
    lines.append("")

    # Category details with examples
    lines.append("## 各类别详细分析")
    lines.append("")
    for cat in sorted(cat_counts.keys(), key=lambda c: -cat_counts[c]):
        cnt = cat_counts[cat]
        name = CATEGORY_NAMES.get(cat, cat)
        fixable = sum(1 for r in error_results if r["analysis"].get("category") == cat and r["analysis"].get("fixable"))
        lines.append(f"### {name}（{cnt} 题，可修复 {fixable} 题）")
        lines.append("")
        lines.append("典型示例：")
        lines.append("")
        for ex in cat_examples[cat]:
            reason = ex["analysis"].get("reason", "")
            lines.append(f"- **Q{ex['question_id']}** [{ex['db_id']}] {reason}")
        lines.append("")

    # Efficiency analysis
    if eff_results:
        lines.append("## 成功案例效率分析")
        lines.append("")
        lines.append(f"抽样 {len(eff_results)} 条正确案例：")
        lines.append("")
        total_tc = sum(r.get("tool_count", 0) for r in eff_results)
        total_elapsed = sum(r.get("elapsed", 0) for r in eff_results)
        reasonable = sum(1 for r in eff_results if r.get("efficiency", {}).get("reasonable", True))
        lines.append(f"- 平均工具调用：{total_tc / max(len(eff_results), 1):.1f} 次")
        lines.append(f"- 平均耗时：{total_elapsed / max(len(eff_results), 1):.1f}s")
        lines.append(f"- 调用次数合理：{reasonable}/{len(eff_results)}")
        lines.append("")

        # DB-level efficiency
        db_eff = defaultdict(list)
        for r in eff_results:
            db_eff[r["db_id"]].append(r)
        lines.append("| 数据库 | 平均调用 | 平均耗时 | 合理率 | 建议 |")
        lines.append("|---|---|---|---|---|")
        for db_id in sorted(db_eff.keys()):
            recs = db_eff[db_id]
            avg_tc = sum(r.get("tool_count", 0) for r in recs) / len(recs)
            avg_t = sum(r.get("elapsed", 0) for r in recs) / len(recs)
            ok = sum(1 for r in recs if r.get("efficiency", {}).get("reasonable", True))
            suggestions = [r.get("efficiency", {}).get("suggestion", "") for r in recs if r.get("efficiency", {}).get("suggestion")]
            suggestion = suggestions[0][:40] if suggestions else "-"
            lines.append(f"| {db_id} | {avg_tc:.1f} | {avg_t:.1f}s | {ok}/{len(recs)} | {suggestion} |")
        lines.append("")

    # Actionable recommendations
    lines.append("## 调整建议")
    lines.append("")
    for cat in sorted(cat_counts.keys(), key=lambda c: -cat_counts[c]):
        cnt = cat_counts[cat]
        if cnt < 10:
            continue
        name = CATEGORY_NAMES.get(cat, cat)
        fixable = sum(1 for r in error_results if r["analysis"].get("category") == cat and r["analysis"].get("fixable"))
        lines.append(f"### {name}（{cnt} 题，可修复 {fixable}）")
        if fixable > 0:
            lines.append(f"- 预期可修复 {fixable} 题，准确率提升约 {fixable / max(sum(db_total.values()), 1) * 100:.1f}%")
        lines.append("")

    return "\n".join(lines)


def count_db_totals(db_filter: str | None = None) -> dict:
    """统计每个数据库的总题数。"""
    dev_data = json.loads(DEV_JSON.read_text(encoding="utf-8"))
    by_db = defaultdict(int)
    for q in dev_data:
        if db_filter and q["db_id"] != db_filter:
            continue
        by_db[q["db_id"]] += 1
    return dict(by_db)


# ═══════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="BIRD Benchmark Error Analysis")
    parser.add_argument("--db", help="只分析指定数据库")
    parser.add_argument("--workers", type=int, default=10, help="并行 worker 数量（默认 10）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s | %(message)s", datefmt="%H:%M:%S")

    print("=== BIRD Benchmark Error Analysis ===\n")

    db_totals = count_db_totals(args.db)

    print("Phase 1: 扫描日志...")
    errors, samples = scan_logs(args.db)
    print(f"  错误: {len(errors)} 条, 成功抽样: {len(samples)} 条\n")

    print("Phase 2: 错误归因分析（DeepSeek）...")
    t0 = time.time()
    error_results = analyze_parallel(errors, analyze_one_error, args.workers)
    print(f"  完成: {len(error_results)} 条, 耗时 {time.time() - t0:.0f}s\n")

    print("Phase 3: 效率分析（DeepSeek）...")
    t0 = time.time()
    eff_results = analyze_parallel(samples, analyze_one_efficiency, args.workers)
    print(f"  完成: {len(eff_results)} 条, 耗时 {time.time() - t0:.0f}s\n")

    print("Phase 4: 生成报告...")
    report = generate_global_report(error_results, eff_results, db_totals)

    report_path = PROJECT_ROOT / "docs" / "benchmark_error_analysis_global.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"  报告: {report_path}")

    # Save machine-readable results
    json_path = BIRD_DIR / "error_categories.json"
    json_data = []
    for r in error_results:
        json_data.append({
            "db_id": r["db_id"],
            "question_id": r["question_id"],
            "difficulty": r["difficulty"],
            "category": r["analysis"].get("category", "unknown"),
            "reason": r["analysis"].get("reason", ""),
            "fixable": r["analysis"].get("fixable", False),
        })
    json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  JSON: {json_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
