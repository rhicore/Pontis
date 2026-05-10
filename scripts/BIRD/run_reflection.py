#!/usr/bin/env python3
"""BIRD Reflection Runner — 从 benchmark 日志提炼跨库经验。

读取 run_bird_benchmark.py 生成的 *.brief.log，
为每个数据库收集已验证的正确/错误案例，交给 reflection agent 分析。

reflection agent 会同时打开：
- 当前数据库项目
- bird 项目

它只应把跨数据库可迁移的经验写入 bird，数据库特有事实只作为分析背景。

Usage:
    python -m scripts.BIRD.run_reflection
    python -m scripts.BIRD.run_reflection --db formula_1
    python -m scripts.BIRD.run_reflection --train
    python -m scripts.BIRD.run_reflection --dry-run
"""
import logging
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from scripts.BIRD.common import (
    get_benchmark_dir,
    get_db_base,
    get_db_dir,
    list_db_ids_with_benchmark_logs,
)

logger = logging.getLogger(__name__)

HEADER_RE = re.compile(r"Q(\d+)\s+\[([^\]]+)\]\s+(\w+)\s+([\d.]+)s")
CASE_ORDER = {"CORRECT": 0, "WRONG": 1, "EXEC_ERROR": 2, "PARSE_ERROR": 3, "ERROR": 4}


def parse_case_logs(bench_dir: Path) -> list[dict]:
    """解析 brief log，返回所有案例。"""
    cases = []
    for brief_file in sorted(bench_dir.glob("*.brief.log")):
        text = brief_file.read_text(encoding="utf-8")
        header_match = HEADER_RE.match(text)
        if not header_match:
            continue

        info = {
            "question_id": int(header_match.group(1)),
            "difficulty": header_match.group(2),
            "result": header_match.group(3),
            "elapsed": float(header_match.group(4)),
            "question": "",
            "evidence": "",
            "predicted_sql": "",
            "golden_sql": "",
            "calls_summary": "",
            "blocks": "",
        }

        in_blocks = False
        block_lines = []
        for line in text.splitlines():
            if line.startswith("Question: "):
                info["question"] = line[len("Question: "):]
            elif line.startswith("Evidence: "):
                info["evidence"] = line[len("Evidence: "):]
            elif line.startswith("Predicted SQL: "):
                info["predicted_sql"] = line[len("Predicted SQL: "):]
            elif line.startswith("Golden SQL: "):
                info["golden_sql"] = line[len("Golden SQL: "):]
            elif line.startswith("Calls: "):
                info["calls_summary"] = line[len("Calls: "):]
            elif line.startswith("Blocks:"):
                in_blocks = True
            elif in_blocks:
                if line.strip():
                    block_lines.append(line.strip())

        if block_lines:
            info["blocks"] = "\n".join(block_lines)
        cases.append(info)

    return cases


def summarize_cases(cases: list[dict]) -> dict:
    total = len(cases)
    by_result = Counter(c["result"] for c in cases)
    by_diff = defaultdict(Counter)
    for case in cases:
        by_diff[case["difficulty"]][case["result"]] += 1
    return {"total": total, "by_result": by_result, "by_diff": by_diff}


def pick_cases(cases: list[dict], max_errors: int, max_correct: int) -> tuple[list[dict], list[dict]]:
    """挑选进入 prompt 的案例，优先覆盖不同错误类型和难度。"""
    errors = [c for c in cases if c["result"] != "CORRECT"]
    corrects = [c for c in cases if c["result"] == "CORRECT"]

    errors.sort(key=lambda c: (
        CASE_ORDER.get(c["result"], 99),
        {"challenging": 0, "moderate": 1, "simple": 2}.get(c["difficulty"], 9),
        -c["elapsed"],
        c["question_id"],
    ))

    selected_errors = []
    seen_pairs = set()
    for case in errors:
        key = (case["result"], case["difficulty"])
        if key not in seen_pairs or len(selected_errors) < max_errors // 2:
            selected_errors.append(case)
            seen_pairs.add(key)
        if len(selected_errors) >= max_errors:
            break
    for case in errors:
        if len(selected_errors) >= max_errors:
            break
        if case not in selected_errors:
            selected_errors.append(case)

    corrects.sort(key=lambda c: (
        {"challenging": 0, "moderate": 1, "simple": 2}.get(c["difficulty"], 9),
        -c["elapsed"],
        c["question_id"],
    ))
    selected_corrects = []
    seen_diff = set()
    for case in corrects:
        if case["difficulty"] not in seen_diff or len(selected_corrects) < max_correct:
            selected_corrects.append(case)
            seen_diff.add(case["difficulty"])
        if len(selected_corrects) >= max_correct:
            break

    return selected_errors, selected_corrects


