"""SQL 实体审查 — query 工具 warn 提醒，最终 SQL 文本输出 block。"""
from agent.guardrail_api import Guardrail, GuardrailContext, CallVerdict
from agent.guardrail.sql_utils import (
    has_read, extract_tables, extract_col_refs,
    resolve_entity_ref, get_sql_from_messages, format_entity_list,
)


class SQLEntityCheck(Guardrail):
    """SQL 全量实体审查：表 + 列必须全部 meta 过。

    query 工具调用 → warn（柔和提醒，允许执行）
    文本 SQL 输出 → block（必须读完 meta 才能输出）
    """

    def __init__(self):
        self._warned: set = set()

    def check(self, ctx: GuardrailContext) -> dict:
        result = {}

        for i, (name, args) in enumerate(ctx.pending_calls):
            if name != "query":
                continue
            sql = args.get("sql", "")
            if not sql:
                continue
            missing = self._check_missing(
                ctx.tool_history, sql, ctx.workspace, suppress_repeats=True
            )
            if missing:
                items = format_entity_list(ctx.workspace, missing[:12])
                msg = ("⚠️ SQL 引用了以下尚未通过 meta 读取的实体，"
                       "如果你对这些实体的语义有信心可以继续执行，"
                       "但建议先 meta 读取确认：\n"
                       + items)
                result[i] = CallVerdict("warn", msg)

        if not ctx.pending_calls:
            sql = get_sql_from_messages(ctx.messages)
            if sql:
                missing = self._check_missing(
                    ctx.tool_history, sql, ctx.workspace, suppress_repeats=False
                )
                if missing:
                    items = format_entity_list(ctx.workspace, missing[:12])
                    msg = ("🚫 最终 SQL 输出被拦截：以下实体尚未通过 meta 读取，"
                           "必须先读取确认语义后才能输出最终 SQL：\n"
                           + items
                           + "\n\n请使用 meta 工具读取以上实体后重新思考确保理解数据库结构和SQL的正确性再输出。")
                    result["text"] = CallVerdict("block", msg)

        return result

    def _check_missing(self, tool_history, sql, workspace=None,
                       suppress_repeats: bool = True) -> list:
        tables, aliases = extract_tables(sql)
        if not tables:
            return []

        missing = []
        for t in sorted(tables):
            if not has_read(tool_history, t):
                ref = resolve_entity_ref(workspace, t)
                if not ref:
                    continue
                if suppress_repeats and ref in self._warned:
                    continue
                missing.append(ref)
                if suppress_repeats:
                    self._warned.add(ref)
        for table, col in extract_col_refs(sql, aliases):
            if not has_read(tool_history, col):
                ref = resolve_entity_ref(workspace, table, col)
                if not ref:
                    continue
                if suppress_repeats and ref in self._warned:
                    continue
                missing.append(ref)
                if suppress_repeats:
                    self._warned.add(ref)

        return missing
