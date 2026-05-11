"""Cypher 解析器和执行器 — Storage 层的唯一查询入口。

标准 Cypher 子集（读写）：
  MATCH (n:table) RETURN n
  MATCH (n {name: "loan"}) RETURN n
  MATCH (n:file:db)--(t:table) RETURN n, t
  MATCH (a:table)-[*1..3]-(b:col) RETURN a, b
  MATCH (n) WHERE n.path = "data/x.db" RETURN n
  MATCH (n) WHERE n.id = $id RETURN n
  CREATE (n:Label {name: "x", path: "data/x.db"})
  MATCH (n {id: $id}) DELETE n
  MATCH (a {id: $a}),(b {id: $b}) CREATE (a)--(b)

参数化查询（params）：
  MATCH (n {id: $id}) RETURN n
  MATCH (n {name: $name}) RETURN n
"""
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Union

from storage.labels import labels_match_all


# ═══════════════════════════════════════════════════════════
#  AST
# ═══════════════════════════════════════════════════════════

@dataclass
class NodePattern:
    var: str
    labels: List[str] = field(default_factory=list)
    props: Dict[str, Union[str, Any]] = field(default_factory=dict)

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
    value: Union[str, Any]


@dataclass
class SetClause:
    var: str
    prop: str = ""
    value: Union[str, Any] = ""
    param_name: Optional[str] = None  # $param 引用
    is_merge: bool = False            # SET n += $props


@dataclass
class ReturnItem:
    expr: str
    alias: str


@dataclass
class CypherQuery:
    nodes: List[NodePattern] = field(default_factory=list)
    rels: List[RelPattern] = field(default_factory=list)
    where: List[WhereClause] = field(default_factory=list)
    return_vars: List[str] = field(default_factory=list)
    return_items: List[ReturnItem] = field(default_factory=list)
    # 写操作
    action: str = "RETURN"   # RETURN | CREATE | DELETE | SET
    set_clauses: List[SetClause] = field(default_factory=list)
    create_rels: List[tuple] = field(default_factory=list)  # [(var_a, var_b), ...]
    params: dict = field(default_factory=dict)


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
# WHERE n.prop = 123 (unquoted numeric)
_WHERE_NUM_RE = re.compile(
    r"""(\w+)\.(\w+)\s*"""
    r"""(>=|<=|!=|>|<|=)\s*"""
    r"""(-?[\d]+(?:\.[\d]+)?)""",
    re.IGNORECASE
)
# WHERE n.prop = $param
_WHERE_PARAM_RE = re.compile(
    r"""(\w+)\.(\w+)\s*(=)\s*\$(\w+)""",
    re.IGNORECASE
)
_RETURN_RE = re.compile(r'RETURN\s+(.+)', re.IGNORECASE)
_CREATE_RE = re.compile(r'\bCREATE\b', re.IGNORECASE)
_DELETE_RE = re.compile(r'\bDELETE\b', re.IGNORECASE)
_SET_RE = re.compile(r'\bSET\b', re.IGNORECASE)
# SET n.prop = "literal"
_SET_CLAUSE_RE = re.compile(
    r"""(\w+)\.(\w+)\s*=\s*['"]([^'"]*)['"]"""
)
# SET n.prop = 123 (unquoted numeric)
_SET_NUM_RE = re.compile(
    r"""(\w+)\.(\w+)\s*=\s*(-?[\d]+(?:\.[\d]+)?)"""
)
# SET n.prop = $param
_SET_PARAM_RE = re.compile(
    r"""(\w+)\.(\w+)\s*=\s*\$(\w+)"""
)
# SET n += $param
_SET_MERGE_RE = re.compile(
    r"""(\w+)\s*\+=\s*\$(\w+)"""
)
# MATCH (a), (b) CREATE (a)--(b) 中的边创建
_CREATE_EDGE_RE = re.compile(r'CREATE\s+(.*?)$', re.IGNORECASE)


def _parse_props(text: str, params: dict = None) -> Dict[str, Union[str, Any]]:
    """解析 `{name: "value", key: $param}` 属性字典。"""
    if not text:
        return {}
    props = {}
    for pair in text.split(','):
        pair = pair.strip()
        if ':' not in pair:
            continue
        key, val = pair.split(':', 1)
        key = key.strip()
        val = val.strip()
        # 检查是否为 $param 引用
        if val.startswith('$') and params is not None:
            param_name = val[1:]
            if param_name in params:
                props[key] = params[param_name]
                continue
        if re.fullmatch(r"-?\d+(?:\.\d+)?", val):
            props[key] = float(val) if "." in val else int(val)
            continue
        # 去引号
        props[key] = val.strip('"').strip("'")
    return props


