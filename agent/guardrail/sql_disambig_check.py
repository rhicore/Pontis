"""SQL 语义消歧检查 — 确保 SQL 涉及的实体相关消歧信息已被读取。

触发场景：
  1. 模型调用 query 工具时（从 args["sql"] 提取）
  2. 模型以文本回复包含 SQL 代码块时（从 messages 提取）

检查逻辑：
  - 表级消歧：SQL 中的表存在关联的 .disambig 实体
  - 列级消歧：SQL 中出现的列名/术语存在关联的 .disambig 实体
  - 只对未读取且未警告过的消歧实体干预
"""
import re
from typing import Dict, List, Optional, Set, Tuple

from agent.guardrail import Guardrail, AgentState


class SQLDisambigCheck(Guardrail):
    """SQL 语义消歧审查。"""

    _SQL_PATTERN = re.compile(r'```sql\s*(.*?)\s*```', re.DOTALL)
    _TABLE_PATTERN = re.compile(r'\bFROM\s+(\w+)|\bJOIN\s+(\w+)', re.IGNORECASE)

    def __init__(self):
        # 缓存: [(disambig_ref, term, disambig_tables, col_tables), ...]
        #   disambig_tables: 连接到 .table 实体的表名 (表级消歧)
        #   col_tables: 连接到 .col 实体的表名 (列级消歧)
        self._disambig_cache: Optional[List[Tuple[str, str, Set[str], Set[str]]]] = None

    # ──────────────── 入口 ────────────────

    def check(self, state: AgentState, pending_calls: list) -> Optional[str]:
        sql = self._get_sql(state, pending_calls)
        if not sql:
            return None

        tables = self._extract_tables(sql)
        cache = self._build_disambig_cache(state.store)
        if not cache:
            return None

        tables_lower = {t.lower() for t in tables}
        sql_lower = sql.lower()

        # 找出与 SQL 相关的未读消歧实体
        unread = []
        for ref, term, disambig_tables, col_tables in cache:
            if self._has_read(state.messages, ref):
                continue
            if self._already_warned(state.messages, ref):
                continue

            # 表级消歧：消歧实体直接连接到 SQL 中的 .table 实体
            if disambig_tables & tables_lower:
                unread.append((ref, term))
            # 列级消歧：歧义术语出现在 SQL 中（单词匹配）
            elif re.search(r'\b' + re.escape(term) + r'\b', sql, re.IGNORECASE):
                unread.append((ref, term))

        if not unread:
            return None

        parts = [f"  - {term}（{ref}）" for ref, term in unread]
        return (
            "⚠️ SQL 语义消歧检查：\n"
            "以下实体存在语义歧义（同名/近名但含义不同），"
            "请先用 meta 读取 .disambig 实体确认具体语义：\n"
            + "\n".join(parts)
        )

    # ──────────────── SQL 提取 ────────────────

    @staticmethod
    def _get_sql(state: AgentState, pending_calls: list) -> Optional[str]:
        """从 query 工具参数或文本回复中提取 SQL。"""
        for name, args in pending_calls:
            if name == "query":
                sql = args.get("sql", "")
                if sql:
                    return sql
        last_msg = state.messages[-1] if state.messages else {}
        if last_msg.get("role") == "assistant" and last_msg.get("content"):
            m = SQLDisambigCheck._SQL_PATTERN.search(last_msg["content"])
            return m.group(1).strip() if m else None
        return None

    @staticmethod
    def _extract_tables(sql: str) -> Set[str]:
        tables = set()
        for m in SQLDisambigCheck._TABLE_PATTERN.finditer(sql):
            tables.add(m.group(1) or m.group(2))
        return tables

    # ──────────────── 消歧缓存 ────────────────

    def _build_disambig_cache(self, store) -> List[Tuple[str, str, Set[str], Set[str]]]:
        """从 store 构建消歧实体缓存。

        Returns: [(disambig_ref, ambiguous_term, disambig_tables, col_tables), ...]
          - disambig_tables: 连接到 .table 邻居的表名（表级消歧）
          - col_tables: 连接到 .col 邻居的表名（列级消歧，仅用于参考）
        """
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

            # 提取消歧术语: "db::points.disambig" → "points"
            entity_part = ref.split("::", 1)[1] if "::" in ref else ref
            term = entity_part.rsplit(".", 1)[0]

            # 区分表级和列级消歧
            disambig_tables = set()  # .table 邻居
            col_tables = set()       # .col 邻居
            for adj_id in store._adjacent.get(eid, set()):
                adj_ref = store._id_index.get(adj_id, "")
                if not adj_ref or "::" not in adj_ref:
                    continue
                adj_entity = adj_ref.split("::", 1)[1]

                if adj_entity.endswith(".table"):
                    table_name = adj_entity.rsplit(".", 1)[0]
                    disambig_tables.add(table_name.lower())
                elif adj_entity.endswith(".col"):
                    parts = adj_entity.split(".")
                    if len(parts) >= 4:
                        col_tables.add(parts[0].lower())

            cache.append((ref, term, disambig_tables, col_tables))

        self._disambig_cache = cache
        return cache

    # ──────────────── 辅助方法 ────────────────

    @staticmethod
    def _has_read(messages: list, disambig_ref: str) -> bool:
        """检查消歧实体是否已在对话中被读取。"""
        # 消歧实体 ref 中 :: 后的部分，用于匹配 meta 工具输出
        entity_part = disambig_ref.split("::", 1)[1] if "::" in disambig_ref else disambig_ref
        for msg in messages:
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            if entity_part in content:
                return True
        return False

    @staticmethod
    def _already_warned(messages: list, disambig_ref: str) -> bool:
        """两击机制：如果已警告过此消歧实体，放行。"""
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            if disambig_ref in content:
                return True
        return False
