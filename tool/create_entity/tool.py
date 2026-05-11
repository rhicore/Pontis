"""Create entity tool — 统一创建入口。

创建规则（类似 mkdir）：
  - ref 必须是精确名称，不允许通配符 (*, ?, [])
  - labels 从 ref 中的 :tag 语法提取
  - 不自动连边，通过 edges 参数显式指定
"""

from tool.utils import execute_cypher
from tool.utils.resolve import resolve_entity_selector, selector_match_pattern

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
    if not workspace.pontis_exists:
        return f"Error: .pontis directory not found in {workspace.project_path}"

    # 禁止通配符
    if _has_wildcards(ref):
        return "错误: 实体名不允许包含通配符 (*, ?, [])"

    name, labels, project = _parse_ref(ref)

    if not name:
        return "错误: 实体名不能为空"

    existing_rows = workspace.cypher(
        'MATCH (n {name: $name}) RETURN n',
        params={"name": name},
        project=project,
    )
    requested_labels = set(labels or [])
    if existing_rows:
        if not requested_labels:
            return f"Entity already exists: {name}"
        for row in existing_rows:
            existing_labels = set(row.get("n", {}).get("labels", []))
            if existing_labels == requested_labels:
                return f"Entity already exists: {name}"

    meta = meta or {}
    props = dict(meta)
    props["name"] = name
    if project:
        props["project"] = project
    prop_values = {k: v for k, v in props.items() if not k.startswith("_")}

    label_str = "".join(f":{label}" for label in (labels or []))
    created = execute_cypher(
        workspace,
        f"CREATE (n{label_str} {{name: $name}}) SET n += $props RETURN n",
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
            created_sel = {"project": project, "name": name, "labels": list(labels or [])}
            a_sel = created_sel if a_name == name or a_name == ref else None
            a_err = None
            if a_sel is None:
                a_sel, a_err = resolve_entity_selector(workspace, a_name)
            if a_err:
                edge_results.append(f"  跳过: {a_err}")
                continue
            b_sel = created_sel if b_name == name or b_name == ref else None
            b_err = None
            if b_sel is None:
                b_sel, b_err = resolve_entity_selector(workspace, b_name)
            if b_err:
                edge_results.append(f"  跳过: {b_err}")
                continue
            if not a_sel or not b_sel:
                edge_results.append(f"  跳过: 无法解析端点 '{a_name}' / '{b_name}'")
                continue
            workspace.materialize(a_sel["name"], project=project)
            workspace.materialize(b_sel["name"], project=project)
            a_match = selector_match_pattern(a_sel, "a", "a_name")
            b_match = selector_match_pattern(b_sel, "b", "b_name")
            execute_cypher(
                workspace,
                f"MATCH {a_match}, {b_match} CREATE (a)--(b)",
                params={"a_name": a_sel["name"], "b_name": b_sel["name"]},
                project=project,
            )
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
