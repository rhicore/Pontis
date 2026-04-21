"""
Update meta tool - Merge-write metadata for existing nodes.

Only allows updating brief and detail fields.
"""

_ALLOWED_FIELDS = {"brief", "detail"}


def update_meta_command(
    store,
    ref: str,
    fields: dict,
) -> str:
    """
    Merge-write metadata for a node.

    Args:
        store: Store instance
        ref: ref string (file path, path::entity, or ent_id)
        fields: Fields to update/merge (only brief and detail allowed)

    Returns:
        Success/error message
    """
    if not store.pontis_exists:
        return f"Error: .pontis directory not found in {store.project_path}"

    # 验证字段白名单
    invalid = set(fields.keys()) - _ALLOWED_FIELDS
    if invalid:
        return f"错误: 不允许修改 {', '.join(sorted(invalid))}。只允许修改: {', '.join(sorted(_ALLOWED_FIELDS))}"

    # Verify node exists
    if not store.node_exists(ref):
        meta = store.get_meta(ref)
        if meta is None:
            return f"Node not found: {ref}"

    store.set_meta(ref, fields)

    # 返回实际写入的值，避免 agent 额外调 meta 验证
    written = []
    for k, v in fields.items():
        written.append(f"  {k}: {v}")
    return f"OK {ref}:\n" + "\n".join(written)


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 4:
        print("Usage: python -m tool_use.update_meta.tool <project_path> <ref> <fields_json>")
        sys.exit(1)

    from storage import Store
    _store = Store(sys.argv[1])
    _ref = sys.argv[2]
    _fields = json.loads(sys.argv[3])
    print(update_meta_command(_store, _ref, _fields))