def build_task_prompt(db_id: str, cases: list[dict], selected_errors: list[dict],
                      selected_corrects: list[dict], summary: dict) -> str:
    """构建 BIRD 专用反思任务 prompt。"""
    total = summary["total"]
    correct = summary["by_result"].get("CORRECT", 0)
    wrong = total - correct

    lines = [
        "## 任务",
        "",
        f"数据库项目: {db_id}",
        "你当前可以访问两个项目：",
        f"- `{db_id}`：当前数据库及其知识图谱",
        "- `bird`：BIRD 数据集的跨库长期经验库",
        "",
        f"总题数: {total}",
        f"正确: {correct}",
        f"非正确: {wrong}",
        "",
        "## 你的职责",
        "",
        "1. 结合正确案例与错误案例，提炼可迁移的解题经验",
        "2. 先查 `bird` 是否已有相似经验",
        "3. 只把跨库可迁移经验写入 `bird`",
        "4. 当前数据库特有事实不要写入 `bird`，最多作为分析背景使用",
        "",
        "## 输出范围约束",
        "",
        "- 默认先查重、先 update，只有明确缺失时才 create",
        "- 每次反思宁可 0 条，也不要为了产出而产出",
        "- 优先创建/更新 `convention` / `pattern` / `lesson`",
        "- 除非非常必要，不要创建 `example`",
        "- 不要创建 `term`；术语定义不属于这套 reflection memory",
        "- 单库证据通常只能支撑较弱结论；若只能证明当前库成立，就不要写入 `bird`",
        "- 不要把当前库的表名、列名、枚举值直接写成全局规则",
        "- 不要仅凭单个案例就写很强的普适结论；只有在多个案例支持时才升格成全局经验",
        "- create_entity / update_meta 的 brief/detail 必须是纯文本，不要 Markdown 代码块，不要未转义双引号",
        "",
        "## 当前批次统计",
        "",
        f"- 结果分布: {dict(summary['by_result'])}",
    ]

    lines.append("- 难度分布:")
    for diff in ("simple", "moderate", "challenging"):
        if diff in summary["by_diff"]:
            lines.append(f"  - {diff}: {dict(summary['by_diff'][diff])}")

    if selected_corrects:
        lines += ["", "## 正确案例（用于总结正向模式）", ""]
        for i, case in enumerate(selected_corrects, 1):
            lines += _format_case(case, i, "正确")

    if selected_errors:
        lines += ["", "## 错误案例（用于总结反向教训）", ""]
        for i, case in enumerate(selected_errors, 1):
            lines += _format_case(case, i, "错误")

    lines += [
        "",
        "## 建议步骤",
        "",
        "1. 先用 `glob(\"bird::*:knowledge\")` / `glob(\"bird::*:pattern\")` / `glob(\"bird::*:lesson\")` 查看已有跨库经验",
        f"2. 再用 `glob(\"{db_id}::*:table\")`、`glob(\"{db_id}::*:fk\")`、`meta(...)` 核对当前库结构",
        "3. 对比正确与错误案例，找重复模式",
        "4. 只把跨库经验写入 `bird`，相似经验优先 update 而不是重复 create",
        "5. 如果最后没有足够硬的跨库经验，可以不写任何知识实体",
        "",
        "请开始分析和更新。",
    ]
    return "\n".join(lines)


