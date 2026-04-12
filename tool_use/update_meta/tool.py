"""
Update meta tool - Merge-write metadata for existing nodes.

Uses store.set_meta() which:
- Only updates fields present in `fields` dict
- Preserves existing fields not mentioned
- Auto-maintains internal fields (_id, _entity_name, _files, _inode)
"""


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
        fields: Fields to update/merge

    Returns:
        Success/error message
    """
    if not store.pontis_exists:
        return f"Error: .pontis directory not found in {store.project_path}"

    # Verify node exists
    if not store.node_exists(ref):
        # Also try get_meta for unindexed files
        meta = store.get_meta(ref)
        if meta is None:
            return f"Node not found: {ref}"

    store.set_meta(ref, fields)

    return f"Updated {ref}: {', '.join(fields.keys())}"


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
