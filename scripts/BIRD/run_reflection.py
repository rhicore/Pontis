#!/usr/bin/env python3
"""BIRD Reflection Runner — 分析 benchmark 日志，提炼可迁移知识。

读取 run_bird_benchmark.py 生成的 *.brief.log，
对每个数据库的错误案例运行 reflection agent，
让 agent 在了解数据库结构的前提下分析执行记录。

所有知识（convention/pattern/term/lesson/example）→ agent 通过 create_entity 写入。
工具和 prompt 问题也写成 .lesson 实体，detail 标注 [tool_issue]/[prompt_issue] 前缀。

Usage:
    python -m scripts.BIRD.run_reflection                       # 全量
    python -m scripts.BIRD.run_reflection --db formula_1        # 指定库
    python -m scripts.BIRD.run_reflection --train               # 分析 train 日志
    python -m scripts.BIRD.run_reflection --dry-run             # 只生成 prompt 不运行
"""
import logging
import re
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def find_benchmark_dir(db_id: str, train: bool) -> Path | None:
    if train:
        db_base = PROJECT_ROOT / "example_data" / "bird_train" / "train_databases"
    else:
        db_base = PROJECT_ROOT / "example_data" / "bird" / "dev_databases"
    bench_dir = db_base / db_id / ".pontis" / "benchmark"
    return bench_dir if bench_dir.exists() else None


def parse_brief_logs(bench_dir: Path) -> list[dict]:
    """解析简洁版日志，返回错误案例列表。"""
    errors = []
    for brief_file in sorted(bench_dir.glob("*.brief.log")):
        text = brief_file.read_text(encoding="utf-8")
        header_match = re.match(r"Q(\d+)\s+\[(\w+)\]\s+(\w+)\s+([\d.]+)s", text)
        if not header_match:
            continue

        result = header_match.group(3)
        if result == "CORRECT":
            continue

        info = {
            "question_id": int(header_match.group(1)),
            "difficulty": header_match.group(2),
            "result": result,
            "elapsed": float(header_match.group(4)),
        }
        for line in text.split("\n"):
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

        errors.append(info)
    return errors


def build_task_prompt(db_id: str, errors: list[dict], total: int) -> str:
    """构建 BIRD 专用的反思任务 prompt（不含通用反思方法论，那部分在系统提示词里）。"""
    correct = total - len(errors)
    lines = [
        f"## 任务",
        f"",
        f"数据库: {db_id}（用 glob/meta/query 探索结构）",
        f"总题数: {total}，正确: {correct}，错误: {len(errors)}",
        f"",
        f"以下是错误案例：",
        f"",
    ]

    for i, err in enumerate(errors):
        lines.append(f"### 记录 #{i + 1}")
        lines.append(f"- Q{err['question_id']} [{err['difficulty']}] {err['result']} {err['elapsed']}s")
        lines.append(f"- question: {err.get('question', '?')}")
        lines.append(f"- evidence: {err.get('evidence', '')}")
        lines.append(f"- predicted_sql: {err.get('predicted_sql', 'N/A')}")
        lines.append(f"- golden_sql: {err.get('golden_sql', 'N/A')}")
        if err.get("calls_summary"):
            lines.append(f"- tool_calls: {err['calls_summary']}")
        lines.append("")

    lines.append("请分析这些错误，提炼知识实体，反馈工具和 prompt 问题。")
    return "\n".join(lines)


def run_reflection(db_id: str, errors: list[dict], total: int,
                   train: bool) -> dict:
    """对单个数据库运行反思 agent。

    使用 DB 目录作为 project_path，agent 通过 glob/meta 探索数据库结构，
    知识实体通过 create_entity 自动路由到全局 store (~/.pontis/)。
    """
    from agent.config import create_agent, AgentSpec

    if train:
        db_dir = PROJECT_ROOT / "example_data" / "bird_train" / "train_databases" / db_id
    else:
        db_dir = PROJECT_ROOT / "example_data" / "bird" / "dev_databases" / db_id

    task_prompt = build_task_prompt(db_id, errors, total)
    print(f"[{db_id}] Starting reflection ({len(errors)} errors, prompt {len(task_prompt)} chars)...")

    agent = create_agent(str(db_dir), AgentSpec(mode="reflection", effort="max"))

    t0 = time.time()
    try:
        response = agent.chat(task_prompt)
        elapsed = time.time() - t0
        print(f"[{db_id}] Done ({elapsed:.0f}s)")

        return {
            "db_id": db_id,
            "status": "ok",
            "elapsed": round(elapsed, 1),
        }
    except Exception as e:
        elapsed = time.time() - t0
        print(f"[{db_id}] Error: {e}")
        return {"db_id": db_id, "status": "error", "elapsed": round(elapsed, 1), "error": str(e)}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="BIRD Reflection — analyze logs, extract knowledge")
    parser.add_argument("--db", help="只分析指定数据库")
    parser.add_argument("--train", action="store_true", help="分析 train 日志")
    parser.add_argument("--dry-run", action="store_true", help="只生成 prompt 不运行")
    parser.add_argument("--min-errors", type=int, default=1, help="最少错误数才触发反思（默认 1）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s | %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.train:
        db_base = PROJECT_ROOT / "example_data" / "bird_train" / "train_databases"
    else:
        db_base = PROJECT_ROOT / "example_data" / "bird" / "dev_databases"

    if not db_base.exists():
        print(f"Error: {db_base} not found")
        sys.exit(1)

    # 找有 benchmark 日志的数据库
    db_ids = []
    for db_dir in sorted(db_base.iterdir()):
        bench_dir = db_dir / ".pontis" / "benchmark"
        if bench_dir.exists() and any(bench_dir.glob("*.brief.log")):
            if args.db and db_dir.name != args.db:
                continue
            db_ids.append(db_dir.name)

    if not db_ids:
        print("No benchmark logs found. Run run_bird_benchmark.py first.")
        sys.exit(1)

    print(f"=== BIRD Reflection Runner ===")
    print(f"Databases: {len(db_ids)}\n")

    results = []
    for db_id in db_ids:
        bench_dir = find_benchmark_dir(db_id, train=args.train)
        if not bench_dir:
            continue

        errors = parse_brief_logs(bench_dir)
        # 统计总数（含正确）
        total = len(list(bench_dir.glob("*.brief.log")))

        if len(errors) < args.min_errors:
            print(f"[{db_id}] {len(errors)} errors (< {args.min_errors}), skipping")
            continue

        if args.dry_run:
            prompt = build_task_prompt(db_id, errors, total)
            print(f"[{db_id}] {len(errors)} errors, prompt {len(prompt)} chars")
            results.append({"db_id": db_id, "status": "dry_run"})
            continue

        result = run_reflection(db_id, errors, total, train=args.train)
        results.append(result)

    ok = sum(1 for r in results if r["status"] == "ok")
    err = sum(1 for r in results if r["status"] == "error")
    print(f"\n=== Summary: OK={ok}, Errors={err} ===")


if __name__ == "__main__":
    main()
