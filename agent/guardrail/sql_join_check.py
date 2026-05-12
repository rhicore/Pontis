"""SQL JOIN 路径合理性检查 — query 工具 warn 提醒，最终 SQL 文本输出 block。"""
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from agent.guardrail_api import Guardrail, GuardrailContext, CallVerdict
from agent.guardrail.sql_utils import has_read, extract_join_col_pairs, get_sql_from_messages


_ENTITY_PATTERN = re.compile(
    r'(\w+)\.(\w+)->(\w+)\.(\w+)'
)


class BridgeTableCheck(Guardrail):
    """JOIN 路径合理性检测。

    query 工具调用 → warn（柔和提醒，允许执行）
    文本 SQL 输出 → block（必须确认 JOIN 路径才能输出）
    """

    def __init__(self):
        self._edge_map: Optional[Dict[Tuple[str, str], List[Tuple[str, str]]]] = None
        self._warned: set = set()       # 已提醒过的关系实体 ref
        self._warned_pairs: set = set() # 已提醒过的列对 (t1,c1,t2,c2)

    def check(self, ctx: GuardrailContext) -> dict:
        result = {}

        for i, (name, args) in enumerate(ctx.pending_calls):
            if name != "query":
                continue
            sql = args.get("sql", "")
            if not sql:
                continue
            msg = self._check_sql(ctx, sql, strict=False)
            if msg:
                result[i] = CallVerdict("warn", msg)

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
                col_pair = _parse_col_pair(e)
                if not col_pair:
                    continue
                et1, ec1, et2, ec2 = col_pair
                pair_match = (
                    (et1.lower() == t1 and ec1.lower() == c1.lower() and
                     et2.lower() == t2 and ec2.lower() == c2.lower()) or
                    (et1.lower() == t2 and ec1.lower() == c2.lower() and
                     et2.lower() == t1 and ec2.lower() == c1.lower())
                )
                if not pair_match:
                    continue
                full_ref = _qualify_ref(project, e)
                if has_read(history, e) or has_read(history, full_ref):
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
                types = {rtype for _, rtype in matched}
                has_fk_rel = bool(types & {"fk", "rel"})
                if has_fk_rel:
                    hint = "存在 fk/rel 关系，建议读取确认"
                else:
                    hint = "仅有 overlap（置信度较低）"
                lines.append(f"  - {t1}.{c1} ↔ {t2}.{c2}（{hint}）：")
                for full_ref, _ in matched:
                    lines.append(f"    - {full_ref}")
                    suggested_refs.append(full_ref)
            else:
                lines.append(
                    f"  - {t1}.{c1} ↔ {t2}.{c2}（"
                    "未找到任何 fk/rel/overlap 关系，请确认 JOIN 条件是否正确或需要桥接表）"
                )

        if not lines:
            return ""

        body = "\n".join(lines)
        hint = _format_meta_examples(suggested_refs)
        if strict:
            return ("🚫 最终 SQL 输出被拦截：以下 JOIN 关系尚未确认，"
                    "必须先读取相关实体确认后才能输出最终 SQL：\n"
                    + body
                    + hint
                    + "\n\n请读取这些关系实体后，再重新思考并输出最终 SQL。")
        return ("⚠️ 以下 JOIN 关系尚未确认，如果你对关联语义有信心可以继续执行，"
                "但建议先读取确认：\n"
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

        rows = workspace.cypher("MATCH (n) RETURN n")
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

            m = _ENTITY_PATTERN.match(ename)
            if not m:
                continue

            src_table = m.group(1)
            dst_table = m.group(3)

            key = tuple(sorted([src_table.lower(), dst_table.lower()]))
            edge_map[key].append((ename, rel_type))

        self._edge_map = dict(edge_map)
        return self._edge_map


def _parse_col_pair(entity: str) -> Optional[tuple]:
    """从关系实体名提取列对 (table1, col1, table2, col2)。

    格式: table1.col1->table2.col2
    """
    if "->" not in entity:
        return None

    left, right = entity.split("->", 1)

    parts_l = left.rsplit(".", 1)
    if len(parts_l) != 2:
        return None
    parts_r = right.rsplit(".", 1)
    if len(parts_r) != 2:
        return None
    return (parts_l[0], parts_l[1], parts_r[0], parts_r[1])


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
