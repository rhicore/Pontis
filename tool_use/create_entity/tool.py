"""
Create entity tool - Create new entity nodes in the knowledge graph.

Supports:
  - .rel / .disambig under .db files (data entities → project store)
  - .convention / .pattern / .term / .lesson / .example (knowledge entities → global store)
"""

import re

_ALLOWED_ENTITY_RE = re.compile(
    r".*\.(db|sqlite|sqlite3|duckdb)::.*\.(rel|disambig)$"
    r"|.*\.(convention|pattern|term|lesson|example)$"
)

_KNOWLEDGE_SUFFIXES = {".convention", ".pattern", ".term", ".lesson", ".example"}


def _is_knowledge_entity(ref: str) -> bool:
    return any(ref.endswith(s) for s in _KNOWLEDGE_SUFFIXES)


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
        ref: Entity ref string
        meta: Initial metadata dict
        edges: Optional list of edge dicts

    Returns:
        Success/error message
    """
    if not store.pontis_exists:
        return f"Error: .pontis directory not found in {store.project_path}"

    if not _ALLOWED_ENTITY_RE.match(ref):
        return (
            "错误: 只允许创建以下类型的实体：\n"
            "  - .rel / .disambig（数据关系实体，需在 .db 下）\n"
            "  - .convention / .pattern / .term / .lesson / .example（知识实体）"
        )

    if store.node_exists(ref):
        return f"Entity already exists: {ref}"

    meta = meta or {}

    # 知识实体 → 全局 store，命名空间 ["knowledge", "global"]
    namespaces = None
    if _is_knowledge_entity(ref):
        namespaces = ["knowledge", "global"]

    store.create_node(ref, meta=meta, edges=edges, namespaces=namespaces)

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
