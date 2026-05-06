"""Create entity tool — 统一创建入口。

支持：
  - .rel / .disambig（数据实体）
  - .convention / .pattern / .term / .lesson / .example（知识实体 → global store）
"""

import re

_ALLOWED_ENTITY_RE = re.compile(
    r"__to__.*$"                                    # FK/rel/overlap: bare __to__ names
    r"|.*\.(db|sqlite|sqlite3|duckdb).*__to__.*\.(rel|disambig)$"  # legacy with suffix
    r"|.*\.(convention|pattern|term|lesson|example)$"              # knowledge entities
)

_KNOWLEDGE_SUFFIXES = {".convention", ".pattern", ".term", ".lesson", ".example"}


def _is_knowledge_entity(ref: str) -> bool:
    return any(ref.endswith(s) for s in _KNOWLEDGE_SUFFIXES)


def create_entity_command(
    obj,  # Workspace or Store
    ref: str,
    meta: dict = None,
    edges: list = None,
) -> str:
    """创建实体节点。

    知识实体自动路由到 global store（通过 workspace）。
    数据实体写入当前 project store。
    """
    # 获取 store
    if hasattr(obj, 'get_store'):
        store = obj.get_store()
    else:
        store = obj

    if hasattr(store, 'pontis_exists') and not store.pontis_exists:
        return f"Error: .pontis directory not found in {store.project_path}"

    if not _ALLOWED_ENTITY_RE.match(ref):
        return (
            "错误: 只允许创建以下类型的实体：\n"
            "  - __to__ 关系实体（FK/rel/overlap/disambig）\n"
            "  - .convention / .pattern / .term / .lesson / .example（知识实体）"
        )

    if store.node_exists(ref):
        return f"Entity already exists: {ref}"

    meta = meta or {}

    # 确定 labels
    labels = None
    project = None
    if _is_knowledge_entity(ref):
        suffix = ref.rsplit(".", 1)[-1]
        labels = [f"knowledge/{suffix}"]
        project = "global"

    # 使用 workspace 路由，或 fallback 到 store
    if hasattr(obj, 'create_entity'):
        obj.create_entity(ref, meta=meta, edges=edges, labels=labels, project=project)
    else:
        store.create_node(ref, meta=meta, edges=edges, labels=labels)

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
