"""Create entity tool — 统一创建入口。

创建规则（类似 mkdir）：
  - ref 必须是精确名称，不允许通配符 (*, ?, [])
  - labels 从 ref 中的 :tag 语法提取
  - 不自动连边，通过 edges 参数显式指定
"""

from tool.utils import execute_cypher
from storage.query_inspector import cypher_label_clause, is_valid_label
from tool.utils.knowledge_meta import is_bird_knowledge, normalize_knowledge_meta
from tool.utils.resolve import resolve_entity_selector


def _selector_pattern(selector: dict, var: str, prefix: str) -> tuple[str, dict]:
    labels = cypher_label_clause(selector.get("labels", []))
    if selector.get("path"):
        return f"({var}{labels} {{path: ${prefix}_path}})", {f"{prefix}_path": selector["path"]}
    if selector.get("ref"):
        ref_key = selector.get("ref_key") or "_ref"
        return f"({var}{labels} {{{ref_key}: ${prefix}_ref}})", {f"{prefix}_ref": selector["ref"]}
    return f"({var}{labels} {{name: ${prefix}_name}})", {f"{prefix}_name": selector["name"]}

def _parse_ref(ref: str) -> tuple:
    """从 ref 中提取实体名和标签。

    格式: [project::]name[:tag1[:tag2[...]]]

    Returns:
        (name, labels, project)
    """
    project = None

    # 解析 project:: 前缀
    if "::" in ref:
        idx = ref.index("::")
        project = ref[:idx]
        ref = ref[idx + 2:]

    # 解析 :tag 标签
    parts = ref.split(":")
    name = parts[0]
    labels = parts[1:] if len(parts) > 1 else []

    return (name, labels, project)


def _has_wildcards(ref: str) -> bool:
    return any(c in ref for c in '*?[]')


def create_entity_command(workspace, ref: str, meta: dict = None,
                          edges: list = None) -> str:
    """创建实体节点。

    Args:
        workspace: Workspace 实例
        ref: 实体引用，格式 [project::]name[:tag1[:tag2]]
        meta: 元数据
        edges: 边列表 [{"a": "...", "b": "..."}, ...]
    """
    # 禁止通配符
    if _has_wildcards(ref):
        return "错误: 实体名不允许包含通配符 (*, ?, [])"

    name, labels, project = _parse_ref(ref)

    if not name:
        return "错误: 实体名不能为空"
    invalid_labels = [label for label in labels if not is_valid_label(label)]
    if invalid_labels:
        return f"错误: 非法标签: {', '.join(invalid_labels)}"

    existing_rows = workspace.cypher(
        'MATCH (n {name: $name}) RETURN n',
        params={"name": name},
        project=project,
    )
    requested_labels = set(labels or [])
    if existing_rows:
        if not requested_labels:
            return f"实体已存在: {name}"
        for row in existing_rows:
            existing_labels = set(row.get("n", {}).get("labels", []))
            if existing_labels == requested_labels:
                return f"实体已存在: {name}"

    meta = normalize_knowledge_meta(project, labels, meta or {})
    if is_bird_knowledge(project, labels):
        if not str(meta.get("brief", "")).strip():
            return "错误: bird 知识实体必须提供非空 brief（可由结构化字段自动推导）"
        if not str(meta.get("detail", "")).strip():
            return "错误: bird 知识实体必须提供非空 detail（可由结构化字段自动推导）"

    props = dict(meta)
    props["name"] = name
    props["labels"] = list(labels or [])
    prop_values = {k: v for k, v in props.items() if not k.startswith("_")}

    label_str = cypher_label_clause(labels or [])
    created = execute_cypher(
        workspace,
        f"CREATE (n{label_str} {{name: $name}}) "
        "SET n.id = 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8) "
        "SET n += $props RETURN n",
        params={"name": name, "props": prop_values},
        project=project,
    )
    if not created:
        return f"Error: failed to create entity: {name}"

    # 创建显式 edges
    edge_results = []
    if edges:
        for e in edges:
            a_name = e.get("a", "")
            b_name = e.get("b", "")
            if not a_name or not b_name:
                continue
            if a_name == name or a_name == ref:
                a_selector = {"project": project, "name": name, "labels": labels}
                a_err = None
            else:
                a_selector, a_err = resolve_entity_selector(workspace, a_name)
            if a_err:
                edge_results.append(f"  跳过: {a_err}")
                continue
            if b_name == name or b_name == ref:
                b_selector = {"project": project, "name": name, "labels": labels}
                b_err = None
            else:
                b_selector, b_err = resolve_entity_selector(workspace, b_name)
            if b_err:
                edge_results.append(f"  跳过: {b_err}")
                continue
            if not a_selector or not b_selector:
                edge_results.append(f"  跳过: 无法解析端点 '{a_name}' / '{b_name}'")
                continue
            a_pat, a_params = _selector_pattern(a_selector, "a", "a")
            b_pat, b_params = _selector_pattern(b_selector, "b", "b")
            rows = workspace.cypher(
                f"MATCH {a_pat}, {b_pat} MERGE (a)-[r:RELATED_TO]->(b) RETURN count(r) AS created",
                params={**a_params, **b_params},
                project=project,
            )
            if not rows:
                edge_results.append(f"  跳过: 无法创建边 '{a_name}' / '{b_name}'")
                continue
            edge_results.append(f"  {a_name} ↔ {b_name}")

    lines = [f"Created: {name}"]
    if labels:
        lines.append(f"Labels: {', '.join(labels)}")
    if edge_results:
        lines.append(f"Edges ({len(edge_results)}):")
        lines.extend(edge_results)

    return "\n".join(lines)


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m tool.create_entity.tool <project_name> <ref> [meta_json]")
        sys.exit(1)

    from storage.workspace import Workspace
    ws = Workspace(active_projects=[sys.argv[1]])
    _ref = sys.argv[2]
    _meta = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    print(create_entity_command(ws, _ref, _meta))
