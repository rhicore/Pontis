#!/usr/bin/env python3
"""批量分析所有 BIRD 数据库的关系实体覆盖情况，并汇总为表格。

Usage:
    python scripts/analyze_all_join_coverage.py
"""
import json
import sys
from pathlib import Path
from collections import defaultdict, Counter

import sqlglot
from sqlglot import exp

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIRD_DIR = PROJECT_ROOT / "example_data" / "bird"
DEV_JSON = BIRD_DIR / "dev.json"
DB_BASE = BIRD_DIR / "dev_databases"

sys.path.insert(0, str(PROJECT_ROOT))
from storage import Store


def count_tables(sql: str) -> int:
    try:
        parsed = sqlglot.parse_one(sql, read="sqlite")
        return len({t.name.lower() for t in parsed.find_all(exp.Table) if t.name})
    except Exception:
        return 0


def count_joins(sql: str) -> int:
    try:
        parsed = sqlglot.parse_one(sql, read="sqlite")
        return len(list(parsed.find_all(exp.Join)))
    except Exception:
        return 0


def extract_sql_joins(sql: str) -> set:
    results = set()
    try:
        parsed = sqlglot.parse_one(sql, read="sqlite")
    except Exception:
        return results

    tables = {}
    for table in parsed.find_all(exp.Table):
        name = table.name.lower()
        alias = table.alias.lower() if table.alias else name
        tables[alias] = name
        tables[name] = name

    for join in parsed.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue
        for eq in on_clause.find_all(exp.EQ):
            left = _extract_col(eq.left, tables)
            right = _extract_col(eq.right, tables)
            if left and right:
                pair = tuple(sorted([left, right]))
                results.add((*pair[0], *pair[1]))

    where = parsed.find(exp.Where)
    if where:
        for eq in where.find_all(exp.EQ):
            left = _extract_col(eq.left, tables)
            right = _extract_col(eq.right, tables)
            if left and right and left[0] != right[0]:
                pair = tuple(sorted([left, right]))
                results.add((*pair[0], *pair[1]))

    return results


def _extract_col(node, tables: dict):
    if isinstance(node, exp.Column):
        table = node.table.lower() if node.table else ""
        col = node.name.lower()
        return (tables.get(table, table), col)
    return None


def normalize_entity(entity_name: str) -> tuple:
    for suffix in [".fk", ".rel", ".overlap"]:
        if entity_name.endswith(suffix):
            entity_name = entity_name[: -len(suffix)]
            break

    if "__to__" in entity_name:
        left, right = entity_name.split("__to__", 1)
    elif "__rel__" in entity_name:
        left, right = entity_name.split("__rel__", 1)
    else:
        return (entity_name, "", "", "")

    lp = left.rsplit(".", 1)
    rp = right.rsplit(".", 1)
    t1 = lp[0] if len(lp) >= 1 else ""
    c1 = lp[1] if len(lp) >= 2 else ""
    t2 = rp[0] if len(rp) >= 1 else ""
    c2 = rp[1] if len(rp) >= 2 else ""

    pair = tuple(sorted([(t1.lower(), c1.lower()), (t2.lower(), c2.lower())]))
    return (*pair[0], *pair[1])