def parse_cypher(text: str, params: dict = None) -> CypherQuery:
    """解析标准 Cypher 子集字符串为 AST。params 用于 $var 替换。"""
    params = params or {}
    text = text.strip().rstrip(';')

    # ── 判断操作类型 ──
    action = "RETURN"

    has_match = bool(re.search(r'\bMATCH\b', text, re.IGNORECASE))
    has_set = bool(_SET_RE.search(text))
    has_create = bool(_CREATE_RE.search(text))
    has_delete = bool(_DELETE_RE.search(text))

    if has_delete:
        action = "DELETE"
    elif has_set and has_match:
        # MATCH ... SET → 更新已存在的节点
        action = "SET"
    elif has_create:
        # 纯 CREATE 或 CREATE ... SET → 创建节点（SET 作为属性补充）
        action = "CREATE"
    elif has_set:
        action = "SET"

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
    if action in ("SET", "CREATE") and has_set:
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
    # 数值比较 WHERE n.prop = 123
    for wm in _WHERE_NUM_RE.finditer(where_text):
        val_str = wm.group(4)
        value = float(val_str) if '.' in val_str else int(val_str)
        wheres.append(WhereClause(
            var=wm.group(1), prop=wm.group(2),
            op=wm.group(3), value=value,
        ))
    # WHERE n.prop = $param
    for wm in _WHERE_PARAM_RE.finditer(where_text):
        param_name = wm.group(4)
        if param_name in params:
            wheres.append(WhereClause(
                var=wm.group(1), prop=wm.group(2),
                op="=", value=params[param_name],
            ))

    # 解析 SET 子句
    set_clauses = []
    # SET n += $param（merge 模式）
    for sm in _SET_MERGE_RE.finditer(set_text):
        param_name = sm.group(2)
        set_clauses.append(SetClause(
            var=sm.group(1), is_merge=True,
            param_name=param_name,
        ))
    # SET n.prop = $param（参数化赋值）
    for sm in _SET_PARAM_RE.finditer(set_text):
        param_name = sm.group(3)
        value = params.get(param_name, "") if params else ""
        set_clauses.append(SetClause(
            var=sm.group(1), prop=sm.group(2),
            value=value, param_name=param_name,
        ))
    # SET n.prop = "literal"（字符串字面量）
    for sm in _SET_CLAUSE_RE.finditer(set_text):
        set_clauses.append(SetClause(
            var=sm.group(1), prop=sm.group(2), value=sm.group(3),
        ))
    # SET n.prop = 123（数值字面量）
    for sm in _SET_NUM_RE.finditer(set_text):
        val_str = sm.group(3)
        value = float(val_str) if '.' in val_str else int(val_str)
        set_clauses.append(SetClause(
            var=sm.group(1), prop=sm.group(2), value=value,
        ))

    # 解析 RETURN
    return_vars = []
    return_items = []
    rm = _RETURN_RE.search(text)
    if rm:
        for raw_item in rm.group(1).split(','):
            item = raw_item.strip()
            if not item:
                continue
            alias = item
            expr = item
            m = re.match(r'(.+?)\s+AS\s+(\w+)$', item, re.IGNORECASE)
            if m:
                expr = m.group(1).strip()
                alias = m.group(2).strip()
            return_items.append(ReturnItem(expr=expr, alias=alias))
            return_vars.append(alias)

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
        props = _parse_props(nm.group(3) or "", params)
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
                       return_vars=return_vars, return_items=return_items,
                       action=action,
                       set_clauses=set_clauses, create_rels=create_rels,
                       params=params)


# ═══════════════════════════════════════════════════════════
#  属性匹配
# ═══════════════════════════════════════════════════════════

def _prop_matches(actual, op: str, expected) -> bool:
    """标准 Cypher 属性比较。actual 是 Python 值，expected 是字符串或任意类型。"""
    if actual is None:
        return False
    # 精确匹配：类型一致时直接比较
    if op == "=":
        if type(actual) == type(expected) and actual == expected:
            return True
        s = str(actual)
        return s == str(expected)
    if op == "!=":
        s = str(actual)
        return s != str(expected)
    s = str(actual)
    exp_s = str(expected)
    if op == "STARTS WITH":
        return s.startswith(exp_s)
    if op == "ENDS WITH":
        return s.endswith(exp_s)
    if op == "CONTAINS":
        return exp_s in s
    # 数值比较
    try:
        num_actual = float(s)
        num_expected = float(exp_s)
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
    """检查内联属性是否全部匹配。"""
    for key, expected in props.items():
        actual = entity_props.get(key)
        if not _prop_matches(actual, "=", expected):
            return False
    return True


