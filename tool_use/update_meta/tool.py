"""
Update meta tool - Update entity metadata.

Merge-write: only updates provided fields, preserves the rest.
"""

_ALLOWED_FIELDS = {"brief", "detail"}


def update_meta_command(
    store,
    ref: str,
    fields: dict,
) -> str:
    """
    Update metadata for a node.

    Args:
        store: Store instance
        ref: entity reference
        fields: meta fields to merge-write (brief and/or detail)

    Returns:
        Success/error message
    """
    if not store.pontis_exists:
        return f"Error: .pontis directory not found in {store.project_path}"

    invalid = set(fields.keys()) - _ALLOWED_FIELDS
    if invalid:
        return f"错误: 不允许修改 {', '.join(sorted(invalid))}。只允许修改: {', '.join(sorted(_ALLOWED_FIELDS))}"

    if not store.node_exists(ref):
        return f"Node not found: {ref}"

    try:
        store.set_meta(ref, fields)
    except RuntimeError as e:
        return f"错误: {e}"

    written = []
    for k, v in fields.items():
        if k == "detail":
            line_count = str(v).count("\n") + 1
            written.append(f"  detail: {len(str(v))} chars, {line_count} lines")
        else:
            written.append(f"  {k}: {v}")

    return f"OK {ref}:\n" + "\n".join(written)
