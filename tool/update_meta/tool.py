"""Update meta tool — 通过 Cypher SET 更新实体元数据。"""

from tool.utils import cypher_escape, execute_cypher
from tool.utils.resolve import resolve_entity

_ALLOWED_FIELDS = {"brief", "detail"}


def update_meta_command(obj, ref: str, fields: dict) -> str:
    """通过 Cypher SET 更新实体元数据。

    ref 支持两种模式：
      - 精确名称 → 直接匹配
      - glob 模式 → 必须匹配唯一实体
    """
    store = obj if not hasattr(obj, 'get_store') else obj.get_store()

    if hasattr(store, 'pontis_exists') and not store.pontis_exists:
        return f"Error: .pontis directory not found in {store.project_path}"

    invalid = set(fields.keys()) - _ALLOWED_FIELDS
    if invalid:
        return f"错误: 不允许修改 {', '.join(sorted(invalid))}。只允许修改: {', '.join(sorted(_ALLOWED_FIELDS))}"

    safe_fields = {k: v for k, v in fields.items() if not k.startswith("_")}
    if not safe_fields:
        return "错误: 没有有效的字段可更新"

    # 解析实体引用
    eid, err = resolve_entity(obj, ref)
    if err:
        return f"Error: {err}"

    # 构造 Cypher
    set_parts = [f'n.{k} = "{cypher_escape(v)}"' for k, v in safe_fields.items()]
    set_clause = ", ".join(set_parts)
    cypher = f'MATCH (n {{id: "{eid}"}}) SET {set_clause}'

    results = execute_cypher(obj, cypher)

    if not results:
        return f"Error: update failed (ref={ref})"

    updated = results[0].get("updated", [])
    if not updated:
        return f"Error: update failed (ref={ref})"

    name = updated[0].get("name", "?")
    written = []
    for k, v in safe_fields.items():
        if k == "detail":
            line_count = str(v).count("\n") + 1
            written.append(f"  detail: {len(str(v))} chars, {line_count} lines")
        else:
            written.append(f"  {k}: {v}")

    return f"OK {name}:\n" + "\n".join(written)
