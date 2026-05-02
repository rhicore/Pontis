#!/usr/bin/env python3
"""统计 BIRD benchmark 中每个数据库的 SQL 查询用了多少个表。

Usage:
    python scripts/analyze_sql_table_count.py
"""
import json
from pathlib import Path
from collections import defaultdict, Counter

import sqlglot
from sqlglot import exp

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIRD_DIR = PROJECT_ROOT / "example_data" / "bird"
DEV_JSON = BIRD_DIR / "dev.json"


def count_tables(sql: str) -> int:
    """统计 SQL 中涉及的独立表数量（含 FROM、JOIN、子查询中的表）。

    返回 0 表示解析失败。
    """
    try:
        parsed = sqlglot.parse_one(sql, read="sqlite")
    except Exception:
        return 0

    tables = set()
    for table in parsed.find_all(exp.Table):
        name = table.name.lower()
        if name:
            tables.add(name)

    return len(tables)


def count_joins(sql: str) -> int:
    """统计 SQL 中显式 JOIN 子句的数量。"""
    try:
        parsed = sqlglot.parse_one(sql, read="sqlite")
    except Exception:
        return 0

    return len(list(parsed.find_all(exp.Join)))


def main():
    data = json.loads(DEV_JSON.read_text(encoding="utf-8"))

    # 按 db 分组
    by_db = defaultdict(list)
    for q in data:
        by_db[q["db_id"]].append(q)

    print("=" * 70)
    print("BIRD dev.json 各数据库表数量与 JOIN 数量统计")
    print("=" * 70)

    # 全局统计
    all_table_counts = Counter()
    all_join_counts = Counter()

    for db_id in sorted(by_db.keys()):
        queries = by_db[db_id]
        table_counter = Counter()
        join_counter = Counter()

        for q in queries:
            t = count_tables(q["SQL"])
            j = count_joins(q["SQL"])
            table_counter[t] += 1
            join_counter[j] += 1
            all_table_counts[t] += 1
            all_join_counts[j] += 1

        print(f"\n【{db_id}】  共 {len(queries)} 条 SQL")
        print("  表数量分布:")
        for t in sorted(table_counter.keys()):
            pct = table_counter[t] / len(queries) * 100
            bar = "█" * int(pct / 2)
            print(f"    {t} 个表: {table_counter[t]:>3} 条 ({pct:5.1f}%) {bar}")

        print("  JOIN 数量分布:")
        for j in sorted(join_counter.keys()):
            pct = join_counter[j] / len(queries) * 100
            bar = "█" * int(pct / 2)
            print(f"    {j} 个 JOIN: {join_counter[j]:>3} 条 ({pct:5.1f}%) {bar}")

    # 全局汇总
    total = sum(all_table_counts.values())
    print("\n" + "=" * 70)
    print(f"【全局汇总】 共 {total} 条 SQL")
    print("  表数量分布:")
    for t in sorted(all_table_counts.keys()):
        pct = all_table_counts[t] / total * 100
        bar = "█" * int(pct / 2)
        print(f"    {t} 个表: {all_table_counts[t]:>4} 条 ({pct:5.1f}%) {bar}")

    print("  JOIN 数量分布:")
    for j in sorted(all_join_counts.keys()):
        pct = all_join_counts[j] / total * 100
        bar = "█" * int(pct / 2)
        print(f"    {j} 个 JOIN: {all_join_counts[j]:>4} 条 ({pct:5.1f}%) {bar}")
    print("=" * 70)


if __name__ == "__main__":
    main()
