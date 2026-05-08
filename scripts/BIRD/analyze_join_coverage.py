#!/usr/bin/env python3
"""分析提取出的关系实体与 BIRD benchmark 实际 JOIN 关系的覆盖情况。

Usage:
    python scripts/analyze_join_coverage.py --db california_schools
"""
import json
import argparse
import sys
from pathlib import Path
from collections import defaultdict

import sqlglot
from sqlglot import exp

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIRD_DIR = PROJECT_ROOT / "example_data" / "bird"
DEV_JSON = BIRD_DIR / "dev.json"
DB_BASE = BIRD_DIR / "dev_databases"


def load_dev_queries(db_id: str):
    """加载指定 db 的所有 golden SQL。"""
    data = json.loads(DEV_JSON.read_text(encoding="utf-8"))
    return [q for q in data if q["db_id"] == db_id]


def extract_joins_from_sql(sql: str) -> set:
    """从 SQL 中提取 JOIN 条件（表对 + 列对）。

    返回 {(table1, col1, table2, col2), ...}，表名和列名均小写。
    """
    results = set()
    try:
        parsed = sqlglot.parse_one(sql, read="sqlite")
    except Exception:
        return results

    # 收集所有表别名映射
    tables = {}  # alias -> table_name
    for table in parsed.find_all(exp.Table):
        name = table.name.lower()
        alias = table.alias.lower() if table.alias else name
        tables[alias] = name
        tables[name] = name  # 也注册本名

    # 查找 JOIN ON 条件
    for join in parsed.find_all(exp.Join):
        # 解析 ON 条件中的等值比较
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            left = _extract_col_ref(eq.left, tables)
            right = _extract_col_ref(eq.right, tables)
            if left and right:
                t1, c1 = left
                t2, c2 = right
                # 去重排序
                pair = tuple(sorted([(t1, c1), (t2, c2)]))
                results.add((*pair[0], *pair[1]))

    # 也检查 WHERE 中的隐式 JOIN（旧式逗号连接）
    where = parsed.find(exp.Where)
    if where:
        for eq in where.find_all(exp.EQ):
            left = _extract_col_ref(eq.left, tables)
            right = _extract_col_ref(eq.right, tables)
            if left and right:
                t1, c1 = left
                t2, c2 = right
                if t1 != t2:
                    pair = tuple(sorted([(t1, c1), (t2, c2)]))
                    results.add((*pair[0], *pair[1]))

    return results


def _extract_col_ref(node, tables: dict) -> tuple | None:
    """从表达式节点提取 (table, column)。"""
    if isinstance(node, exp.Column):
        table = node.table.lower() if node.table else ""
        col = node.name.lower()
        table = tables.get(table, table)
        return (table, col)
    return None


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


def extract_entities(workspace, db_ref: str):
    """从 workspace 中提取所有 .fk / .rel / .overlap 实体。

    返回三个集合，元素为 (table1, col1, table2, col2)，已规范化排序。
    """
    fk_set = set()
    rel_set = set()
    overlap_set = set()

    for ref in _find_nodes_by_suffix(workspace, db_ref, ".fk"):
        _, entity = ref.split("::", 1)
        fk_set.add(_normalize_relation(entity))

    for ref in _find_nodes_by_suffix(workspace, db_ref, ".rel"):
        _, entity = ref.split("::", 1)
        rel_set.add(_normalize_relation(entity))

    for ref in _find_nodes_by_suffix(workspace, db_ref, ".overlap"):
        _, entity = ref.split("::", 1)
        overlap_set.add(_normalize_relation(entity))

    return fk_set, rel_set, overlap_set


def _normalize_relation(entity_name: str) -> tuple:
    """把关系实体名解析为规范化的 (table1, col1, table2, col2)。"""
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

    # 规范化排序，便于和 SQL 提取的 JOIN 进行集合运算
    pair = tuple(sorted([(t1.lower(), c1.lower()), (t2.lower(), c2.lower())]))
    return (*pair[0], *pair[1])