def analyze_db(db_id: str):
    db_dir = DB_BASE / db_id
    if not db_dir.exists():
        return None

    # Load queries
    data = json.loads(DEV_JSON.read_text(encoding="utf-8"))
    queries = [q for q in data if q["db_id"] == db_id]

    # Table / join stats
    table_counts = Counter()
    join_counts = Counter()
    sql_joins = set()

    for q in queries:
        t = count_tables(q["SQL"])
        j = count_joins(q["SQL"])
        table_counts[t] += 1
        join_counts[j] += 1
        sql_joins.update(extract_sql_joins(q["SQL"]))

    # Load store
    store = Store(str(db_dir))
    db_ref = None
    for pattern in ["*.db::*", "*.sqlite::*", "*.sqlite3::*", "*.duckdb::*"]:
        nodes = list(store.find_nodes(pattern))
        if nodes:
            db_ref = nodes[0].split("::", 1)[0] + "::"
            break

    fk_set, rel_set, overlap_set = set(), set(), set()
    if db_ref:
        for ref in store.find_nodes(f"{db_ref}*.fk"):
            fk_set.add(normalize_entity(ref.split("::", 1)[1]))
        for ref in store.find_nodes(f"{db_ref}*.rel"):
            rel_set.add(normalize_entity(ref.split("::", 1)[1]))
        for ref in store.find_nodes(f"{db_ref}*.overlap"):
            overlap_set.add(normalize_entity(ref.split("::", 1)[1]))

    rel_only = rel_set - fk_set
    overlap_only = overlap_set - fk_set - rel_only

    fk_sql = fk_set & sql_joins
    rel_sql = rel_only & sql_joins
    overlap_sql = overlap_only & sql_joins
    covered_any = sql_joins & (fk_set | rel_only | overlap_only)
    not_covered = sql_joins - (fk_set | rel_only | overlap_only)

    return {
        "db_id": db_id,
        "queries": len(queries),
        "table_counts": dict(table_counts),
        "join_counts": dict(join_counts),
        "sql_joins": len(sql_joins),
        "fk_total": len(fk_set),
        "rel_total": len(rel_set),
        "rel_only": len(rel_only),
        "overlap_total": len(overlap_set),
        "overlap_only": len(overlap_only),
        "fk_sql": len(fk_sql),
        "rel_sql": len(rel_sql),
        "overlap_sql": len(overlap_sql),
        "covered": len(covered_any),
        "not_covered": len(not_covered),
        "not_covered_list": sorted(not_covered),
    }


def main():
    data = json.loads(DEV_JSON.read_text(encoding="utf-8"))
    db_ids = sorted({q["db_id"] for q in data})

    results = []
    for db_id in db_ids:
        r = analyze_db(db_id)
        if r:
            results.append(r)

    print("=" * 120)
    print("BIRD 全库 JOIN 覆盖分析汇总")
    print("=" * 120)

    # Header
    print(f"{'Database':<28} {'Queries':>7} {'1T%':>5} {'2T%':>5} {'3T+%':>5} {'0J%':>5} {'1J%':>5} {'2J+%':>5} {'FK':>3} {'REL*':>4} {'OV*':>4} {'SQL_J':>5} {'FKcov':>5} {'RELcov':>6} {'Total%':>6}")
    print("-" * 125)

    total_queries = 0
    total_sql_joins = 0
    total_covered = 0

    for r in results:
        tc = r["table_counts"]
        jc = r["join_counts"]
        q = r["queries"]

        pct_1t = tc.get(1, 0) / q * 100
        pct_2t = tc.get(2, 0) / q * 100
        pct_3t_plus = sum(c for t, c in tc.items() if t >= 3) / q * 100

        pct_0j = jc.get(0, 0) / q * 100
        pct_1j = jc.get(1, 0) / q * 100
        pct_2j_plus = sum(c for j, c in jc.items() if j >= 2) / q * 100

        fk_cov = r["fk_sql"]
        rel_cov = r["rel_sql"]
        total_cov_pct = r["covered"] / r["sql_joins"] * 100 if r["sql_joins"] else 0

        print(f"{r['db_id']:<28} {q:>7} {pct_1t:>4.0f}% {pct_2t:>4.0f}% {pct_3t_plus:>4.0f}% {pct_0j:>4.0f}% {pct_1j:>4.0f}% {pct_2j_plus:>4.0f}% {r['fk_total']:>3} {r['rel_only']:>4} {r['overlap_only']:>4} {r['sql_joins']:>5} {fk_cov:>5} {rel_cov:>6} {total_cov_pct:>5.0f}%")

        total_queries += q
        total_sql_joins += r["sql_joins"]
        total_covered += r["covered"]

    print("-" * 125)
    print(f"{'TOTAL / AVG':<28} {total_queries:>7}")
    print(f"{'Overall JOIN coverage':<28} {total_covered}/{total_sql_joins} = {total_covered/total_sql_joins*100:.1f}%")
    print("=" * 125)

    # 打印未覆盖详情
    print("\n未覆盖 JOIN 详情（SQL 使用但未提取到任何关系实体）：\n")
    for r in results:
        if r["not_covered"]:
            print(f"【{r['db_id']}】 {r['not_covered']} 个未覆盖:")
            for j in r["not_covered_list"]:
                print(f"    {j[0]}.{j[1]}  ↔  {j[2]}.{j[3]}")
            print()


if __name__ == "__main__":
    main()
