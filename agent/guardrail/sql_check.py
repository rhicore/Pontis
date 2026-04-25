"""SQL 实体审查 — SQL 中涉及的每个表和列都必须通过 meta 读取，缺一不可。"""
from agent.guardrail_api import Guardrail, GuardrailContext, CallVerdict
from agent.guardrail.sql_utils import (
    get_db_prefix, has_read, extract_tables, extract_col_refs,
    get_sql_from_messages,
)


class SQLEntityCheck(Guardrail):
    """SQL 全量实体审查：表 + 列必须全部 meta 过。"""

    def check(self, ctx: GuardrailContext) -> dict:
        result = {}
        for i, (name, args) in enumerate(ctx.pending_calls):
            if name != "query":
                continue
            sql = args.get("sql", "")
            if not sql:
                continue
            missing = self._check_missing(ctx.tool_history, sql, get_db_prefix(ctx))
            if missing:
                result[i] = CallVerdict("block", missing)

        if not ctx.pending_calls:
            sql = get_sql_from_messages(ctx.messages)
            if sql:
                missing = self._check_missing(ctx.tool_history, sql, get_db_prefix(ctx))
                if missing:
                    result["text"] = CallVerdict("block", missing)

        return result

    @staticmethod
    def _check_missing(tool_history, sql, prefix) -> str:
        tables, aliases = extract_tables(sql)
        if not tables:
            return ""

        missing = []
        for t in sorted(tables):
            if not has_read(tool_history, f"{t}.table"):
                missing.append(f"{prefix}{t}.table" if prefix else f"{t}.table")
        for table, col in extract_col_refs(sql, aliases):
            key = f"{table}.{col}"
            if not has_read(tool_history, key):
                missing.append(f"{prefix}{key}" if prefix else key)

        if not missing:
            return ""

        items = "\n".join(f"  - {m}" for m in missing[:12])
        return ("⚠️ SQL 引用了以下尚未查看的实体，必须先用 meta 读取后再决定下一步：\n"
                + items
                + "\n\n读取后请重新思考：\n"
                "- 这些实体的实际语义是否与 SQL 中的用法一致？\n"
                "- SQL 真的需要执行吗？是否需要修改？还是根本不需要这条查询？或者需要进一步探索获取信息？")
