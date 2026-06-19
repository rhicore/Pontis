"""SQL 语义消歧检查 — 展示相关消歧实体供模型参考。"""
import re
from dataclasses import dataclass
from typing import List, Optional, Set

from agent.guardrail_api import Guardrail, GuardrailContext, CallVerdict
from agent.guardrail.sql_utils import (
    has_meta_read, extract_tables, extract_col_refs, get_sql_from_messages,
)


@dataclass(frozen=True)
class _DisambigEntry:
    ref: str
    term: str
    tables: Set[str]
    columns: Set[str]
    table_columns: Set[tuple[str, str]]


class SQLDisambigCheck(Guardrail):
    """SQL 语义消歧审查：展示相关消歧实体供模型参考。

    query 工具调用 → warn（展示消歧实体，允许执行）
    文本 SQL 输出 → block，直到相关消歧实体被 meta 实际读取
    find 展示或历史 warning 不算读取。
    """

    def __init__(self):
        self._disambig_cache: Optional[List[_DisambigEntry]] = None

    def check(self, ctx: GuardrailContext) -> dict:
        result = {}

        for i, (name, args) in enumerate(ctx.pending_calls):
            if name != "query":
                continue
            sql = args.get("sql", "")
            if not sql:
                continue
            msg = self._check_sql(ctx, sql)
            if msg:
                result[i] = CallVerdict("warn", msg)

        if not ctx.pending_calls:
            sql = get_sql_from_messages(ctx.messages)
            if sql:
                msg = self._check_sql(ctx, sql)
                if msg:
                    result["text"] = CallVerdict("block", msg)

        return result

    def _check_sql(self, ctx, sql) -> str:
        tables, aliases = extract_tables(sql)
        col_refs = extract_col_refs(sql, aliases)
        cache = self._build_disambig_cache(ctx.workspace)
        if not cache:
            return ""

        tables_lower = {t.lower() for t in tables}
        columns_lower = {c.lower() for _, c in col_refs}
        table_columns_lower = {(t.lower(), c.lower()) for t, c in col_refs}
        history = ctx.tool_history

        # 找出所有未通过 meta 实际读取的消歧实体，平铺展示。
        disambigs: Set[str] = set()

        for entry in cache:
            if has_meta_read(history, entry.ref):
                continue

            if self._matches_entry(entry, sql, tables_lower, columns_lower, table_columns_lower):
                disambigs.add(entry.ref)

        if not disambigs:
            return ""

        return ("⚠️ SQL 中涉及以下语义消歧实体；最终 SQL 前必须用 meta 读取它们，再确认表、列、值、JOIN 和聚合选择：\n"
                + _format_disambig_list(ctx.workspace, sorted(disambigs))
                + "\n\n只看到 find 结果或 guardrail 提示不算读取；请调用 meta 读取相关 disambig。")

    def _matches_entry(
        self,
        entry: _DisambigEntry,
        sql: str,
        tables_lower: Set[str],
        columns_lower: Set[str],
        table_columns_lower: Set[tuple[str, str]],
    ) -> bool:
        if entry.table_columns & table_columns_lower:
            return True

        # If the SQL references one side of a column-level ambiguity, surface the
        # full disambiguation even when the disambig is not connected to tables.
        if entry.columns & columns_lower:
            return True

        term = _normalize_disambig_term(entry.term)
        if term and re.search(r'\b' + re.escape(term) + r'\b', sql, re.IGNORECASE):
            return True
        return False

    # ──────────────── 消歧缓存（workspace 缓存）────────────────

    def _build_disambig_cache(self, workspace) -> List[_DisambigEntry]:
        if self._disambig_cache is not None:
            return self._disambig_cache

        cache = []
        if workspace is None:
            self._disambig_cache = cache
            return cache

        rows = workspace.cypher("MATCH (n) RETURN n")
        for row in rows:
            n = row.get("n", {})
            ename = n.get("name", "")
            labels = n.get("labels", [])
            if "disambig" not in labels:
                continue

            # term = entity name without .disambig suffix (if present)
            term = ename.replace(".disambig", "")

            disambig_tables: Set[str] = set()
            disambig_columns: Set[str] = set()
            disambig_table_columns: Set[tuple[str, str]] = set()
            neighbor_rows = workspace.cypher(
                "MATCH (n {name: $name})--(m) "
                "OPTIONAL MATCH (m)--(parent) "
                "RETURN m, collect(parent) AS parents",
                params={"name": ename}
            )
            for nr in neighbor_rows:
                m = nr.get("m", {}) or {}
                neighbor = m.get("name", "")
                if not neighbor:
                    continue
                n_labels = m.get("labels", []) or []
                if "table" in n_labels or "view" in n_labels:
                    disambig_tables.add(neighbor.lower())
                elif "col" in n_labels:
                    disambig_columns.add(neighbor.lower())
                    for parent in nr.get("parents") or []:
                        parent_labels = parent.get("labels", []) or []
                        parent_name = parent.get("name", "")
                        if parent_name and ("table" in parent_labels or "view" in parent_labels):
                            disambig_table_columns.add((parent_name.lower(), neighbor.lower()))

            cache.append(_DisambigEntry(
                ref=ename,
                term=term,
                tables=disambig_tables,
                columns=disambig_columns,
                table_columns=disambig_table_columns,
            ))

        self._disambig_cache = cache
        return cache


def _normalize_disambig_term(term: str) -> str:
    term = term.replace(".disambig", "")
    term = term.replace("_dual_source", "")
    term = term.replace("_disambiguation", "")
    term = term.replace("_choice", "")
    return term.replace("_", " ").strip()


def _format_disambig_list(workspace, refs: list[str]) -> str:
    lines = []
    for ref in refs:
        brief = ""
        labels = []
        if workspace is not None:
            rows = workspace.cypher(
                "MATCH (n {name: $name}) "
                "WHERE 'disambig' IN coalesce(n.labels, []) "
                "RETURN n",
                params={"name": ref},
            )
            meta = rows[0].get("n") if rows else None
            if meta:
                brief = meta.get("brief") or ""
                labels = meta.get("labels") or []
        display_ref = ref
        if "disambig" in labels and not display_ref.endswith(":disambig"):
            display_ref = f"{display_ref}:disambig"
        line = f"  {display_ref}"
        if brief:
            line += f"\t{brief}"
        lines.append(line)
    return "\n".join(lines)
