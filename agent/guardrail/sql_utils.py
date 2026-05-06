"""SQL 解析和 store 查询工具 — guardrail 业务逻辑层。

从各 guardrail 提取的通用业务逻辑，不涉及框架 API。
SQL 解析使用 sqlglot，正确处理字符串字面量、子查询等边界情况。
"""
import json
import re
from typing import Dict, List, Optional, Set, Tuple

import sqlglot


# ═══════════════════════════════════════════════════════════
#  实体列表格式化（与 glob/search 共用逻辑）
# ═══════════════════════════════════════════════════════════

def format_entity_list(store, refs: list[str]) -> str:
    """格式化实体列表，显示 brief（与 glob/search 一致的展示风格）。

    Args:
        store: Store 实例
        refs: 实体 ref 列表（如 "formula_1.sqlite::position.disambig"）

    Returns:
        格式化的多行字符串，每行 "  - ref | brief"
    """
    lines = []
    for ref in refs:
        meta = store.get_meta(ref, include_props=[]) if store else None
        if meta and meta.get("brief"):
            lines.append(f"  - {ref} | {meta['brief']}")
        else:
            lines.append(f"  - {ref}")
    return "\n".join(lines)
from sqlglot import exp


# ═══════════════════════════════════════════════════════════
#  SQL 提取
# ═══════════════════════════════════════════════════════════

def get_sql_from_calls(pending_calls: list) -> Optional[str]:
    """从 pending tool calls 中提取 SQL（query 工具的 sql 参数）。"""
    for name, args in pending_calls:
        if name == "query":
            sql = args.get("sql", "")
            if sql:
                return sql
    return None


def get_sql_from_messages(messages: list) -> Optional[str]:
    """从消息历史中提取最新 SQL（```sql ... ```）。"""
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            break
        content = msg.get("content", "")
        if not content:
            continue
        m = re.search(r'```sql\s*(.*?)\s*```', content, re.DOTALL)
        if m:
            return m.group(1)
    return None


def get_current_sql(ctx) -> Optional[str]:
    """当前 SQL：优先 pending_calls 中的 query，其次消息中的 SQL 块。

    如果有 pending_calls 但不含 query，返回 None（不检查非 query 调用）。
    """
    if ctx.pending_calls:
        sql = get_sql_from_calls(ctx.pending_calls)
        if sql:
            return sql
        return None
    return get_sql_from_messages(ctx.messages)


def get_db_prefix(ctx) -> str:
    """数据库文件前缀，如 "formula_1.sqlite::"。"""
    for name, args in ctx.pending_calls:
        if name == "query":
            f = args.get("file", "")
            if f:
                return f + "::"
    for msg in ctx.messages:
        for tc in msg.get("tool_calls") or []:
            if tc.get("function", {}).get("name") == "query":
                try:
                    args = json.loads(tc["function"]["arguments"])
                    f = args.get("file", "")
                    if f:
                        return f + "::"
                except (json.JSONDecodeError, KeyError):
                    pass
    return ""


# ═══════════════════════════════════════════════════════════
#  Meta 读取查询
# ═══════════════════════════════════════════════════════════

def get_meta_read(tool_history: list) -> frozenset:
    """从 tool_history 提取所有已读 meta 实体路径。"""
    read = set()
    for name, args, _ in tool_history:
        if name == "meta":
            path = args.get("path", "")
            entity = path.split("::", 1)[1] if "::" in path else path
            read.add(entity)
    return frozenset(read)


def has_read(tool_history: list, entity: str) -> bool:
    """检查实体是否已被 meta 读取。支持前缀匹配。"""
    if "::" in entity:
        entity = entity.split("::", 1)[1]
    read = get_meta_read(tool_history)
    if entity in read:
        return True
    return any(s.startswith(entity + ".") for s in read)


def resolve_entity_ref(store, table: str, column: str = None) -> str:
    """从 store 查找实体引用路径，大小写不敏感。

    table="qualifying", column=None → "qualifying"（如果 label 含 table）
    table="qualifying", column="raceId" → 匹配 label 含 col 的实体
    找不到时返回 None。
    """
    if store is None:
        return None
    if column is None:
        for ename, labels in store.list_all():
            if ename.lower() == table.lower() and any(
                l == "table" or l == "view" for l in labels
            ):
                return ename
    else:
        for ename, labels in store.list_all():
            if not any(l.startswith("col") for l in labels):
                continue
            # 实体名就是列名（新格式），需要检查邻接确认属于哪个表
            if ename.lower() == column.lower():
                return ename
    return None