def _render_relation(t: tuple) -> str:
    return f"{t[0]}.{t[1]}  ↔  {t[2]}.{t[3]}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="数据库 ID（如 california_schools）")
    args = parser.parse_args()

    db_id = args.db
    db_dir = DB_BASE / db_id
    if not db_dir.exists():
        print(f"Error: {db_dir} not found")
        sys.exit(1)

    sys.path.insert(0, str(PROJECT_ROOT))
    from storage.workspace import Workspace

    ws = Workspace(project_path=str(db_dir))

    # 找到 db ref
    db_nodes = _find_db_nodes(ws)
    db_ref = None
    if db_nodes:
        db_ref = db_nodes[0].split("::", 1)[0] + "::"
    if not db_ref:
        print("Error: cannot find database root in store")
        sys.exit(1)

    print(f"=== Database: {db_id} ===\n")

    # 1. 提取实体
    fk_set, rel_set, overlap_set = extract_entities(ws, db_ref)

    # REL 中与 FK 同方向的去掉（避免同一对既有 FK 又有 REL）
    # 但为了展示，我们先保留，后面交集分析时再处理

    # 2. 提取 SQL JOIN
    queries = load_dev_queries(db_id)
    sql_joins = set()
    for q in queries:
        sql_joins.update(extract_joins_from_sql(q["SQL"]))

    # 3. 计算各区域
    # 定义集合（REL 先排除与 FK 完全同向的）
    rel_only = rel_set - fk_set
    overlap_only = overlap_set - fk_set - rel_set

    # 与 SQL 的交集
    fk_sql = fk_set & sql_joins
    rel_sql = rel_only & sql_joins
    overlap_sql = overlap_only & sql_joins

    fk_not_sql = fk_set - sql_joins
    rel_not_sql = rel_only - sql_joins
    overlap_not_sql = overlap_only - sql_joins

    sql_not_covered = sql_joins - fk_set - rel_set - overlap_set
    sql_covered_by_any = sql_joins & (fk_set | rel_set | overlap_set)

    # 4. 打印统计
    print("┌─────────────────────────────────────────────────────────────┐")
    print("│                     实体统计                                │")
    print("├─────────────────────────────────────────────────────────────┤")
    print(f"│  FK 实体总数:        {len(fk_set):>3}                                    │")
    print(f"│  REL 实体总数:       {len(rel_set):>3}                                    │")
    print(f"│  OVERLAP 实体总数:   {len(overlap_set):>3}                                    │")
    print(f"│  去重后 unique 关系: {len(fk_set | rel_set | overlap_set):>3}                                    │")
    print("└─────────────────────────────────────────────────────────────┘\n")

    print("┌─────────────────────────────────────────────────────────────┐")
    print("│              dev.json 实际 JOIN 统计                        │")
    print("├─────────────────────────────────────────────────────────────┤")
    print(f"│  总 query 数:        {len(queries):>3}                                    │")
    print(f"│  唯一 JOIN 对数:     {len(sql_joins):>3}                                    │")
    print("└─────────────────────────────────────────────────────────────┘\n")

    # 5. 打印交集关系（Venn 式）
    print("==================== 覆盖交集分析 ====================\n")

    if fk_sql:
        print(f"【A】FK ∩ SQL  被物理外键覆盖且实际被使用  ({len(fk_sql)} 个)")
        for j in sorted(fk_sql):
            print(f"      ✓ {_render_relation(j)}")
        print()

    if rel_sql:
        print(f"【B】REL ∩ SQL  被 AI 推断覆盖且实际被使用  ({len(rel_sql)} 个)")
        for j in sorted(rel_sql):
            print(f"      ✓ {_render_relation(j)}")
        print()

    if overlap_sql:
        print(f"【C】OVERLAP ∩ SQL  被重叠检测覆盖且实际被使用  ({len(overlap_sql)} 个)")
        for j in sorted(overlap_sql):
            print(f"      ✓ {_render_relation(j)}")
        print()

    if fk_not_sql:
        print(f"【D】FK - SQL  提取了物理外键但 SQL 中未使用  ({len(fk_not_sql)} 个)")
        for j in sorted(fk_not_sql):
            print(f"      - {_render_relation(j)}")
        print()

    if rel_not_sql:
        print(f"【E】REL - SQL  提取了 AI 关系但 SQL 中未使用  ({len(rel_not_sql)} 个)")
        # 只打印前 10 个，避免刷屏
        for j in sorted(rel_not_sql)[:10]:
            print(f"      - {_render_relation(j)}")
        if len(rel_not_sql) > 10:
            print(f"      ... 还有 {len(rel_not_sql)-10} 个")
        print()

    if overlap_not_sql:
        print(f"【F】OVERLAP - SQL  提取了重叠但 SQL 中未使用  ({len(overlap_not_sql)} 个)")
        for j in sorted(overlap_not_sql):
            print(f"      - {_render_relation(j)}")
        print()

    if sql_not_covered:
        print(f"【G】SQL - (FK∪REL∪OVERLAP)  实际使用但未提取到  ({len(sql_not_covered)} 个)")
        for j in sorted(sql_not_covered):
            print(f"      ✗ {_render_relation(j)}")
        print()

    # 6. 汇总
    covered_by_fk = len(fk_sql)
    covered_by_rel_only = len(rel_sql)
    covered_by_overlap_only = len(overlap_sql)
    total_sql = len(sql_joins)
    total_covered = len(sql_covered_by_any)

    print("==================== 汇总 ====================\n")
    print(f"  实际 JOIN 总数:           {total_sql}")
    print(f"  被 FK 覆盖:               {covered_by_fk}  ({covered_by_fk/total_sql*100:.0f}%)")
    print(f"  被 REL 覆盖（无 FK）:     {covered_by_rel_only}  ({covered_by_rel_only/total_sql*100:.0f}%)")
    print(f"  被 OVERLAP 覆盖（无上两者）: {covered_by_overlap_only}  ({covered_by_overlap_only/total_sql*100:.0f}%)")
    print(f"  总计覆盖:                 {total_covered}  ({total_covered/total_sql*100:.0f}%)")
    print(f"  未覆盖:                   {len(sql_not_covered)}  ({len(sql_not_covered)/total_sql*100:.0f}%)")
    print()


if __name__ == "__main__":
    main()
