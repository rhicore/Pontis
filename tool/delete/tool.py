"""Delete tool — 删除节点，支持级联删除。"""

from tool.config import resolve_rank
from tool.utils import execute_cypher
from tool.utils.resolve import resolve_entity_selector, selector_match_pattern


def _get_entity_rank_from_labels(labels: list[str]) -> int:
    return resolve_rank(labels)


def _is_more_derived(ref_a: str, ref_b: str, workspace) -> bool:
    a_sel, a_err = resolve_entity_selector(workspace, ref_a)
    b_sel, b_err = resolve_entity_selector(workspace, ref_b)
    if a_err or b_err:
        return False
    return _get_entity_rank_from_labels(a_sel["labels"]) > _get_entity_rank_from_labels(b_sel["labels"])


def delete_command(workspace, ref: str) -> str:
    """通过 Cypher DELETE 删除节点。

    ref 支持两种模式：
      - 精确名称 → 直接匹配
      - glob 模式 → 必须匹配唯一实体
    """
    if not workspace.pontis_exists:
        return f"错误: 未找到 .pontis 目录 ({workspace.project_path})"

    selector, err = resolve_entity_selector(workspace, ref)
    if err:
        return f"Error: {err}"

    project = selector["project"]
    name = selector["name"]
    if not name:
        return f"节点不存在: {ref}"

    workspace.materialize(name, project=project)

    # 级联查找派生实体
    # 只对已知层级关系级联：file→table, table→col, col→fk/rel/overlap
    _CASCADE_FROM = {"file", "db", "csv", "table", "view", "col"}
    to_delete = [selector]
    main_labels = set(selector["labels"])
    if main_labels & _CASCADE_FROM:
        match = selector_match_pattern(selector, "n")
        neighbor_rows = workspace.cypher(
            f"MATCH {match}--(m) RETURN m",
            params={"name": name},
            project=project,
        )
        for row in neighbor_rows:
            meta = row.get("m")
            if not meta:
                continue
            neighbor_labels = meta.get("labels", [])
            if _get_entity_rank_from_labels(neighbor_labels) > _get_entity_rank_from_labels(selector["labels"]):
                to_delete.append({
                    "project": project,
                    "name": meta.get("name", ""),
                    "labels": neighbor_labels,
                })

    # 逐个删除
    deleted = []
    for sel in to_delete:
        if not sel.get("name"):
            continue
        match = selector_match_pattern(sel, "n")
        execute_cypher(
            workspace,
            f"MATCH {match} DELETE n",
            params={"name": sel["name"]},
            project=sel.get("project"),
        )
        deleted.append(sel["name"])

    if not deleted:
        return f"删除失败: {ref}"

    lines = [f"已删除 {len(deleted)} 个节点:"]
    for d in deleted:
        lines.append(f"  - {d}")
    return "\n".join(lines)