# ═══════════════════════════════════════════════════════════
#  SQL 表 / 列 / JOIN 解析（sqlglot）
# ═══════════════════════════════════════════════════════════

_VALID_IDENTIFIER = re.compile(r'^[A-Za-z_]\w*$')


def _parse(sql: str) -> Optional[exp.Expression]:
    """解析 SQL，解析失败时返回 None。"""
    try:
        return sqlglot.parse_one(sql, dialect="sqlite")
    except sqlglot.errors.ParseError:
        return None


def extract_tables(sql: str) -> Tuple[Set[str], Dict[str, str]]:
    """提取表名和别名映射。"""
    tree = _parse(sql)
    if tree is None:
        return set(), {}

    tables: Set[str] = set()
    aliases: Dict[str, str] = {}
    for t in tree.find_all(exp.Table):
        name = t.name
        alias = t.alias
        tables.add(name)
        if alias and alias.lower() != name.lower():
            aliases[alias.lower()] = name
    return tables, aliases


def extract_col_refs(sql: str, aliases: Dict[str, str] = None) -> List[Tuple[str, str]]:
    """提取列引用 (table, col)，自动解析别名。

    有限定前缀的列（如 t.col）直接解析。
    无限定列（如 col）：单表查询时归属到该表；多表查询时无法确定归属，跳过。
    """
    tree = _parse(sql)
    if tree is None:
        return []

    _aliases = aliases or {}
    all_tables: Set[str] = set()
    for t in tree.find_all(exp.Table):
        all_tables.add(_aliases.get(t.name.lower(), t.name) if t.alias
                       else t.name)
    single_table = next(iter(all_tables)) if len(all_tables) == 1 else None

    seen: Set[Tuple[str, str]] = set()
    result: List[Tuple[str, str]] = []
    for col in tree.find_all(exp.Column):
        col_name = col.name
        if not _VALID_IDENTIFIER.match(col_name):
            continue
        table_prefix = col.table
        if table_prefix:
            resolved = _aliases.get(table_prefix.lower(), table_prefix)
        elif single_table:
            resolved = single_table
        else:
            continue
        key = (resolved, col_name)
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def extract_join_pairs(sql: str) -> List[Tuple[str, str]]:
    """提取相邻 JOIN 表对（按 FROM → JOIN 顺序）。"""
    tree = _parse(sql)
    if tree is None:
        return []

    tables_in_order: List[str] = []
    from_clause = tree.find(exp.From)
    if from_clause:
        for t in from_clause.find_all(exp.Table):
            tables_in_order.append(t.name.lower())
    for join in tree.find_all(exp.Join):
        for t in join.find_all(exp.Table):
            tables_in_order.append(t.name.lower())

    if len(tables_in_order) < 2:
        return []

    pairs = []
    for i in range(len(tables_in_order) - 1):
        t1, t2 = tables_in_order[i], tables_in_order[i + 1]
        if t1 != t2:
            pairs.append((t1, t2))
    return pairs


def extract_table_names(sql: str) -> Set[str]:
    """提取所有表名（不含别名）。"""
    tree = _parse(sql)
    if tree is None:
        return set()
    return {t.name for t in tree.find_all(exp.Table)}


def extract_join_col_pairs(sql: str) -> List[Tuple[str, str, str, str]]:
    """提取 JOIN 的列对 (table1, col1, table2, col2)。

    从 ON 子句中的等值条件提取，如
    `JOIN results r ON ds.driverId = r.driverId` →
    `('driverstandings', 'driverId', 'results', 'driverId')`
    """
    tree = _parse(sql)
    if tree is None:
        return []

    # 构建别名映射
    aliases: Dict[str, str] = {}
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        if t.alias:
            aliases[t.alias.lower()] = name

    pairs: List[Tuple[str, str, str, str]] = []
    for join in tree.find_all(exp.Join):
        join_table = join.find(exp.Table)
        if not join_table:
            continue
        right_table = join_table.name.lower()

        # ON 子句中的等值条件
        for eq in join.find_all(exp.EQ):
            left = eq.left
            right = eq.right
            if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                continue
            t1 = aliases.get(left.table.lower(), left.table.lower()) if left.table else ""
            t2 = aliases.get(right.table.lower(), right.table.lower()) if right.table else ""
            c1 = left.name
            c2 = right.name
            if not t1 or not t2:
                continue
            pairs.append((t1, c1, t2, c2))

    return pairs
