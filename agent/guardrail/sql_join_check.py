"""SQL JOIN 路径合理性检查 — 检查相邻 JOIN 表对的 fk/rel/overlap 关系。"""
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from agent.guardrail_api import Guardrail, GuardrailContext, CallVerdict
from agent.guardrail.sql_utils import get_db_prefix, has_read, extract_join_pairs, get_sql_from_messages


_ENTITY_PATTERN = re.compile(
    r'(\w+)\.\w+__to__(\w+)\.\w+\.(fk|rel|overlap)'
)


class BridgeTableCheck(Guardrail):
    """JOIN 路径合理性检测。"""

    def __init__(self):
        self._edge_map: Optional[Dict[Tuple[str, str], List[Tuple[str, str]]]] = None

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
        pairs = extract_join_pairs(sql)
        if not pairs:
            return ""

        edge_map = self._build_edge_map(ctx.store)
        if not edge_map:
            return ""

        prefix = get_db_prefix(ctx)
        history = ctx.tool_history

        warnings = []
        for t1, t2 in pairs:
            key = tuple(sorted([t1, t2]))
            entities = edge_map.get(key, [])

            fk_rels = [(e, t) for e, t in entities if t in ("fk", "rel")]
            overlaps = [(e, t) for e, t in entities if t == "overlap"]

            if fk_rels:
                if not all(has_read(history, e) for e, _ in fk_rels):
                    names = "\n".join(f"    - {prefix}{e}" for e, _ in fk_rels[:3])
                    warnings.append(
                        f"  - {t1} 和 {t2} 之间存在 fk/rel 关系，必须先读取确认关联语义：\n{names}"
                    )
            elif overlaps:
                names = "\n".join(f"    - {prefix}{e}" for e, _ in overlaps[:3])
                warnings.append(
                    f"  - {t1} 和 {t2} 之间仅有 overlap 关系（置信度较低），"
                    "读取后请重新评估这个 JOIN 是否合理：\n{names}"
                )
            else:
                if self._already_warned(ctx.messages, t1, t2):
                    continue
                warnings.append(
                    f"  - {t1} 和 {t2} 之间未找到任何 fk/rel/overlap 关系，"
                    "请确认 JOIN 条件是否正确或是否需要桥接表"
                )

        if not warnings:
            return ""

        return ("⚠️ 以下 JOIN 路径需要确认，读取相关实体后请重新审视 SQL 是否正确，是否需要修改？或根本就无需执行？或者需要进一步探索获取信息？：\n"
                + "\n".join(warnings))

    # ──────────────── 邻接图构建（store 缓存）────────────────

    def _build_edge_map(self, store) -> Dict[Tuple[str, str], List[Tuple[str, str]]]:
        if self._edge_map is not None:
            return self._edge_map

        edge_map: Dict[Tuple[str, str], List[Tuple[str, str]]] = defaultdict(list)

        if store is None:
            self._edge_map = dict(edge_map)
            return self._edge_map

        store._ensure_index()
        for eid, ref in store._id_index.items():
            if "::" not in ref:
                continue
            entity = ref.split("::", 1)[1]

            if ".fk" not in entity and ".rel" not in entity and ".overlap" not in entity:
                continue

            m = _ENTITY_PATTERN.match(entity)
            if not m:
                continue

            src_table = m.group(1)
            dst_table = m.group(2)
            rel_type = m.group(3)

            key = tuple(sorted([src_table.lower(), dst_table.lower()]))
            edge_map[key].append((entity, rel_type))

        self._edge_map = dict(edge_map)
        return self._edge_map

    @staticmethod
    def _already_warned(messages: list, t1: str, t2: str) -> bool:
        pair_str = f"{t1} ↔ {t2}" if t1 < t2 else f"{t2} ↔ {t1}"
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            if pair_str in content:
                return True
        return False
