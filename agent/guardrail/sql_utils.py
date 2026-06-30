"""SQL 解析和 store 查询工具 — guardrail 业务逻辑层。

从各 guardrail 提取的通用业务逻辑，不涉及框架 API。
SQL 解析使用 sqlglot，正确处理字符串字面量、子查询等边界情况。
"""
import json
import re
from typing import Dict, List, Optional, Set, Tuple

import sqlglot


# ═══════════════════════════════════════════════════════════
#  实体列表格式化（与实体发现工具共用逻辑）
# ═══════════════════════════════════════════════════════════

def format_entity_list(workspace, refs: list[str]) -> str:
    """格式化实体列表，显示 brief（与实体发现工具一致的展示风格）。

    Args:
        workspace: Workspace 实例
        refs: 实体 ref 列表（如 "formula_1.sqlite::position.disambig"）

    Returns:
        格式化的多行字符串，每行 "  - ref | brief"
    """
    lines = []
    for ref in refs:
        meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": ref})
        meta = meta_rows[0].get("n") if meta_rows else None
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
            f = args.get("ref", "") or args.get("file", "")
            if f:
                return f + "::"
    for msg in ctx.messages:
        for tc in msg.get("tool_calls") or []:
            if tc.get("function", {}).get("name") == "query":
                try:
                    args = json.loads(tc["function"]["arguments"])
                    f = args.get("ref", "") or args.get("file", "")
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
        if name != "meta":
            continue
        ref = args.get("ref", "") or args.get("path", "")
        for entity in _normalized_ref_variants(ref):
            read.add(entity)
    return frozenset(read)


def get_seen_entities(tool_history: list) -> frozenset:
    """从 find 结果中提取已经显式展示过的实体引用。"""
    seen = set()
    for name, _args, output in tool_history:
        if name != "find":
            continue
        text = str(output or "")
        if not text or text.startswith("No objects found") or text.startswith("Error:"):
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("("):
                continue
            head = line.split("\t", 1)[0].strip()
            if not head or head in {".:dir", "."}:
                continue
            if "::\t" in line:
                head = line.split("\t", 2)[1].strip()
            for entity in _normalized_ref_variants(head):
                seen.add(entity)
    return frozenset(seen)


def _normalized_ref_variants(entity: str) -> set[str]:
    variants = set()
    if not entity:
        return variants
    local = entity.split("::", 1)[1] if "::" in entity else entity
    variants.add(local)

    if "/" in local:
        parts = [p for p in local.split("/") if p]
        normalized_parts = [p.split(":", 1)[0] if ":" in p else p for p in parts]
        normalized = "/".join(normalized_parts)
        variants.add(normalized)
        if normalized_parts:
            variants.add(normalized_parts[-1])
    elif ":" in local:
        variants.add(local.split(":", 1)[0])

    return {v for v in variants if v}


def has_read(tool_history: list, entity: str) -> bool:
    """检查实体是否已被 meta 读取。"""
    read = get_meta_read(tool_history)
    seen = get_seen_entities(tool_history)
    targets = _normalized_ref_variants(entity)
    if any(target in read or target in seen for target in targets):
        return True

    # SQL 解析阶段常拿到裸 table/column 名，而实际 meta 读取常是
    # `db/table` 或 `db/table/column` 这种 scoped ref。这里做一个轻量后缀匹配，
    # 让“已读取 scoped ref”能够满足同名裸实体检查。
    for target in targets:
        suffixes = (f"/{target}", f".{target}")
        if any(r.endswith(suffixes) for r in read) or any(r.endswith(suffixes) for r in seen):
            return True
    return False


def has_meta_read(tool_history: list, entity: str) -> bool:
    """检查实体是否已被 meta 工具实际读取。

    Unlike has_read(), this does not count find output as a read. Use it for
    guardrails where showing an entity in search results is not enough.
    """
    read = get_meta_read(tool_history)
    targets = _normalized_ref_variants(entity)
    if any(target in read for target in targets):
        return True

    for target in targets:
        suffixes = (f"/{target}", f".{target}")
        if any(r.endswith(suffixes) for r in read):
            return True
    return False


def resolve_entity_ref(workspace, table: str, column: str = None) -> str:
    """从 workspace 查找实体引用路径，大小写不敏感。

    table="qualifying", column=None → "qualifying"（如果 label 含 table）
    table="qualifying", column="raceId" → 匹配 label 含 col 的实体
    找不到时返回 None。
    """
    if workspace is None:
        return None
    if column is None:
        rows = workspace.cypher("MATCH (f:file)--(t) RETURN f, t")
        for row in rows:
            tmeta = row.get("t", {}) or {}
            fmeta = row.get("f", {}) or {}
            ename = tmeta.get("name", "")
            labels = tmeta.get("labels", [])
            if ename.lower() == table.lower() and any(l in {"table", "view"} for l in labels):
                fname = fmeta.get("name", "")
                return f"{fname}/{ename}" if fname else ename
    else:
        rows = workspace.cypher("MATCH (f:file)--(t)--(c:col) RETURN f, t, c")
        for row in rows:
            cmeta = row.get("c", {}) or {}
            tmeta = row.get("t", {}) or {}
            fmeta = row.get("f", {}) or {}
            c_name = cmeta.get("name", "")
            t_name = tmeta.get("name", "")
            if c_name.lower() != column.lower():
                continue
            if t_name.lower() != table.lower():
                continue
            f_name = fmeta.get("name", "")
            if f_name and t_name:
                return f"{f_name}/{t_name}/{c_name}"
            if t_name:
                return f"{t_name}/{c_name}"
            return c_name
    return None


# ═══════════════════════════════════════════════════════════
#  SQL 表 / 列 / JOIN 解析（sqlglot）
# ═══════════════════════════════════════════════════════════

_VALID_IDENTIFIER = re.compile(r'^[A-Za-z_]\w*$')


def _parse(sql: str) -> Optional[exp.Expression]:
    """解析 SQL，解析失败时返回 None。"""
    try:
        return sqlglot.parse_one(sql, dialect="sqlite")
    except sqlglot.errors.SqlglotError:
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

    # 构建真实表名与别名映射。子查询/CTE alias 不在这里登记；
    # JOIN 到派生表时，不应把派生表 alias 当作图谱表名检查。
    aliases: Dict[str, str] = {}
    table_names: Set[str] = set()
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        table_names.add(name)
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
            left_prefix = left.table.lower() if left.table else ""
            right_prefix = right.table.lower() if right.table else ""
            if not left_prefix or not right_prefix:
                continue
            if left_prefix not in aliases and left_prefix not in table_names:
                continue
            if right_prefix not in aliases and right_prefix not in table_names:
                continue
            t1 = aliases.get(left_prefix, left_prefix)
            t2 = aliases.get(right_prefix, right_prefix)
            c1 = left.name
            c2 = right.name
            pairs.append((t1, c1, t2, c2))

    return pairs
