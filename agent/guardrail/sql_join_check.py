"""SQL JOIN 路径合理性检查。

该 guardrail 只拦截最终 SQL 中没有图关系支撑的 JOIN。
工具使用阶段不插入提醒，JOIN 探索由系统提示和工具元数据引导。
"""
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from agent.guardrail_api import Guardrail, GuardrailContext, CallVerdict
from agent.guardrail.sql_utils import has_read, extract_join_col_pairs, get_sql_from_messages


class BridgeTableCheck(Guardrail):
    """JOIN 路径合理性检测。

    query 工具调用 → 不拦截、不提醒。
    文本 SQL 输出 → 仅 block 没有 fk/rel/overlap 实体支撑的 JOIN。
    """

    def __init__(self):
        self._edge_map: Optional[Dict[Tuple[str, str], List[Tuple[str, str]]]] = None
        self._warned: set = set()       # 已提醒过的关系实体 ref
        self._warned_pairs: set = set() # 已提醒过的列对 (t1,c1,t2,c2)

    def check(self, ctx: GuardrailContext) -> dict:
        result = {}

        if not ctx.pending_calls:
            sql = get_sql_from_messages(ctx.messages)
            if sql:
                msg = self._check_sql(ctx, sql, strict=True)
                if msg:
                    result["text"] = CallVerdict("block", msg)

        return result

    def _check_sql(self, ctx, sql, strict: bool = False) -> str:
        col_pairs = extract_join_col_pairs(sql)
        if not col_pairs:
            return ""

        edge_map = self._build_edge_map(ctx.workspace)
        if not edge_map:
            return ""

        project = _get_query_project(ctx)
        history = ctx.tool_history

        # 按提取的列对分组，查找对应关系实体
        lines = []
        suggested_refs = []
        for t1, c1, t2, c2 in col_pairs:
            pair_key = (t1, c1, t2, c2)
            if not strict and pair_key in self._warned_pairs:
                continue

            key = tuple(sorted([t1, t2]))
            entities = edge_map.get(key, [])

            # 筛选与列对匹配的关系实体
            matched = []
            already_confirmed = False
            for e, rtype in entities:
                # 匹配列名（大小写不敏感）
                pair_match = any(
                    (
                        et1.lower() == t1 and ec1.lower() == c1.lower() and
                        et2.lower() == t2 and ec2.lower() == c2.lower()
                    ) or (
                        et1.lower() == t2 and ec1.lower() == c2.lower() and
                        et2.lower() == t1 and ec2.lower() == c1.lower()
                    )
                    for et1, ec1, et2, ec2 in _parse_col_pairs(e)
                )
                if not pair_match:
                    continue
                labeled_ref = f"{e}:{rtype}"
                full_ref = _qualify_ref(project, labeled_ref)
                if has_read(history, e) or has_read(history, labeled_ref) or has_read(history, full_ref):
                    already_confirmed = True
                    continue
                if not strict and full_ref in self._warned:
                    continue
                if not strict:
                    self._warned.add(full_ref)
                matched.append((full_ref, rtype))

            if not strict:
                self._warned_pairs.add(pair_key)

            if already_confirmed:
                continue

            if matched:
                # 有显式关系实体时，JOIN 路径在图谱层面已经可解释。
                # 不要求 agent 必须先 meta 读取，否则会把正确 SQL 打回去重写。
                continue

            lines.append(
                f"  - {t1}.{c1} ↔ {t2}.{c2}（"
                "图谱中没有对应 fk/rel/overlap 实体）"
            )

        if not lines:
            return ""

        body = "\n".join(lines)
        hint = _format_meta_examples(suggested_refs)
        if strict:
            return ("以下 JOIN 关系缺少图谱关系支撑：\n"
                    + body
                    + hint
                    + "\n\n请先确认表之间是否存在正确连接路径，再输出最终 SQL。")
        return ("⚠️ 以下 JOIN 关系建议读取确认：\n"
                + body
                + hint)

    # ──────────────── 邻接图构建（workspace 缓存）────────────────

    def _build_edge_map(self, workspace) -> Dict[Tuple[str, str], List[Tuple[str, str]]]:
        if self._edge_map is not None:
            return self._edge_map

        edge_map: Dict[Tuple[str, str], List[Tuple[str, str]]] = defaultdict(list)

        if workspace is None:
            self._edge_map = dict(edge_map)
            return self._edge_map

        rows = workspace.cypher(
            "MATCH (n)--(c:col)--(t) "
            "WHERE any(label IN coalesce(n.labels, []) WHERE label IN ['fk', 'rel', 'overlap']) "
            "AND any(label IN coalesce(t.labels, []) WHERE label IN ['table', 'view']) "
            "RETURN n, collect(DISTINCT {table_name: t.name, column_name: c.name}) AS endpoints"
        )
        for row in rows:
            n = row.get("n", {})
            ename = n.get("name", "")
            labels = n.get("labels", [])
            # 检查 labels 是否包含 fk/rel/overlap
            rel_type = None
            if "fk" in labels:
                rel_type = "fk"
            elif "rel" in labels:
                rel_type = "rel"
            elif "overlap" in labels:
                rel_type = "overlap"
            if rel_type is None:
                continue

            endpoints = row.get("endpoints", []) or []
            table_columns: Dict[str, set] = defaultdict(set)
            for endpoint in endpoints:
                table_name = str(endpoint.get("table_name") or "")
                column_name = str(endpoint.get("column_name") or "")
                if table_name and column_name:
                    table_columns[table_name].add(column_name)
            table_names = sorted(table_columns)
            for index, src_table in enumerate(table_names):
                for dst_table in table_names[index + 1:]:
                    key = tuple(sorted([src_table.lower(), dst_table.lower()]))
                    edge_map[key].append((ename, rel_type))
            for table_name, columns in table_columns.items():
                if len(columns) >= 2:
                    key = (table_name.lower(), table_name.lower())
                    edge_map[key].append((ename, rel_type))

        self._edge_map = dict(edge_map)
        return self._edge_map


def _get_query_project(ctx) -> str:
    workspace = ctx.workspace
    if workspace is None:
        return ""
    active = list(getattr(workspace, "active_projects", []) or [])
    for project in active:
        if project != "bird":
            return project
    return active[0] if active else ""


def _qualify_ref(project: str, entity: str) -> str:
    if not project or "::" in entity:
        return entity
    return f"{project}::{entity}"


def _format_meta_examples(refs: List[str]) -> str:
    unique = []
    seen = set()
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        unique.append(ref)

    if not unique:
        return ""

    sample = unique[:3]
    lines = ["", "", "可直接复制使用这些 meta 调用："]
    for ref in sample:
        lines.append(f'  - meta({{"ref": "{ref}"}})')
    if len(unique) > len(sample):
        lines.append("  - 其余同理，直接对上面列出的 ref 调用 meta")
    return "\n".join(lines)
