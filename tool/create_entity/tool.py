"""Create entity tool — 统一创建入口，通过 Cypher 执行。

创建规则（类似 mkdir）：
  - ref 必须是精确名称，不允许通配符 (*, ?, [])
  - labels 从 ref 中的 :tag 语法提取
  - 不自动连边，通过 edges 参数显式指定
"""

from tool.utils import execute_cypher


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
    """创建实体节点，构造 Cypher CREATE 语句执行。

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

    if workspace.cypher('MATCH (n {name: $name}) RETURN n', params={"name": name}):
        return f"Entity already exists: {name}"

    meta = meta or {}

    # 构造 Cypher CREATE
    labels_str = "".join(f":{l}" for l in labels)
    props = dict(meta)
    props["name"] = name
    if project:
        props["project"] = project
    prop_values = {k: v for k, v in props.items() if not k.startswith("_")}
    cypher = f'CREATE (n{labels_str} $props)'

    execute_cypher(workspace, cypher, params={"props": prop_values})

    # 创建显式 edges
    edge_results = []
    if edges:
        for e in edges:
            a_name = e.get("a", "")
            b_name = e.get("b", "")
            if not a_name or not b_name:
                continue
            if not workspace.cypher('MATCH (n {name: $name}) RETURN n', params={"name": a_name}):
                edge_results.append(f"  跳过: 端点不存在 '{a_name}'")
                continue
            if not workspace.cypher('MATCH (n {name: $name}) RETURN n', params={"name": b_name}):
                edge_results.append(f"  跳过: 端点不存在 '{b_name}'")
                continue
            edge_cypher = 'MATCH (a {name: $a_name}),(b {name: $b_name}) CREATE (a)--(b)'
            execute_cypher(workspace, edge_cypher, params={"a_name": a_name, "b_name": b_name})
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
