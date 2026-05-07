"""Cypher 解析器和执行器 — Storage 层的唯一查询入口。

标准 Cypher 子集（读写）：
  MATCH (n:table) RETURN n
  MATCH (n {name: "loan"}) RETURN n
  MATCH (n:file:db)--(t:table) RETURN n, t
  MATCH (a:table)-[*1..3]-(b:col) RETURN a, b
  MATCH (n) WHERE n.name ENDS WITH 'id' RETURN n
  CREATE (n:Label {name: "x", path: "data/x.db"})
  MATCH (n {name: "x"}) DELETE n
  MATCH (n {name: "x"}) SET n.brief = "description", n.detail = "..."
  MATCH (a {name: "x"}),(b {name: "y"}) CREATE (a)--(b)
"""
import os
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from storage.labels import labels_match_all


# ═══════════════════════════════════════════════════════════
#  AST
# ═══════════════════════════════════════════════════════════

@dataclass
class NodePattern:
    var: str
    labels: List[str] = field(default_factory=list)
    props: Dict[str, str] = field(default_factory=dict)

    def matches_labels(self, entity_labels: List[str]) -> bool:
        if not self.labels:
            return True
        return labels_match_all(entity_labels, self.labels)


@dataclass
class RelPattern:
    from_var: str
    to_var: str
    min_hops: int = 1
    max_hops: int = 1


@dataclass
class WhereClause:
    var: str
    prop: str
    op: str        # "=" | "!=" | ">" | "<" | ">=" | "<="
                   # "STARTS WITH" | "ENDS WITH" | "CONTAINS"
    value: str


@dataclass
class SetClause:
    var: str
    prop: str
    value: str


@dataclass
class CypherQuery:
    nodes: List[NodePattern] = field(default_factory=list)
    rels: List[RelPattern] = field(default_factory=list)
    where: List[WhereClause] = field(default_factory=list)
    return_vars: List[str] = field(default_factory=list)
    # 写操作
    action: str = "RETURN"   # RETURN | CREATE | DELETE | SET
    set_clauses: List[SetClause] = field(default_factory=list)
    create_rels: List[tuple] = field(default_factory=list)  # [(var_a, var_b), ...]


# ═══════════════════════════════════════════════════════════
#  解析器
# ═══════════════════════════════════════════════════════════

# (var:Label1:Label2 {prop: "value", ...})
_NODE_RE = re.compile(
    r'\((\w+)'                    # var
    r'((?::\w+)*)'               # :Label1:Label2
    r'(?:\s*\{([^}]*)\})?'       # {prop: "value", ...}
    r'\)'
)
_REL_VAR_RE = re.compile(r'-\[\*(\d+)\.\.(\d*)\]-')
_WHERE_RE = re.compile(
    r"""(\w+)\.(\w+)\s*"""
    r"""(STARTS\s+WITH|ENDS\s+WITH|CONTAINS|!=|>=|<=|=|>|<)\s*"""
    r"""['"]([^'"]*)['"]""",
    re.IGNORECASE
)
_RETURN_RE = re.compile(r'RETURN\s+(.+)', re.IGNORECASE)
_CREATE_RE = re.compile(r'\bCREATE\b', re.IGNORECASE)
_DELETE_RE = re.compile(r'\bDELETE\b', re.IGNORECASE)
_SET_RE = re.compile(r'\bSET\b', re.IGNORECASE)
_SET_CLAUSE_RE = re.compile(
    r"""(\w+)\.(\w+)\s*=\s*['"]([^'"]*)['"]"""
)
# MATCH (a), (b) CREATE (a)--(b) 中的边创建
_CREATE_EDGE_RE = re.compile(r'CREATE\s+(.*?)$', re.IGNORECASE)


