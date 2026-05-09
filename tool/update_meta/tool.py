"""Update meta tool — 更新实体元数据。"""

from tool.utils.resolve import resolve_entity

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

    # 解析实体引用
    eid, err = resolve_entity(workspace, ref)
    if err:
        return f"Error: {err}"

    project = ref.split("::", 1)[0] if "::" in ref else None
    store = workspace._get_store(project)
    if store is None:
        return "Error: no active store"

    if eid not in store._id_index:
        return f"Error: entity disappeared before update (ref={ref})"

    store._set_meta(eid, safe_fields)
    name = store._id_index.get(eid, {}).get("name", ref)
    written = []
    for k, v in safe_fields.items():
        if k == "detail":
            line_count = str(v).count("\n") + 1
            written.append(f"  detail: {len(str(v))} chars, {line_count} lines")
        else:
            written.append(f"  {k}: {v}")

    return f"OK {name}:\n" + "\n".join(written)
