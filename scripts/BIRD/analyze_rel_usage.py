#!/usr/bin/env python3
"""分析 benchmark 日志中 agent 是否错误使用了 REL 实体做 JOIN。

对每个 log:
1. 提取 agent 通过 meta 读取的 .rel 实体
2. 提取最终 predicted SQL 中的 JOIN 条件
3. 判断 JOIN 条件是否匹配某个 REL（而非 FK）
4. 统计结果

Usage:
    python scripts/analyze_rel_usage.py --db california_schools
"""
import json
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict

import sqlglot
from sqlglot import exp

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIRD_DIR = PROJECT_ROOT / "example_data" / "bird"
DEV_JSON = BIRD_DIR / "dev.json"
DB_BASE = BIRD_DIR / "dev_databases"

# ── 日志解析 ──

_META_RE = re.compile(r'Tool call: meta\(\{"path":\s*"([^"]+\.rel)"')
_SQL_BLOCK_RE = re.compile(r"```sql\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
_PREDICTED_SQL_RE = re.compile(r"Predicted SQL:\s*(.+)")
_RESULT_RE = re.compile(r"Result:\s+(\w+)")


def parse_log(log_path: Path) -> dict:
    """解析单个 benchmark log。"""
    text = log_path.read_text(encoding="utf-8", errors="ignore")

    # 1. 提取 agent 读取的 .rel 实体
    rels_read = _META_RE.findall(text)

    # 2. 提取 predicted SQL
    predicted_sql = None
    m = _PREDICTED_SQL_RE.search(text)
    if m:
        predicted_sql = m.group(1).strip()

    # 3. 提取结果
    result = "UNKNOWN"
    m = _RESULT_RE.search(text)
    if m:
        result = m.group(1)

    # 4. 从 predicted SQL 提取 JOIN 条件
    joins = set()
    if predicted_sql and predicted_sql != "PARSE_ERROR":
        joins = extract_joins_from_sql(predicted_sql)

    return {
        "qid": int(log_path.stem[1:]),
        "rels_read": rels_read,
        "predicted_sql": predicted_sql,
        "joins": joins,
        "result": result,
    }


def extract_joins_from_sql(sql: str) -> set:
    """从 SQL 中提取 JOIN 条件 {(t1, c1, t2, c2), ...}。"""
    results = set()
    try:
        parsed = sqlglot.parse_one(sql, read="sqlite")
    except Exception:
        return results

    tables = {}
    for t in parsed.find_all(exp.Table):
        name = t.name.lower()
        alias = t.alias.lower() if t.alias else name
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


# ── REL 实体解析 ──

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
        return None

    # 从完整 ref 中取最后一段
    if "::" in left:
        left = left.split("::")[-1]
    if "::" in right:
        right = right.split("::")[-1]

    lp = left.rsplit(".", 1)
    rp = right.rsplit(".", 1)
    t1 = lp[0] if len(lp) >= 1 else ""
    c1 = lp[1] if len(lp) >= 2 else ""
    t2 = rp[0] if len(rp) >= 1 else ""
    c2 = rp[1] if len(rp) >= 2 else ""

    pair = tuple(sorted([(t1.lower(), c1.lower()), (t2.lower(), c2.lower())]))
    return (*pair[0], *pair[1])


def _find_nodes_by_suffix(workspace, prefix: str, suffix: str) -> list:
    """通过 Cypher 查找 name 以 prefix 开头且以 suffix 结尾的节点。"""
    rows = workspace.cypher(
        "MATCH (n) WHERE n.name STARTS WITH $prefix AND n.name ENDS WITH $suffix RETURN n.name AS name",
        params={"prefix": prefix, "suffix": suffix}
    )
    return [r["name"] for r in rows if r.get("name")]


def _find_db_nodes(workspace) -> list:
    """查找数据库根节点。"""
    for ext in [".db::", ".sqlite::", ".sqlite3::", ".duckdb::"]:
        rows = workspace.cypher(
            "MATCH (n) WHERE n.name CONTAINS $ext RETURN n.name AS name",
            params={"ext": ext}
        )
        names = [r["name"] for r in rows if r.get("name")]
        if names:
            return names
    return []


def load_fk_rel_entities(db_id: str):
    """加载 FK 和 REL 实体集合。"""
    sys.path.insert(0, str(PROJECT_ROOT))
    from storage.workspace import Workspace

    db_dir = DB_BASE / db_id
    ws = Workspace(project_path=str(db_dir))
    db_nodes = _find_db_nodes(ws)
    db_ref = None
    if db_nodes:
        db_ref = db_nodes[0].split("::", 1)[0] + "::"
    if not db_ref:
        return set(), set()

    fk_set, rel_set = set(), set()
    for ref in _find_nodes_by_suffix(ws, db_ref, ".fk"):
        n = normalize_entity(ref.split("::", 1)[1])
        if n:
            fk_set.add(n)
    for ref in _find_nodes_by_suffix(ws, db_ref, ".rel"):
        n = normalize_entity(ref.split("::", 1)[1])
        if n:
            rel_set.add(n)

    # REL-only = 不与 FK 重叠的
    rel_only = rel_set - fk_set
    return fk_set, rel_only


# ── 主流程 ──

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    args = parser.parse_args()

    db_id = args.db
    bench_dir = DB_BASE / db_id / ".pontis" / "benchmark"
    if not bench_dir.exists():
        print(f"No benchmark logs found for {db_id}")
        sys.exit(1)

    # 加载 FK/REL
    fk_set, rel_only = load_fk_rel_entities(db_id)
    print(f"FK entities: {len(fk_set)}")
    print(f"REL-only entities (excl FK overlap): {len(rel_only)}")
    print()

    # 加载 golden SQL 的正确 JOIN
    data = json.loads(DEV_JSON.read_text(encoding="utf-8"))
    golden_joins = {}  # qid -> set of joins
    for q in data:
        if q["db_id"] == db_id:
            golden_joins[q["question_id"]] = extract_joins_from_sql(q["SQL"])

    # 解析所有 log
    logs = sorted(bench_dir.glob("q*.log"))
    print(f"Benchmark logs: {len(logs)}\n")

    # 分类统计
    rel_read_count = 0           # 读过 .rel 实体的 query 数
    rel_used_in_join = 0         # JOIN 条件匹配 REL-only 的 query 数
    rel_used_correct = 0         # 用了 REL JOIN 但结果正确
    rel_used_wrong = 0           # 用了 REL JOIN 且结果错误
    rel_only_join = 0            # JOIN 只匹配 REL-only（不匹配 FK）
    rel_only_join_wrong = 0      # 上面这些里面结果错误的

    details = []

    for log_path in logs:
        info = parse_log(log_path)
        qid = info["qid"]
        rels = info["rels_read"]
        joins = info["joins"]
        result = info["result"]

        if not rels:
            continue

        rel_read_count += 1

        # 检查 JOIN 条件是否匹配 REL-only
        matched_rel_joins = joins & rel_only
        matched_fk_joins = joins & fk_set
        # JOIN 条件匹配 REL 但不匹配 FK（纯 REL JOIN）
        pure_rel_joins = matched_rel_joins - matched_fk_joins

        if matched_rel_joins:
            rel_used_in_join += 1
            if "CORRECT" in result:
                rel_used_correct += 1
            else:
                rel_used_wrong += 1

        if pure_rel_joins:
            rel_only_join += 1
            if "CORRECT" not in result:
                rel_only_join_wrong += 1
                details.append({
                    "qid": qid,
                    "result": result,
                    "rel_joins": pure_rel_joins,
                    "fk_joins": matched_fk_joins,
                    "all_joins": joins,
                    "golden_joins": golden_joins.get(qid, set()),
                    "rels_read": rels,
                    "predicted_sql": info["predicted_sql"],
                })

    # 输出
    print("=" * 70)
    print(f"读过 .rel 实体的 query: {rel_read_count}")
    print(f"JOIN 条件匹配 REL-only: {rel_used_in_join}")
    print(f"  其中 CORRECT: {rel_used_correct}")
    print(f"  其中 WRONG/ERROR: {rel_used_wrong}")
    print(f"纯 REL JOIN（不匹配 FK）: {rel_only_join}")
    print(f"  其中结果错误: {rel_only_join_wrong}")
    print("=" * 70)

    if details:
        print(f"\n--- 错误使用 REL JOIN 的详情 ({len(details)} 条) ---\n")
        for d in details:
            print(f"Q{d['qid']} [{d['result']}]")
            print(f"  纯 REL JOIN:")
            for j in d["rel_joins"]:
                print(f"    {j[0]}.{j[1]} <-> {j[2]}.{j[3]}")
            if d["fk_joins"]:
                print(f"  同时也有 FK JOIN:")
                for j in d["fk_joins"]:
                    print(f"    {j[0]}.{j[1]} <-> {j[2]}.{j[3]}")
            print(f"  Golden SQL JOIN:")
            for j in d["golden_joins"]:
                print(f"    {j[0]}.{j[1]} <-> {j[2]}.{j[3]}")
            print(f"  读过的 REL:")
            for r in d["rels_read"]:
                short = r.split("::", 1)[-1] if "::" in r else r
                print(f"    {short}")
            sql = d["predicted_sql"] or ""
            print(f"  Predicted SQL: {sql[:150]}{'...' if len(sql)>150 else ''}")
            print()


if __name__ == "__main__":
    main()