def _parse_props(text: str) -> Dict[str, str]:
    """解析 `{name: "value", key: "val"}` 属性字典。"""
    if not text:
        return {}
    props = {}
    for pair in text.split(','):
        pair = pair.strip()
        if ':' not in pair:
            continue
        key, val = pair.split(':', 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        props[key] = val
    return props


def parse_cypher(text: str) -> CypherQuery:
    """解析标准 Cypher 子集字符串为 AST。"""
    text = text.strip().rstrip(';')

    # ── 判断操作类型 ──
    action = "RETURN"

    # 检测 MATCH ... DELETE
    if _DELETE_RE.search(text):
        action = "DELETE"
    # 检测 MATCH ... SET
    elif _SET_RE.search(text):
        action = "SET"
    # 检测纯 CREATE（无 MATCH）或 MATCH ... CREATE
    elif _CREATE_RE.search(text):
        # 区分 MATCH ... CREATE (边) 和 纯 CREATE (节点)
        if re.search(r'\bMATCH\b', text, re.IGNORECASE):
            # MATCH (a),(b) CREATE (a)--(b) → 创建边
            action = "CREATE"
        else:
            # CREATE (n:Label {props}) → 创建节点
            action = "CREATE"

    # ── 拆分各子句 ──
    where_text = ""
    match_text = text
    set_text = ""

    where_m = re.search(r'\bWHERE\b', text, re.IGNORECASE)
    if where_m:
        match_text = text[:where_m.start()]
        remainder = text[where_m.end():]

        # WHERE 后面可能跟着 RETURN / DELETE / SET / CREATE
        next_keyword = re.search(
            r'\b(RETURN|DELETE|SET|CREATE)\b', remainder, re.IGNORECASE)
        if next_keyword:
            where_text = remainder[:next_keyword.start()]
        else:
            where_text = remainder

    # 提取 SET 子句内容
    if action == "SET":
        set_m = _SET_RE.search(text)
        if set_m:
            set_text = text[set_m.end():]

    # 解析 WHERE 子句
    wheres = []
    for wm in _WHERE_RE.finditer(where_text):
        op = wm.group(3).upper().replace('  ', ' ')
        wheres.append(WhereClause(
            var=wm.group(1), prop=wm.group(2),
            op=op, value=wm.group(4),
        ))

    # 解析 SET 子句
    set_clauses = []
    for sm in _SET_CLAUSE_RE.finditer(set_text):
        set_clauses.append(SetClause(
            var=sm.group(1), prop=sm.group(2), value=sm.group(3),
        ))

    # 解析 RETURN
    return_vars = []
    rm = _RETURN_RE.search(text)
    if rm:
        return_vars = [v.strip() for v in rm.group(1).split(',')]

    # ── 解析 MATCH 部分 ──
    match_part = match_text
    has_match = bool(re.search(r'\bMATCH\b', match_text, re.IGNORECASE))

    if has_match:
        # MATCH 查询：去掉 RETURN/DELETE/SET/CREATE 及其后面的内容
        for kw in (r'RETURN', r'DELETE', r'SET', r'CREATE'):
            km = re.search(rf'\b{kw}\b', match_part, re.IGNORECASE)
            if km:
                match_part = match_part[:km.start()]
    elif action == "CREATE":
        # 纯 CREATE：去掉 CREATE 关键字，保留节点模式
        cm = _CREATE_RE.search(match_part)
        if cm:
            match_part = match_part[cm.end():]

    nodes = []
    rels = []

    # 提取所有节点模式
    for nm in _NODE_RE.finditer(match_part):
        var = nm.group(1)
        labels_str = nm.group(2)
        labels = [l for l in labels_str.split(':') if l]
        props = _parse_props(nm.group(3) or "")
        nodes.append(NodePattern(var=var, labels=labels, props=props))

    # 提取关系模式（MATCH 段内的，仅当 between 包含 -- 时）
    node_positions = [(m.start(), m.end(), m) for m in _NODE_RE.finditer(match_part)]
    for i in range(len(node_positions) - 1):
        _, end_curr, curr_m = node_positions[i]
        start_next, _, next_m = node_positions[i + 1]
        between = match_part[end_curr:start_next]

        # 逗号分隔 = 独立节点，无关系
        stripped = between.strip()
        if not stripped or stripped == ',':
            continue

        from_var = curr_m.group(1)
        to_var = next_m.group(1)

        var_m = _REL_VAR_RE.search(between)
        if var_m:
            max_s = var_m.group(2)
            rels.append(RelPattern(from_var=from_var, to_var=to_var,
                                   min_hops=int(var_m.group(1)),
                                   max_hops=int(max_s) if max_s else 0))
        else:
            rels.append(RelPattern(from_var=from_var, to_var=to_var))

    # ── 解析 CREATE 边（MATCH ... CREATE (a)--(b)）──
    create_rels = []
    if action == "CREATE" and re.search(r'\bMATCH\b', text, re.IGNORECASE):
        # 从 CREATE 后面的部分提取边
        create_m = _CREATE_RE.search(text)
        if create_m:
            create_part = text[create_m.end():]
            # 找 (var1)--(var2) 模式
            edge_nodes = _NODE_RE.findall(create_part)
            if len(edge_nodes) >= 2:
                # 简单情况：CREATE (a)--(b)
                create_rels.append((edge_nodes[0][0], edge_nodes[1][0]))

    return CypherQuery(nodes=nodes, rels=rels, where=wheres,
                       return_vars=return_vars, action=action,
                       set_clauses=set_clauses, create_rels=create_rels)


# ═══════════════════════════════════════════════════════════
#  属性匹配
# ═══════════════════════════════════════════════════════════

def _prop_matches(actual, op: str, expected: str) -> bool:
    """标准 Cypher 属性比较。actual 是 Python 值，expected 是字符串。"""
    if actual is None:
        return False
    s = str(actual)
    if op == "=":
        return s == expected
    if op == "!=":
        return s != expected
    if op == "STARTS WITH":
        return s.startswith(expected)
    if op == "ENDS WITH":
        return s.endswith(expected)
    if op == "CONTAINS":
        return expected in s
    # 数值比较
    try:
        num_actual = float(s)
        num_expected = float(expected)
    except (ValueError, TypeError):
        return False
    if op == ">":
        return num_actual > num_expected
    if op == "<":
        return num_actual < num_expected
    if op == ">=":
        return num_actual >= num_expected
    if op == "<=":
        return num_actual <= num_expected
    return False


def _inline_props_match(props: Dict[str, str], entity_props: dict) -> bool:
    """检查内联属性 {name: "loan"} 是否全部匹配。"""
    for key, expected in props.items():
        actual = entity_props.get(key)
        if not _prop_matches(actual, "=", expected):
            return False
    return True


def _where_matches(wheres: List[WhereClause], var: str,
                   entity_props: dict) -> bool:
    """检查 WHERE 条件。所有属性（包括 name/project）从 entity_props 统一读取。"""
    for w in wheres:
        if w.var != var:
            continue
        actual = entity_props.get(w.prop)
        if not _prop_matches(actual, w.op, w.value):
            return False
    return True


# ═══════════════════════════════════════════════════════════
#  辅助
# ═══════════════════════════════════════════════════════════

def _entity_props(store, eid: str, name: str, labels: List[str]) -> dict:
    """构建实体的基础属性。"""
    return {
        "id": eid if not _is_virtual(eid) else "",
        "name": name,
        "labels": labels,
        "project": getattr(store, '_project_name', ''),
    }


def _node_result(store, eid: str) -> Optional[dict]:
    """将 ent_id 转换为结果字典（含基础属性）。"""
    if isinstance(eid, str) and eid.startswith("__vdir__"):
        key = eid[len("__vdir__"):]
        name = os.path.basename(key) if key != "." else "."
        return {"id": "", "name": name, "labels": ["dir"], "project": ""}
    if isinstance(eid, str) and eid.startswith("__vfile__"):
        key = eid[len("__vfile__"):]
        return {"id": "", "name": os.path.basename(key), "labels": ["file"], "project": ""}
    name = store._id_index.get(eid)
    if not name:
        return None
    labels = store._get_labels_by_id(eid)
    return {"id": eid, "name": name, "labels": labels,
            "project": getattr(store, '_project_name', '')}


def _is_virtual(eid: str) -> bool:
    """判断是否为虚实体 ID。"""
    return isinstance(eid, str) and (
        eid.startswith("__vdir__") or eid.startswith("__vfile__")
    )


# ═══════════════════════════════════════════════════════════
#  执行器
# ═══════════════════════════════════════════════════════════

class CypherExecutor:
    """在 Store 上执行标准 Cypher AST（读写）。"""

    def __init__(self, store):
        self.store = store

    def execute(self, query: CypherQuery) -> List[dict]:
        if not query.nodes:
            return []

        action = query.action

        # ── 写操作 ──
        if action == "CREATE":
            if query.create_rels:
                return self._execute_create_edge(query)
            return self._execute_create_node(query)
        if action == "DELETE":
            return self._execute_delete(query)
        if action == "SET":
            return self._execute_set(query)

        # ── 读操作 ──
        if not query.rels:
            return self._execute_single(query)

        var_rel = next((r for r in query.rels
                        if r.max_hops == 0 or r.max_hops > 1), None)
        if var_rel:
            return self._execute_varlen(query, var_rel)

        return self._execute_traverse(query)

    # ════════════════════════════════════════════════════════
    #  写操作
    # ════════════════════════════════════════════════════════

    def _execute_create_node(self, query: CypherQuery) -> List[dict]:
        """CREATE (n:Label {name: "x", path: "data/x.db"})"""
        node = query.nodes[0]
        props = dict(node.props)
        name = props.pop("name", None)
        if not name:
            return [{"error": "CREATE requires a 'name' property"}]

        meta = {}
        # 剩余内联属性写入 meta（排除基础属性）
        for k, v in props.items():
            if k not in ("labels", "project"):
                meta[k] = v

        # 收集 edges（来自 query.create_rels）
        edges = []
        if query.rels:
            for rel in query.rels:
                other_var = rel.to_var if rel.from_var == node.var else rel.from_var
                other_node = next((n for n in query.nodes if n.var == other_var), None)
                if other_node:
                    other_name = other_node.props.get("name")
                    if other_name:
                        edges.append({"a": name, "b": other_name})

        ent_id = self.store.create_node(name, meta=meta,
                                        labels=node.labels or None,
                                        edges=edges or None)
        result = _node_result(self.store, ent_id) or {"name": name}
        return [{"created": result}]

    def _execute_create_edge(self, query: CypherQuery) -> List[dict]:
        """MATCH (a {name:"x"}),(b {name:"y"}) CREATE (a)--(b)"""
        # 找到 MATCH 匹配的节点对
        matched = self._execute_match_only(query)
        if not matched:
            return []

        edges_to_add = []
        for row in matched:
            pair = []
            for var_a, var_b in query.create_rels:
                a_info = row.get(var_a)
                b_info = row.get(var_b)
                if a_info and b_info:
                    a_name = a_info.get("name")
                    b_name = b_info.get("name")
                    if a_name and b_name:
                        pair.append({"a": a_name, "b": b_name})
            edges_to_add.extend(pair)

        if not edges_to_add:
            return []

        # 去重
        seen = set()
        unique = []
        for e in edges_to_add:
            key = frozenset([e["a"], e["b"]])
            if key not in seen:
                seen.add(key)
                unique.append(e)

        self.store.add_edges(unique)
        return [{"created_edges": len(unique)}]

    def _execute_delete(self, query: CypherQuery) -> List[dict]:
        """MATCH (n {name: "x"}) DELETE n"""
        matched = self._execute_match_only(query)
        deleted = []
        skipped = []

        for row in matched:
            for var in query.return_vars or [n.var for n in query.nodes]:
                info = row.get(var)
                if not info or not isinstance(info, dict):
                    continue
                name = info.get("name")
                if not name:
                    continue

                # 检查是否虚实体
                ent_id = self.store._resolve_to_id(name)
                if not ent_id or _is_virtual(ent_id):
                    skipped.append({"name": name, "reason": "virtual node"})
                    continue

                removed = self.store.delete_node(name)
                if removed:
                    deleted.append({"name": removed})

        result = []
        if deleted:
            result.append({"deleted": deleted})
        if skipped:
            result.append({"skipped": skipped})
        return result

    def _execute_set(self, query: CypherQuery) -> List[dict]:
        """MATCH (n {name: "x"}) SET n.brief = "...", n.detail = "..." """
        matched = self._execute_match_only(query)
        updated = []
        skipped = []

        # 按 var 分组 SET 子句
        var_sets = {}
        for sc in query.set_clauses:
            var_sets.setdefault(sc.var, {})[sc.prop] = sc.value

        for row in matched:
            for var, fields in var_sets.items():
                info = row.get(var)
                if not info or not isinstance(info, dict):
                    continue
                name = info.get("name")
                if not name:
                    continue

                # 检查是否虚实体
                ent_id = self.store._resolve_to_id(name)
                if not ent_id or _is_virtual(ent_id):
                    skipped.append({"name": name, "reason": "virtual node"})
                    continue

                # 过滤掉系统属性
                safe_fields = {k: v for k, v in fields.items()
                               if not k.startswith("_")}
                if not safe_fields:
                    continue

                self.store.set_meta(name, safe_fields)
                updated.append({"name": name, "set": safe_fields})

        result = []
        if updated:
            result.append({"updated": updated})
        if skipped:
            result.append({"skipped": skipped})
        return result

    # ── 内部：MATCH 匹配（不含 RETURN 格式化） ──

    def _execute_match_only(self, query: CypherQuery) -> List[dict]:
        """执行 MATCH 匹配部分，返回原始结果（供 DELETE/SET/CREATE 使用）。

        对于无关系的多个节点（MATCH (a),(b)），做笛卡尔积。
        """
        # 单节点或有关联关系的多节点 → 复用现有读逻辑
        if len(query.nodes) == 1 or query.rels:
            if not query.rels:
                return self._execute_single(query)
            var_rel = next((r for r in query.rels
                            if r.max_hops == 0 or r.max_hops > 1), None)
            if var_rel:
                return self._execute_varlen(query, var_rel)
            return self._execute_traverse(query)

        # 多个独立节点 → 笛卡尔积
        per_node = []
        for node in query.nodes:
            sub_query = CypherQuery(nodes=[node], where=[
                w for w in query.where if w.var == node.var
            ])
            results = self._execute_single(sub_query)
            per_node.append(results)

        # 笛卡尔积
        import itertools
        combined = []
        for combo in itertools.product(*per_node):
            row = {}
            for r in combo:
                row.update(r)
            combined.append(row)
        return combined

    # ════════════════════════════════════════════════════════
    #  读操作
    # ════════════════════════════════════════════════════════

    def _execute_single(self, query: CypherQuery) -> List[dict]:
        node = query.nodes[0]
        wheres = query.where
        needs_meta = self._needs_meta(node, wheres)

        self.store._ensure_index()
        results = []
        for eid, name in self.store._id_index.items():
            labels = self.store._get_labels_by_id(eid)
            if not node.matches_labels(labels):
                continue
            props = _entity_props(self.store, eid, name, labels)
            if needs_meta:
                meta = self.store.get_meta(name, include_props=[]) or {}
                props.update(meta)
            if not _inline_props_match(node.props, props):
                continue
            if not _where_matches(wheres, node.var, props):
                continue
            node_res = _node_result(self.store, eid)
            results.append({node.var: node_res or props})

        # 虚实体
        for vkey, vname, vlabels, vtype in self.store.discover_virtual("*"):
            if not node.matches_labels(vlabels):
                continue
            props = _entity_props(self.store, None, vname, vlabels)
            if not _inline_props_match(node.props, props):
                continue
            if not _where_matches(wheres, node.var, props):
                continue
            results.append({node.var: {"id": "", "name": vname, "labels": vlabels, "project": ""}})

        return results

    # ── 固定长度遍历 ──

    def _execute_traverse(self, query: CypherQuery) -> List[dict]:
        nodes = query.nodes
        wheres = query.where

        first = nodes[0]
        seeds = self._seed_nodes(first, wheres)
        paths = [tuple([eid]) for eid in seeds]

        for i, rel in enumerate(query.rels):
            target = nodes[i + 1]
            needs_meta = self._needs_meta(target, wheres)
            next_paths = []
            for path in paths:
                eid = path[-1]
                visited = set(path)
                for adj_eid in self._adjacent_of(eid):
                    if adj_eid in visited:
                        continue
                    adj_name = self.store._id_index.get(adj_eid)
                    if not adj_name:
                        if isinstance(adj_eid, str) and adj_eid.startswith("__vdir__"):
                            key = adj_eid[len("__vdir__"):]
                            adj_name = os.path.basename(key) if key != "." else "."
                        elif isinstance(adj_eid, str) and adj_eid.startswith("__vfile__"):
                            key = adj_eid[len("__vfile__"):]
                            adj_name = os.path.basename(key)
                        else:
                            continue
                    adj_labels = self.store._get_labels_by_id(adj_eid)
                    if not target.matches_labels(adj_labels):
                        continue
                    props = _entity_props(self.store, adj_eid, adj_name, adj_labels)
                    if needs_meta:
                        meta = self.store.get_meta(adj_name, include_props=[]) or {}
                        props.update(meta)
                    if not _inline_props_match(target.props, props):
                        continue
                    if not _where_matches(wheres, target.var, props):
                        continue
                    next_paths.append(path + (adj_eid,))
            paths = next_paths

        results = []
        ret_vars = query.return_vars if query.return_vars else [n.var for n in nodes]
        for path in paths:
            row = {}
            for i, node in enumerate(nodes):
                if i < len(path):
                    nr = _node_result(self.store, path[i])
                    if nr:
                        row[node.var] = nr
            for v in ret_vars:
                if v not in row:
                    row[v] = None
            results.append(row)

        return results

    # ── 可变长度路径 ──

    def _execute_varlen(self, query: CypherQuery, var_rel: RelPattern) -> List[dict]:
        from_node = next(n for n in query.nodes if n.var == var_rel.from_var)
        to_node = next(n for n in query.nodes if n.var == var_rel.to_var)
        wheres = query.where
        unbounded = var_rel.max_hops == 0
        max_hops = var_rel.max_hops if not unbounded else float('inf')

        seeds = self._seed_nodes(from_node, wheres)

        results = []
        for seed in seeds:
            visited = {seed}
            queue = deque([(seed, 1)])
            while queue:
                eid, depth = queue.popleft()
                if depth > max_hops:
                    continue
                for adj_eid in self._adjacent_of(eid):
                    if adj_eid in visited:
                        continue
                    adj_name = self.store._id_index.get(adj_eid)
                    if not adj_name:
                        continue
                    adj_labels = self.store._get_labels_by_id(adj_eid)

                    if to_node.matches_labels(adj_labels):
                        props = _entity_props(self.store, adj_eid, adj_name, adj_labels)
                        if _where_matches(wheres, to_node.var, props):
                            if depth >= var_rel.min_hops:
                                seed_nr = _node_result(self.store, seed)
                                adj_nr = _node_result(self.store, adj_eid)
                                if seed_nr and adj_nr:
                                    results.append({
                                        from_node.var: seed_nr,
                                        to_node.var: adj_nr,
                                    })

                    if depth < max_hops:
                        visited.add(adj_eid)
                        queue.append((adj_eid, depth + 1))

        seen = set()
        deduped = []
        for row in results:
            key = (row[from_node.var]["name"], row[to_node.var]["name"])
            if key not in seen:
                seen.add(key)
                deduped.append(row)
        return deduped

    # ── 辅助方法 ──

    def _needs_meta(self, node: NodePattern, wheres: List[WhereClause]) -> bool:
        """是否需要加载完整 meta（内联属性或 WHERE 引用了非基础属性）。"""
        base_keys = {"name", "labels", "project"}
        if any(k not in base_keys for k in node.props):
            return True
        return any(w.prop not in base_keys
                   for w in wheres if w.var == node.var)

    def _seed_nodes(self, node: NodePattern,
                    wheres: List[WhereClause]) -> Set[str]:
        """收集匹配节点模式的所有实体 ID（含虚实体）。"""
        needs_meta = self._needs_meta(node, wheres)
        self.store._ensure_index()
        ids = set()
        for eid, name in self.store._id_index.items():
            labels = self.store._get_labels_by_id(eid)
            if not node.matches_labels(labels):
                continue
            props = _entity_props(self.store, eid, name, labels)
            if needs_meta:
                meta = self.store.get_meta(name, include_props=[]) or {}
                props.update(meta)
            if not _inline_props_match(node.props, props):
                continue
            if not _where_matches(wheres, node.var, props):
                continue
            ids.add(eid)

        for vkey, vname, vlabels, vtype in self.store.discover_virtual("*"):
            if not node.matches_labels(vlabels):
                continue
            props = _entity_props(self.store, None, vname, vlabels)
            if not _inline_props_match(node.props, props):
                continue
            if not _where_matches(wheres, node.var, props):
                continue
            if vtype == "dir":
                ids.add(f"__vdir__{vkey}")
            else:
                ids.add(f"__vfile__{vkey}")

        return ids

    def _adjacent_of(self, eid: str) -> Set[str]:
        if isinstance(eid, str) and eid.startswith("__vdir__"):
            vkey = eid[len("__vdir__"):]
            result = set()
            for child_key, child_name, child_labels in self.store.get_virtual_neighbors(vkey):
                if isinstance(child_key, str) and child_key.startswith("ent_"):
                    result.add(child_key)
                else:
                    result.add(f"__vdir__{child_key}")
            return result
        if isinstance(eid, str) and eid.startswith("__vfile__"):
            return set()
        return self.store._adjacent.get(eid, set())
