"""Update meta tool — 更新实体元数据。"""

from tool.utils import execute_cypher
from tool.utils.resolve import resolve_entity_selector, selector_match_pattern

_ALLOWED_FIELDS = {"brief", "detail"}


def update_meta_command(workspace, ref: str, fields: dict) -> str:
    """通过 Cypher SET 更新实体元数据。

    ref 支持两种模式：
      - 精确名称 → 直接匹配
      - glob 模式 → 必须匹配唯一实体
    """
    if not workspace.pontis_exists:
        return f"Error: .pontis directory not found in {workspace.project_path}"

    invalid = set(fields.keys()) - _ALLOWED_FIELDS
    if invalid:
        return f"错误: 不允许修改 {', '.join(sorted(invalid))}。只允许修改: {', '.join(sorted(_ALLOWED_FIELDS))}"

    safe_fields = {k: v for k, v in fields.items() if not k.startswith("_")}
    if not safe_fields:
        return "错误: 没有有效的字段可更新"

    selector, err = resolve_entity_selector(workspace, ref)
    if err:
        return f"Error: {err}"

    project = selector["project"]
    match = selector_match_pattern(selector, "n")
    rows = execute_cypher(
        workspace,
        f"MATCH {match} RETURN n",
        params={"name": selector["name"]},
        project=project,
    )
    if not rows:
        if not workspace.materialize(selector["name"], project=project):
            return f"Error: entity disappeared before update (ref={ref})"
        rows = execute_cypher(
            workspace,
            f"MATCH {match} RETURN n",
            params={"name": selector["name"]},
            project=project,
        )
        if not rows:
            return f"Error: entity disappeared before update (ref={ref})"

    set_parts = [f"n.{k} = ${k}" for k in safe_fields]
    execute_cypher(
        workspace,
        f"MATCH {match} SET {', '.join(set_parts)} RETURN n",
        params={"name": selector["name"], **safe_fields},
        project=project,
    )
    name = rows[0]["n"].get("name", ref)
    written = []
    for k, v in safe_fields.items():
        if k == "detail":
            line_count = str(v).count("\n") + 1
            written.append(f"  detail: {len(str(v))} chars, {line_count} lines")
        else:
            written.append(f"  {k}: {v}")

    return f"OK {name}:\n" + "\n".join(written)
