"""SQL 实体审查 — SQL 中涉及的每个表和列都必须通过 meta 读取，缺一不可。"""
import re
from typing import Dict, List, Optional, Set, Tuple

from agent.guardrail import Guardrail, AgentState, _get_db_prefix


class SQLEntityCheck(Guardrail):
    """SQL 全量实体审查：表 + 列必须全部 meta 过。"""

    _TABLE_PATTERN = re.compile(
        r'\bFROM\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?|\bJOIN\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?',
        re.IGNORECASE)
    _COL_REF_PATTERN = re.compile(r'\b(\w+)\.(\w+)\b')

    def __init__(self):
        self._meta_read: Set[str] = set()
        self._sync_idx: int = 0

    def _sync(self, state: AgentState):
        """从 tool_history 增量同步已读 meta 实体。"""
        for name, args, _ in state.tool_history[self._sync_idx:]:
            if name == "meta":
                path = args.get("path", "")
                entity_part = path.split("::", 1)[1] if "::" in path else path
                self._meta_read.add(entity_part)
        self._sync_idx = len(state.tool_history)

    def _has_read(self, entity_path: str) -> bool:
        if "::" in entity_path:
            entity_path = entity_path.split("::", 1)[1]
        if entity_path in self._meta_read:
            return True
        return any(s.startswith(entity_path + ".") for s in self._meta_read)

    def check(self, state: AgentState, pending_calls: list) -> Optional[str]:
        self._sync(state)

        sql = self._get_sql(state, pending_calls)
        if not sql:
            return None

        tables, aliases = self._extract_tables(sql)
        if not tables:
            return None

        db_prefix = _get_db_prefix(state, pending_calls)

        missing = []
        for t in sorted(tables):
            if not self._has_read(f"{t}.table"):
                missing.append(f"{db_prefix}{t}.table" if db_prefix else f"{t}.table")

        col_refs = self._extract_col_refs(sql, aliases)
        for table, col in col_refs:
            key = f"{table}.{col}"
            if not self._has_read(key):
                missing.append(f"{db_prefix}{key}" if db_prefix else key)

        if not missing:
            return None

        items = "\n".join(f"  - {m}" for m in missing[:12])
        return ("⚠️ 以下实体尚未通过 meta 读取，必须全部读取后才能输出 SQL：\n"
                + items
                + "\n请读取以上实体后重新生成SQL。")

    # ──────────────── SQL 提取 ────────────────

    @staticmethod
    def _get_sql(state: AgentState, pending_calls: list) -> Optional[str]:
        for name, args in pending_calls:
            if name == "query":
                sql = args.get("sql", "")
                if sql:
                    return sql
        if pending_calls:
            return None
        for msg in reversed(state.messages):
            if msg.get("role") != "assistant":
                break
            content = msg.get("content", "")
            if not content:
                continue
            m = re.search(r'```sql\s*(.*?)\s*```', content, re.DOTALL)
            if m:
                return m.group(1)
        return None

    # ──────────────── 表 + 别名 ────────────────

    @staticmethod
    def _extract_tables(sql: str) -> Tuple[Set[str], Dict[str, str]]:
        tables = set()
        aliases: Dict[str, str] = {}
        for m in SQLEntityCheck._TABLE_PATTERN.finditer(sql):
            table = m.group(1) or m.group(3)
            alias = m.group(2) or m.group(4)
            if table:
                tables.add(table)
                if alias and alias.lower() != table.lower():
                    aliases[alias.lower()] = table
        return tables, aliases

    # ──────────────── 列引用 ────────────────

    @staticmethod
    def _extract_col_refs(sql: str, aliases: Dict[str, str]) -> List[Tuple[str, str]]:
        seen = set()
        result = []
        for m in SQLEntityCheck._COL_REF_PATTERN.finditer(sql):
            prefix = m.group(1)
            col = m.group(2)
            if prefix.upper() in ('SELECT', 'FROM', 'WHERE', 'JOIN', 'ON', 'AND',
                                   'OR', 'NOT', 'IN', 'IS', 'AS', 'BY', 'ORDER',
                                   'GROUP', 'HAVING', 'LIMIT', 'DISTINCT', 'NULL',
                                   'CAST', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END',
                                   'LEFT', 'RIGHT', 'INNER', 'OUTER', 'CROSS'):
                continue
            table = aliases.get(prefix.lower(), prefix)
            key = (table, col)
            if key not in seen:
                seen.add(key)
                result.append(key)
        return result
