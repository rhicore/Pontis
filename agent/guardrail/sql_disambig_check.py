"""SQL 语义消歧检查 — SQL 涉及的消歧实体必须全部通过 meta 读取，缺一不可。"""
import re
from typing import List, Optional, Set, Tuple

from agent.guardrail_api import Guardrail, GuardrailContext, CallVerdict
from agent.guardrail.sql_utils import has_read, extract_table_names, get_sql_from_messages


class SQLDisambigCheck(Guardrail):
    """SQL 语义消歧审查：所有相关 disambig 实体必须读过。"""

    def __init__(self):
        self._disambig_cache: Optional[List[Tuple[str, str, Set[str]]]] = None

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
                result[i] = CallVerdict("block", msg)

        if not ctx.pending_calls:
            sql = get_sql_from_messages(ctx.messages)
            if sql:
                msg = self._check_sql(ctx, sql)
                if msg:
                    result["text"] = CallVerdict("block", msg)

        return result

    def _check_sql(self, ctx, sql) -> str:
        tables = extract_table_names(sql)
        cache = self._build_disambig_cache(ctx.store)
        if not cache:
            return ""

        tables_lower = {t.lower() for t in tables}
        history = ctx.tool_history

        unread = []
        for ref, term, disambig_tables in cache:
            if has_read(history, ref):
                continue
            if disambig_tables & tables_lower:
                unread.append(ref)
            elif re.search(r'\b' + re.escape(term) + r'\b', sql, re.IGNORECASE):
                unread.append(ref)

        if not unread:
            return ""

        items = "\n".join(f"  - {ref}" for ref in unread)
        return ("⚠️ 以下实体存在语义歧义，必须先用 meta 读取确认具体语义：\n"
                + items
                + "\n读取后请重新思考 SQL 的必需性和正确性，或者需要进一步探索获取信息。")

    # ──────────────── 消歧缓存（store 缓存）────────────────

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
