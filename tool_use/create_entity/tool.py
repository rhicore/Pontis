"""
Create entity tool - Create new entity nodes in the knowledge graph.

Currently only allows creating .rel entities under .db files.
"""

import re

_ALLOWED_ENTITY_RE = re.compile(r".*\.(db|sqlite|sqlite3|duckdb)::.*\.rel$")


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
        ref: Entity ref string, e.g. "event.db::users__orders.rel"
        meta: Initial metadata dict
        edges: Optional list of edge dicts

    Returns:
        Success/error message
    """
    if not store.pontis_exists:
        return f"Error: .pontis directory not found in {store.project_path}"

    if "::" not in ref:
        return f"Error: ref must contain '::' for entity creation. Got: '{ref}'"

    if not _ALLOWED_ENTITY_RE.match(ref):
        return f"错误: 目前只允许创建 .rel 实体（逻辑关系），ref 格式应为 *.db::**.rel"

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
