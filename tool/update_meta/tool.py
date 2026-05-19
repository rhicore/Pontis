"""Update meta tool — 更新实体元数据。"""

from tool.utils.resolve import resolve_entity_selector, selector_match_pattern, selector_params

_ALLOWED_FIELDS = {"brief", "detail"}


def _split_project_ref(ref: str) -> tuple[str | None, str]:
    if "::" not in ref:
        return None, ref
    project, local_ref = ref.split("::", 1)
    return project, local_ref


def update_meta_command(workspace, ref: str, fields: dict) -> str:
    """通过 Cypher SET 更新实体元数据。

    ref 支持两种模式：
      - 精确名称 → 直接匹配
      - ref 模式 → 必须匹配唯一实体
    """
    invalid = set(fields.keys()) - _ALLOWED_FIELDS
    if invalid:
        return f"错误: 不允许修改 {', '.join(sorted(invalid))}。只允许修改: {', '.join(sorted(_ALLOWED_FIELDS))}"

    safe_fields = {k: v for k, v in fields.items() if not k.startswith("_")}
    if not safe_fields:
        return "错误: 没有有效的字段可更新"

    selector, err = resolve_entity_selector(workspace, ref)
    if err:
        return f"Error: {err}"

    project, local_ref = _split_project_ref(ref)
    if selector.get("project"):
        project = selector["project"]
    match = selector_match_pattern(selector, "n")
    rows = workspace.cypher(
        f"MATCH {match} SET n += $props RETURN n",
        params=selector_params(selector, {"props": safe_fields}),
        project=project,
    )
    if not rows:
        return f"Error: failed to update entity: {ref}"

    has_wildcards = any(c in local_ref for c in "*?[]")
    target_ref = selector.get("ref") or selector.get("path") or local_ref
    display_ref = ref if "::" in ref else (target_ref if has_wildcards else local_ref)
    written = [f"OK {display_ref}:"]
    for k, v in safe_fields.items():
        written.append(f"{k}: {v}")
    return "\n".join(written)
