"""
Create entity tool - Create new entity nodes in the knowledge graph.

Uses store.create_node() which handles:
- Writing meta to .pontis/nodes/{ent_id}/_meta.yml
- Auto-recording _inode for file nodes
- Auto-adding contains edge for entity nodes
- Adding user-specified edges
"""


def create_entity_command(
    store,
    ref: str,
    meta: dict = None,
    edges: list = None,
) -> str:
    """
    Create a new entity node.

    Args:
        store: Store instance
        ref: Entity ref string, e.g. "event.db::user_event_join.view"
        meta: Initial metadata dict
        edges: Optional list of edge dicts with {from, type, to}

    Returns:
        Success/error message
    """
    if not store.pontis_exists:
        return f"Error: .pontis directory not found in {store.project_path}"

    if "::" not in ref:
        return f"Error: ref must contain '::' for entity creation. Got: '{ref}'"

    if store.node_exists(ref):
        return f"Entity already exists: {ref}"

    meta = meta or {}

    store.create_node(ref, meta=meta, edges=edges)

    return f"Created entity: {ref}"


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m tool_use.create_entity.tool <project_path> <ref> [meta_json]")
        sys.exit(1)

    from storage import Store
    _store = Store(sys.argv[1])
    _ref = sys.argv[2]
    _meta = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    print(create_entity_command(_store, _ref, _meta))