def _where_matches(wheres: List[WhereClause], var: str,
                   entity_props: dict) -> bool:
    """检查 WHERE 条件。"""
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

def _strip_internal(results: List[dict]) -> List[dict]:
    """递归移除内部字段。"""
    clean = []
    for row in results:
        out = {}
        for k, v in row.items():
            if isinstance(v, dict):
                out[k] = {dk: dv for dk, dv in v.items() if not dk.startswith("_")}
            else:
                out[k] = v
        clean.append(out)
    return clean


def _entity_props(store, eid: str) -> dict:
    """构建实体的框架保留访问面。"""
    props = store._id_index.get(eid, {})
    return {
        "id": eid,
        "labels": props.get("labels", []),
        "project": getattr(store, '_project_name', ''),
    }


def _node_result(store, eid: str) -> Optional[dict]:
    """将内部节点 id 转换为 Cypher 实体结果。"""
    props = store._id_index.get(eid)
    if not props:
        return None
    internal = store.internal_fields
    result = {"id": eid,
              "labels": props.get("labels", []),
              "project": getattr(store, '_project_name', '')}
    full_meta = store._get_meta(eid)
    if full_meta:
        for k, v in full_meta.items():
            if not k.startswith("_") and k not in internal and k not in result:
                result[k] = v
    for k, v in props.items():
        if not k.startswith("_") and k not in internal and k not in result:
            result[k] = v
    return result


# ═══════════════════════════════════════════════════════════
#  执行器
# ═══════════════════════════════════════════════════════════

