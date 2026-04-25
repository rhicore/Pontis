"""SQL 解析和 store 查询工具 — guardrail 业务逻辑层。

从各 guardrail 提取的通用业务逻辑，不涉及框架 API。
"""
import json
import re
from typing import Dict, List, Optional, Set, Tuple


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


# ═══════════════════════════════════════════════════════════
#  SQL 表 / 列 / JOIN 解析
# ═══════════════════════════════════════════════════════════

_TABLE_PATTERN = re.compile(
    r'\bFROM\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?|\bJOIN\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?',
    re.IGNORECASE)

_COL_REF_PATTERN = re.compile(r'\b(\w+)\.(\w+)\b')

_SQL_KEYWORDS = frozenset({
    'SELECT', 'FROM', 'WHERE', 'JOIN', 'ON', 'AND', 'OR', 'NOT', 'IN',
    'IS', 'AS', 'BY', 'ORDER', 'GROUP', 'HAVING', 'LIMIT', 'DISTINCT',
    'NULL', 'CAST', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END',
    'LEFT', 'RIGHT', 'INNER', 'OUTER', 'CROSS',
})

_FROM_PATTERN = re.compile(r'\bFROM\s+(\w+)', re.IGNORECASE)

_JOIN_PATTERN = re.compile(r'\bJOIN\s+(\w+)', re.IGNORECASE)

_TABLE_ONLY_PATTERN = re.compile(r'\bFROM\s+(\w+)|\bJOIN\s+(\w+)', re.IGNORECASE)


def extract_tables(sql: str) -> Tuple[Set[str], Dict[str, str]]:
    """提取表名和别名映射。"""
    tables = set()
    aliases: Dict[str, str] = {}
    for m in _TABLE_PATTERN.finditer(sql):
        table = m.group(1) or m.group(3)
        alias = m.group(2) or m.group(4)
        if table:
            tables.add(table)
            if alias and alias.lower() != table.lower():
                aliases[alias.lower()] = table
    return tables, aliases


def extract_col_refs(sql: str, aliases: Dict[str, str]) -> List[Tuple[str, str]]:
    """提取列引用 (table, col)，过滤 SQL 关键字。"""
    seen = set()
    result = []
    for m in _COL_REF_PATTERN.finditer(sql):
        prefix = m.group(1)
        col = m.group(2)
        if prefix.upper() in _SQL_KEYWORDS or prefix.isdigit():
            continue
        table = aliases.get(prefix.lower(), prefix)
        key = (table, col)
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def extract_join_pairs(sql: str) -> List[Tuple[str, str]]:
    """提取相邻 JOIN 表对（按 FROM → JOIN 顺序）。"""
    tables_in_order = []
    m = _FROM_PATTERN.search(sql)
    if m:
        tables_in_order.append(m.group(1).lower())
    for m in _JOIN_PATTERN.finditer(sql):
        tables_in_order.append(m.group(1).lower())

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
    tables = set()
    for m in _TABLE_ONLY_PATTERN.finditer(sql):
        tables.add(m.group(1) or m.group(2))
    return tables
