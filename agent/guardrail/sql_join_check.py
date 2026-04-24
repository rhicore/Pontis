"""SQL JOIN 路径合理性检查 — 检查相邻 JOIN 表对的 fk/rel/overlap 关系。

触发场景：
  1. 模型调用 query 工具时（从 args["sql"] 提取）
  2. 模型以文本回复包含 SQL 代码块时（从 messages 提取）

三种实体置信度：
  - fk/rel（强置信）：未读 meta 时提示参考
  - overlap（弱置信）：提示要求模型确认有充分理由
  - 无任何实体：第一次拦截，第二次放行（两击机制）
"""
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from agent.guardrail import Guardrail, AgentState


class BridgeTableCheck(Guardrail):
    """JOIN 路径合理性检测。"""

    _SQL_PATTERN = re.compile(r'```sql\s*(.*?)\s*```', re.DOTALL)
    _FROM_PATTERN = re.compile(r'\bFROM\s+(\w+)', re.IGNORECASE)
    _JOIN_PATTERN = re.compile(r'\bJOIN\s+(\w+)', re.IGNORECASE)
    _ENTITY_PATTERN = re.compile(
        r'(\w+)\.\w+__to__(\w+)\.\w+\.(fk|rel|overlap)'
    )

    def __init__(self):
        self._edge_map: Optional[Dict[Tuple[str, str], List[Tuple[str, str]]]] = None

    # ──────────────── 入口 ────────────────

    def check(self, state: AgentState, pending_calls: list) -> Optional[str]:
        sql = self._get_sql(state, pending_calls)
        if not sql:
            return None

        pairs = self._extract_join_pairs(sql)
        if not pairs:
            return None

        edge_map = self._build_edge_map(state.store)
        if not edge_map:
            return None

        warnings = []
        for t1, t2 in pairs:
            key = tuple(sorted([t1, t2]))
            entities = edge_map.get(key, [])

            fk_rels = [(e, t) for e, t in entities if t in ("fk", "rel")]
            overlaps = [(e, t) for e, t in entities if t == "overlap"]

            if fk_rels:
                if not self._has_read_entities(state.messages, [e for e, _ in fk_rels]):
                    names = [e for e, _ in fk_rels[:3]]
                    warnings.append(
                        f"  {t1} ↔ {t2}：存在 fk/rel 关系（{names}），"
                        "建议先读取 meta 确认关联语义"
                    )
            elif overlaps:
                warnings.append(
                    f"  {t1} ↔ {t2}：仅有 overlap 关系（置信度较低），"
                    "请确保有充分理由使用此 JOIN 路径"
                )
            else:
                if self._already_warned(state.messages, t1, t2):
                    continue
                warnings.append(
                    f"  {t1} ↔ {t2}：未找到任何 fk/rel/overlap 实体。"
                    "请确认 JOIN 条件是否正确，或使用 find_path 查找桥接表。"
                    "如果你确认这个 JOIN 是合理的，请直接输出相同 SQL"
                )

        if not warnings:
            return None

        return "⚠️ SQL JOIN 路径检查：\n" + "\n".join(warnings)

    # ──────────────── SQL 提取 ────────────────

    @staticmethod
    def _get_sql(state: AgentState, pending_calls: list) -> Optional[str]:
        """从 query 工具参数或文本回复中提取 SQL。"""
        for name, args in pending_calls:
            if name == "query":
                sql = args.get("sql", "")
                if sql:
                    return sql
        last_msg = state.messages[-1] if state.messages else {}
        if last_msg.get("role") == "assistant" and last_msg.get("content"):
            m = BridgeTableCheck._SQL_PATTERN.search(last_msg["content"])
            return m.group(1).strip() if m else None
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
        """提取 SQL JOIN 链中相邻的表对。"""
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
    def _has_read_entities(messages: list, entity_names: List[str]) -> bool:
        for msg in messages:
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            for name in entity_names:
                if name in content:
                    return True
        return False

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
