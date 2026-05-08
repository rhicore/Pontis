"""SQL 语义消歧检查 — 展示过消歧实体即可，不强制 meta 读取。"""
import re
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from agent.guardrail_api import Guardrail, GuardrailContext, CallVerdict
from agent.guardrail.sql_utils import (
    has_read, extract_table_names, get_sql_from_messages,
    format_entity_list, resolve_entity_ref,
)


class SQLDisambigCheck(Guardrail):
    """SQL 语义消歧审查：展示相关消歧实体供模型参考。

    query 工具调用 → warn（展示消歧实体，允许执行）
    文本 SQL 输出 → block 展示未展示过的消歧实体（展示过就不再 block）
    不强制 meta 读取，展示过一次即可。
    """

    def __init__(self):
        self._disambig_cache: Optional[List[Tuple[str, str, Set[str]]]] = None
        self._shown: set = set()  # 已展示过的消歧实体 ref

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
        tables = extract_table_names(sql)
        cache = self._build_disambig_cache(ctx.workspace)
        if not cache:
            return ""

        tables_lower = {t.lower() for t in tables}
        history = ctx.tool_history

        # 找出所有未读且未展示过的消歧实体
        table_disambigs: Dict[str, List[str]] = defaultdict(list)

        for ref, term, disambig_tables in cache:
            if has_read(history, ref):
                continue
            if ref in self._shown:
                continue

            matched_tables = disambig_tables & tables_lower
            if matched_tables:
                for t in matched_tables:
                    table_disambigs[ref].append(t)
                self._shown.add(ref)
            elif re.search(r'\b' + re.escape(term) + r'\b', sql, re.IGNORECASE):
                table_disambigs[ref] = list(tables_lower)
                self._shown.add(ref)

        if not table_disambigs:
            return ""

        # 双层列表：第一层表实体 ref，第二层消歧实体 ref
        table_to_refs: Dict[str, List[str]] = defaultdict(list)
        for ref, matched_tables in table_disambigs.items():
            for t in matched_tables:
                table_to_refs[t].append(ref)

        lines = []
        for table in sorted(table_to_refs.keys()):
            table_ref = resolve_entity_ref(ctx.workspace, table)
            lines.append(f"  - {table_ref or table}")
            refs = table_to_refs[table]
            items = format_entity_list(ctx.workspace, refs)
            indented = "\n".join("    " + line for line in items.split("\n"))
            lines.append(indented)

        return ("⚠️ SQL 中提及的以下实体涉及到语义歧义，"
                "请读取对应的语义歧义实体，了解详情：\n"
                + "\n".join(lines)
                + "\n\n meta读取对应的消歧实体之后，请仔细辨析有歧义的实体之间的关系，仔细考虑当前SQL究竟应该使用哪个实体，很多时候另一条逻辑也能走通，所以需要你仔细辨析，选择最可能的选项。")

    # ──────────────── 消歧缓存（workspace 缓存）────────────────

    def _build_disambig_cache(self, workspace) -> List[Tuple[str, str, Set[str]]]:
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

            disambig_tables = set()
            neighbor_rows = workspace.cypher(
                "MATCH (n {name: $name})--(m) RETURN m",
                params={"name": ename}
            )
            for nr in neighbor_rows:
                neighbor = nr.get("m", {}).get("name", "")
                if not neighbor:
                    continue
                # Check if neighbor is a table via label lookup
                nmeta_rows = workspace.cypher(
                    "MATCH (n {name: $name}) RETURN n",
                    params={"name": neighbor}
                )
                nmeta = nmeta_rows[0].get("n") if nmeta_rows else None
                n_labels = nmeta.get("labels", []) if nmeta else []
                if "table" in n_labels:
                    disambig_tables.add(neighbor.lower())

            cache.append((ename, term, disambig_tables))

        self._disambig_cache = cache
        return cache