class CypherExecutor:
    """在 Store 上执行标准 Cypher AST（读写）。"""

    def __init__(self, store):
        self.store = store

    def execute(self, query: CypherQuery, *, strip_internal: bool = True) -> List[dict]:
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
        if not query.rels and len(query.nodes) > 1:
            results = self._execute_match_only(query)
        elif not query.rels:
            results = self._execute_single(query)
        else:
            var_rel = next((r for r in query.rels
                            if r.max_hops == 0 or r.max_hops > 1), None)
            if var_rel:
                results = self._execute_varlen(query, var_rel)
            else:
                results = self._execute_traverse(query)

        results = self._project_results(query, results)
        if strip_internal:
            return _strip_internal(results)
        return results

    # ════════════════════════════════════════════════════════
    #  写操作
    # ════════════════════════════════════════════════════════

    def _execute_create_node(self, query: CypherQuery) -> List[dict]:
        """CREATE (n:Label {ordinary: "property"})"""
        node = query.nodes[0]
        meta = dict(node.props)
        for sc in query.set_clauses:
            if sc.is_merge:
                value = query.params.get(sc.param_name, {})
                if isinstance(value, dict):
                    meta.update(value)
            elif sc.prop:
                meta[sc.prop] = sc.value
        ent_id = self.store._create_node("", meta=meta, labels=node.labels or None)
        result = _node_result(self.store, ent_id)
        return [{"created": result}]

    def _execute_create_edge(self, query: CypherQuery) -> List[dict]:
        """MATCH (a {id:$a}),(b {id:$b}) CREATE (a)--(b)"""
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
                    a_id = a_info.get("id")
                    b_id = b_info.get("id")
                    if a_id and b_id:
                        pair.append({"a": a_id, "b": b_id})
            edges_to_add.extend(pair)

        if not edges_to_add:
            return []

        # 去重
        seen = set()
        unique = []
        for e in edges_to_add:
            key = frozenset({e["a"], e["b"]})
            if key not in seen:
                seen.add(key)
                unique.append(e)

        self.store._add_edges(unique)
        return [{"created_edges": len(unique)}]

    def _execute_delete(self, query: CypherQuery) -> List[dict]:
        """MATCH (...) DELETE n"""
        matched = self._execute_match_only(query)
        deleted = []
        skipped = []

        for row in matched:
            for var in query.return_vars or [n.var for n in query.nodes]:
                info = row.get(var)
                if not info or not isinstance(info, dict):
                    continue
                eid = info.get("id")
                if not eid:
                    continue

                removed = self.store._delete_node(eid)
                if removed:
                    deleted.append({"removed": True})

        result = []
        if deleted:
            result.append({"deleted": deleted})
        if skipped:
            result.append({"skipped": skipped})
        return result

    def _execute_set(self, query: CypherQuery) -> List[dict]:
        """MATCH (...) SET n.prop = value"""
        matched = self._execute_match_only(query)
        updated = []
        skipped = []

        # 收集所有 SET 操作，按 var 分组
        # merge 子句（n += $param）和普通子句（n.key = val）分别处理
        var_merges = {}  # var → dict to merge
        var_sets = {}    # var → {prop: value}
        for sc in query.set_clauses:
            if sc.is_merge:
                # SET n += $param — 从 params 取值
                value = query.params.get(sc.param_name, {})
                if isinstance(value, dict):
                    existing = var_merges.get(sc.var, {})
                    existing.update(value)
                    var_merges[sc.var] = existing
            else:
                var_sets.setdefault(sc.var, {})[sc.prop] = sc.value

        for row in matched:
            for var in set(list(var_merges.keys()) + list(var_sets.keys())):
                info = row.get(var)
                if not info or not isinstance(info, dict):
                    continue
                eid = info.get("id")
                if not eid:
                    continue

                # 合并 merge 和 set
                fields = {}
                if var in var_merges:
                    fields.update(var_merges[var])
                if var in var_sets:
                    fields.update(var_sets[var])

                safe_fields = {k: v for k, v in fields.items()
                               if not k.startswith("_") and k not in ("id", "project", "src")}
                if not safe_fields:
                    continue

                if "labels" in safe_fields:
                    labels = safe_fields["labels"]
                    if isinstance(labels, str):
                        labels = [labels]
                    safe_fields["labels"] = labels
                self.store._set_meta(eid, safe_fields)
                updated.append({"set": list(safe_fields.keys())})

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
        for eid, props in self.store._id_index.items():
            labels = props.get("labels", [])
            if not node.matches_labels(labels):
                continue
            ent_props = _entity_props(self.store, eid)
            if needs_meta:
                meta = self.store._get_meta(eid, include_props=[]) or {}
                ent_props.update(meta)
            if not _inline_props_match(node.props, ent_props):
                continue
            if not _where_matches(wheres, node.var, ent_props):
                continue
            node_res = _node_result(self.store, eid)
            results.append({node.var: node_res or ent_props})

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
                    adj_props = self.store._id_index.get(adj_eid)
                    if not adj_props:
                        continue
                    adj_labels = adj_props.get("labels", [])
                    if not target.matches_labels(adj_labels):
                        continue
                    ent_props = _entity_props(self.store, adj_eid)
                    if needs_meta:
                        meta = self.store._get_meta(adj_eid, include_props=[]) or {}
                        ent_props.update(meta)
                    if not _inline_props_match(target.props, ent_props):
                        continue
                    if not _where_matches(wheres, target.var, ent_props):
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
                    adj_props = self.store._id_index.get(adj_eid)
                    if not adj_props:
                        continue
                    adj_labels = adj_props.get("labels", [])

                    if to_node.matches_labels(adj_labels):
                        ent_props = _entity_props(self.store, adj_eid)
                        if _where_matches(wheres, to_node.var, ent_props):
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
            key = (row[from_node.var].get("id"), row[to_node.var].get("id"))
            if key not in seen:
                seen.add(key)
                deduped.append(row)
        return deduped

    # ── 辅助方法 ──

    def _needs_meta(self, node: NodePattern, wheres: List[WhereClause]) -> bool:
        """非核心属性通过普通/虚属性元数据参与匹配。"""
        base_keys = {"id", "labels", "project", "src"}
        if any(k not in base_keys for k in node.props):
            return True
        return any(w.prop not in base_keys
                   for w in wheres if w.var == node.var)

    def _seed_nodes(self, node: NodePattern,
                    wheres: List[WhereClause]) -> Set[str]:
        """收集匹配节点模式的所有实体 ID。"""
        needs_meta = self._needs_meta(node, wheres)
        self.store._ensure_index()
        ids = set()
        for eid, props in self.store._id_index.items():
            labels = props.get("labels", [])
            if not node.matches_labels(labels):
                continue
            ent_props = _entity_props(self.store, eid)
            if needs_meta:
                meta = self.store._get_meta(eid, include_props=[]) or {}
                ent_props.update(meta)
            if not _inline_props_match(node.props, ent_props):
                continue
            if not _where_matches(wheres, node.var, ent_props):
                continue
            ids.add(eid)

        return ids

    def _adjacent_of(self, eid: str) -> Set[str]:
        return self.store._adjacent.get(eid, set())

    def _project_results(self, query: CypherQuery, results: List[dict]) -> List[dict]:
        """按 RETURN 投影结果。支持普通变量、变量属性、n.src 虚属性。"""
        if not query.return_items:
            return results

        projected = []
        for row in results:
            out = {}
            for item in query.return_items:
                out[item.alias] = self._resolve_return_expr(item.expr, row)
            projected.append(out)
        return projected

    def _resolve_return_expr(self, expr: str, row: dict):
        if "." not in expr:
            return row.get(expr)

        var, prop = expr.split(".", 1)
        base = row.get(var)
        if not isinstance(base, dict):
            return None

        if prop == "src":
            return self.store.bind_src(base)

        return base.get(prop)
