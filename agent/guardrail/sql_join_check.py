"""SQL JOIN 路径合理性检查 — 检查相邻 JOIN 表对的 fk/rel/overlap 关系。"""
import re
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from agent.guardrail import Guardrail, AgentState, _get_db_prefix


class BridgeTableCheck(Guardrail):
    """JOIN 路径合理性检测。"""

    _SQL_PATTERN = re.compile(r'```sql\s*(.*?)\s*```', re.DOTALL)
    _FROM_PATTERN = re.compile(r'\bFROM\s+(\w+)', re.IGNORECASE)
    _JOIN_PATTERN = re.compile(r'\bJOIN\s+(\w+)', re.IGNORECASE)
    _ENTITY_PATTERN = re.compile(
        r'(\w+)\.\w+__to__(\w+)\.\w+\.(fk|rel|overlap)'
    )

    def __init__(self):
        self._meta_read: Set[str] = set()
        self._sync_idx: int = 0
        self._edge_map: Optional[Dict[Tuple[str, str], List[Tuple[str, str]]]] = None

    def _sync(self, state: AgentState):
        for name, args, _ in state.tool_history[self._sync_idx:]:
            if name == "meta":
                path = args.get("path", "")
                entity_part = path.split("::", 1)[1] if "::" in path else path
                self._meta_read.add(entity_part)
        self._sync_idx = len(state.tool_history)

    def _has_read(self, entity_path: str) -> bool:
        if "::" in entity_path:
            entity_path = entity_path.split("::", 1)[1]
        if entity_path in self._meta_read:
            return True
        return any(s.startswith(entity_path + ".") for s in self._meta_read)

    # ──────────────── 入口 ────────────────

    def check(self, state: AgentState, pending_calls: list) -> Optional[str]:
        self._sync(state)

        sql = self._get_sql(state, pending_calls)
        if not sql:
            return None

        pairs = self._extract_join_pairs(sql)
        if not pairs:
            return None

        edge_map = self._build_edge_map(state.store)
        if not edge_map:
            return None

        db_prefix = _get_db_prefix(state, pending_calls)

        warnings = []
        for t1, t2 in pairs:
            key = tuple(sorted([t1, t2]))
            entities = edge_map.get(key, [])

            fk_rels = [(e, t) for e, t in entities if t in ("fk", "rel")]
            overlaps = [(e, t) for e, t in entities if t == "overlap"]

            if fk_rels:
                if not all(self._has_read(e) for e, _ in fk_rels):
                    names = "\n".join(f"    - {db_prefix}{e}" for e, _ in fk_rels[:3])
                    warnings.append(
                        f"  - {t1} 和 {t2} 之间存在 fk/rel 关系：\n{names}\n"
                        "    请先读取以上实体确认关联语义"
                    )
            elif overlaps:
                names = "\n".join(f"    - {db_prefix}{e}" for e, _ in overlaps[:3])
                warnings.append(
                    f"  - {t1} 和 {t2} 之间仅有 overlap 关系（置信度较低）：\n{names}\n"
                    "    请确保有充分理由使用此 JOIN 路径"
                )
            else:
                if self._already_warned(state.messages, t1, t2):
                    continue
                warnings.append(
                    f"  - {t1} 和 {t2} 之间未找到任何 fk/rel/overlap 实体\n"
                    "    请确认 JOIN 条件是否正确，或使用 find_path 查找桥接表"
                )

        if not warnings:
            return None

        return ("⚠️ 以下 JOIN 路径需要确认，请读取相关实体后重新生成SQL：\n"
                + "\n".join(warnings))

    # ──────────────── SQL 提取 ────────────────

    @staticmethod
    def _get_sql(state: AgentState, pending_calls: list) -> Optional[str]:
        for name, args in pending_calls:
            if name == "query":
                sql = args.get("sql", "")
                if sql:
                    return sql
        if pending_calls:
            return None
        for msg in reversed(state.messages):
            if msg.get("role") != "assistant":
                break
            content = msg.get("content", "")
            if not content:
                continue
            m = re.search(r'```sql\s*(.*?)\s*```', content, re.DOTALL)
            if m:
                return m.group(1)
        return None

    # ──────────────── 邻接图构建 ────────────────

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

            m = self._ENTITY_PATTERN.match(entity)
            if not m:
                continue

            src_table = m.group(1)
            dst_table = m.group(2)
            rel_type = m.group(3)

            key = tuple(sorted([src_table.lower(), dst_table.lower()]))
            edge_map[key].append((entity, rel_type))

        self._edge_map = dict(edge_map)
        return self._edge_map

    # ──────────────── 辅助方法 ────────────────

    @staticmethod
    def _extract_join_pairs(sql: str) -> List[Tuple[str, str]]:
        tables_in_order = []
        m = BridgeTableCheck._FROM_PATTERN.search(sql)
        if m:
            tables_in_order.append(m.group(1).lower())
        for m in BridgeTableCheck._JOIN_PATTERN.finditer(sql):
            tables_in_order.append(m.group(1).lower())

        if len(tables_in_order) < 2:
            return []

        pairs = []
        for i in range(len(tables_in_order) - 1):
            t1, t2 = tables_in_order[i], tables_in_order[i + 1]
            if t1 != t2:
                pairs.append((t1, t2))
        return pairs

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