def _format_case(case: dict, idx: int, kind: str) -> list[str]:
    lines = [
        f"### {kind}案例 #{idx}",
        f"- Q{case['question_id']} [{case['difficulty']}] {case['result']} {case['elapsed']}s",
        f"- question: {case.get('question', '?')}",
        f"- evidence: {case.get('evidence') or '(无)'}",
        f"- predicted_sql: {case.get('predicted_sql') or 'N/A'}",
        f"- golden_sql: {case.get('golden_sql') or 'N/A'}",
    ]
    if case.get("calls_summary"):
        lines.append(f"- tool_calls: {case['calls_summary']}")
    if case.get("blocks"):
        lines.append(f"- blocks: {case['blocks']}")
    lines.append("")
    return lines


def run_reflection(db_id: str, prompt: str, train: bool) -> dict:
    """对单个数据库运行反思 agent。"""
    from agent.config import create_agent, AgentSpec

    db_dir = get_db_dir(db_id, train)

    print(f"[{db_id}] Starting reflection (prompt {len(prompt)} chars)...")

    spec = AgentSpec(mode="reflection", effort="max")
    spec.projects = [db_id, "bird"]
    agent = create_agent(str(db_dir), spec)

    t0 = time.time()
    try:
        agent.chat(prompt)
        elapsed = time.time() - t0
        print(f"[{db_id}] Done ({elapsed:.0f}s)")
        return {"db_id": db_id, "status": "ok", "elapsed": round(elapsed, 1)}
    except Exception as e:
        elapsed = time.time() - t0
        print(f"[{db_id}] Error: {e}")
        return {"db_id": db_id, "status": "error", "elapsed": round(elapsed, 1), "error": str(e)}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="BIRD Reflection — analyze benchmark cases, extract bird cross-db lessons")
    parser.add_argument("--db", help="只分析指定数据库")
    parser.add_argument("--train", action="store_true", help="分析 train 日志")
    parser.add_argument("--dry-run", action="store_true", help="只生成 prompt 不运行")
    parser.add_argument("--min-errors", type=int, default=1, help="最少错误数才触发反思（默认 1）")
    parser.add_argument("--max-errors", type=int, default=12, help="每个数据库最多送入 prompt 的错误案例数")
    parser.add_argument("--max-correct", type=int, default=3, help="每个数据库最多送入 prompt 的正确案例数")
    parser.add_argument("--allow-correct-only", action="store_true", help="允许仅基于正确案例进行反思")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s | %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    db_base = get_db_base(args.train)

    if not db_base.exists():
        print(f"Error: {db_base} not found")
        sys.exit(1)

    db_ids = list_db_ids_with_benchmark_logs(args.train, selected_db=args.db)

    if not db_ids:
        print("No benchmark logs found. Run run_bird_benchmark.py first.")
        sys.exit(1)

    print("=== BIRD Reflection Runner ===")
    print(f"Databases: {len(db_ids)}\n")

    results = []
    for db_id in db_ids:
        bench_dir = get_benchmark_dir(db_id, train=args.train)
        if not bench_dir.exists():
            continue

        cases = parse_case_logs(bench_dir)
        if not cases:
            print(f"[{db_id}] No parseable cases, skipping")
            continue

        summary = summarize_cases(cases)
        error_count = summary["total"] - summary["by_result"].get("CORRECT", 0)
        if error_count == 0 and not args.allow_correct_only:
            print(f"[{db_id}] 0 errors, skipping")
            continue
        if error_count > 0 and error_count < args.min_errors:
            print(f"[{db_id}] errors < {args.min_errors}, skipping")
            continue

        selected_errors, selected_corrects = pick_cases(
            cases, max_errors=args.max_errors, max_correct=args.max_correct
        )
        prompt = build_task_prompt(db_id, cases, selected_errors, selected_corrects, summary)

        if args.dry_run:
            print(f"[{db_id}] prompt {len(prompt)} chars | correct={len(selected_corrects)} | errors={len(selected_errors)}")
            results.append({"db_id": db_id, "status": "dry_run"})
            continue

        result = run_reflection(db_id, prompt, train=args.train)
        results.append(result)

    ok = sum(1 for r in results if r["status"] == "ok")
    err = sum(1 for r in results if r["status"] == "error")
    print(f"\n=== Summary: OK={ok}, Errors={err} ===")


if __name__ == "__main__":
    main()
