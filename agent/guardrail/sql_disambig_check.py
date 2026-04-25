"""SQL 语义消歧检查 — SQL 涉及的消歧实体必须全部通过 meta 读取，缺一不可。"""
import re
from typing import List, Optional, Set, Tuple

from agent.guardrail import Guardrail, AgentState


class SQLDisambigCheck(Guardrail):
    """SQL 语义消歧审查：所有相关 disambig 实体必须读过。"""

    _TABLE_PATTERN = re.compile(r'\bFROM\s+(\w+)|\bJOIN\s+(\w+)', re.IGNORECASE)

    def __init__(self):
        self._meta_read: Set[str] = set()
        self._sync_idx: int = 0
        self._disambig_cache: Optional[List[Tuple[str, str, Set[str]]]] = None

    def _sync(self, state: AgentState):
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

    # ──────────────── 入口 ────────────────

    def check(self, state: AgentState, pending_calls: list) -> Optional[str]:
        self._sync(state)

        sql = self._get_sql(state, pending_calls)
        if not sql:
            return None

        tables = self._extract_tables(sql)
        cache = self._build_disambig_cache(state.store)
        if not cache:
            return None

        tables_lower = {t.lower() for t in tables}

        unread = []
        for ref, term, disambig_tables in cache:
            if self._has_read(ref):
                continue
            if disambig_tables & tables_lower:
                unread.append(ref)
            elif re.search(r'\b' + re.escape(term) + r'\b', sql, re.IGNORECASE):
                unread.append(ref)

        if not unread:
            return None

        items = "\n".join(f"  - {ref}" for ref in unread)
        return (
            "⚠️ 以下实体存在语义歧义，必须先用 meta 读取确认具体语义：\n"
            + items
            + "\n请读取以上实体后重新生成SQL。"
        )

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

    @staticmethod
    def _extract_tables(sql: str) -> Set[str]:
        tables = set()
        for m in SQLDisambigCheck._TABLE_PATTERN.finditer(sql):
            tables.add(m.group(1) or m.group(2))
        return tables

    # ──────────────── 消歧缓存 ────────────────

    def _build_disambig_cache(self, store) -> List[Tuple[str, str, Set[str]]]:
        if self._disambig_cache is not None:
            return self._disambig_cache

        cache = []
        if store is None:
            self._disambig_cache = cache
            return cache

        store._ensure_index()
        for eid, ref in store._id_index.items():
            if not ref.endswith(".disambig"):
                continue

            entity_part = ref.split("::", 1)[1] if "::" in ref else ref
            term = entity_part.rsplit(".", 1)[0]

            disambig_tables = set()
            for adj_id in store._adjacent.get(eid, set()):
                adj_ref = store._id_index.get(adj_id, "")
                if not adj_ref or "::" not in adj_ref:
                    continue
                adj_entity = adj_ref.split("::", 1)[1]
                if adj_entity.endswith(".table"):
                    table_name = adj_entity.rsplit(".", 1)[0]
                    disambig_tables.add(table_name.lower())

            cache.append((ref, term, disambig_tables))

        self._disambig_cache = cache
        return cache
