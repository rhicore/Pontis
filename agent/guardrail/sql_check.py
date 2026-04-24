"""SQL 实体审查 — 检查 SQL 涉及的表是否已读 meta。

触发场景：
  1. 模型调用 query 工具时（从 args["sql"] 提取）
  2. 模型以文本回复包含 SQL 代码块时（从 messages 提取）
"""
import re
from typing import Optional, Set

from agent.guardrail import Guardrail, AgentState


class SQLEntityCheck(Guardrail):
    """SQL 输出实体审查。"""

    _SQL_PATTERN = re.compile(r'```sql\s*(.*?)\s*```', re.DOTALL)
    _TABLE_PATTERN = re.compile(r'\bFROM\s+(\w+)|\bJOIN\s+(\w+)', re.IGNORECASE)

    def check(self, state: AgentState, pending_calls: list) -> Optional[str]:
        sql = self._get_sql(state, pending_calls)
        if not sql:
            return None

        tables = self._extract_tables(sql)
        if not tables:
            return None

        read_tables = self._find_read_tables(state.messages, tables)
        unread = tables - read_tables
        if unread:
            return (
                f"⚠️ 你的 SQL 涉及表 {unread}，但你尚未读取这些表的 meta detail。"
                "请先用 meta 工具确认每个表的语义后再输出 SQL。"
            )
        return None

    # ──────────────── SQL 提取 ────────────────

    @staticmethod
    def _get_sql(state: AgentState, pending_calls: list) -> Optional[str]:
        """从 query 工具参数或文本回复中提取 SQL。"""
        # 优先从 query 工具参数提取
        for name, args in pending_calls:
            if name == "query":
                sql = args.get("sql", "")
                if sql:
                    return sql
        # 其次从最新 assistant 消息的文本提取
        last_msg = state.messages[-1] if state.messages else {}
        if last_msg.get("role") == "assistant" and last_msg.get("content"):
            return SQLEntityCheck._extract_sql(last_msg["content"])
        return None

    @staticmethod
    def _extract_sql(content: str) -> Optional[str]:
        m = SQLEntityCheck._SQL_PATTERN.search(content)
        return m.group(1).strip() if m else None

    @staticmethod
    def _extract_tables(sql: str) -> Set[str]:
        tables = set()
        for m in SQLEntityCheck._TABLE_PATTERN.finditer(sql):
            tables.add(m.group(1) or m.group(2))
        return tables

    @staticmethod
    def _find_read_tables(messages: list, target_tables: Set[str]) -> Set[str]:
        """扫描对话历史，检查 meta 工具是否读取过目标表。"""
        found = set()
        for msg in messages:
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            for t in target_tables:
                if f"{t}.table" in content:
                    found.add(t)
        return found
