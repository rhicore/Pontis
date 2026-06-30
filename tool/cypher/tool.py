"""Cypher 查询工具 — 标准 Cypher 的只读透传封装。"""

import re

from tool.utils.formatters import format_labels


_WRITE_KEYWORDS = {
    "CREATE",
    "MERGE",
    "SET",
    "DELETE",
    "DETACH",
    "REMOVE",
    "DROP",
    "LOAD",
    "FOREACH",
}
_CALL_WRITE_PREFIXES = (
    "DB.CREATE",
    "DB.INDEX",
    "DBMS.",
)


def _strip_string_literals(query: str) -> str:
    """Remove quoted strings before keyword checks."""
    return re.sub(r"""(['"])(?:\\.|(?!\1).)*\1""", " ", query, flags=re.DOTALL)


def _is_readonly_cypher(query: str) -> bool:
    cleaned = _strip_string_literals(query)
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", cleaned.upper())
    if any(token in _WRITE_KEYWORDS for token in tokens):
        return False
    return not any(
        token == "CALL" and idx + 1 < len(tokens) and tokens[idx + 1].startswith(_CALL_WRITE_PREFIXES)
        for idx, token in enumerate(tokens)
    )


def cypher_command(workspace, query: str, offset: int = 0,
                   limit: int = 100, params: dict = None,
                   project: str = None) -> str:
    """执行只读 Cypher 查询，直接透传给 workspace。

    Args:
        workspace: Workspace 实例
        query: 标准 Cypher 查询语句
        offset: 起始偏移
        limit: 最大返回条数
        params: 参数化查询参数
        project: 目标 project database；省略时由 Workspace 使用默认项目
    """
    if not _is_readonly_cypher(query):
        return (
            "Error: cypher 工具只允许只读查询。"
            "请使用 MATCH/OPTIONAL MATCH/WITH/RETURN 等读取语句，不要 CREATE/MERGE/SET/DELETE/REMOVE。"
        )

    results = workspace.cypher(query, params=params, project=project)

    if not results:
        return "(无结果)"

    page = results[offset:offset + limit]
    lines = [f"共 {len(results)} 条结果（显示 {offset+1}-{offset+len(page)}）\n"]

    for row in page:
        parts = []
        for var, info in row.items():
            if info is None:
                parts.append(f"{var}: (未追踪)")
            elif isinstance(info, dict) and "name" in info:
                labels = info.get("labels", [])
                labels_str = format_labels(labels)
                name = info.get("name", "?")
                if labels_str:
                    parts.append(f"{var}: {name} [{labels_str}]")
                else:
                    parts.append(f"{var}: {name}")
            else:
                parts.append(f"{var}: {info}")
        lines.append("  ".join(parts))

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m tool.cypher.tool <project_name> <cypher_query>")
        sys.exit(1)

    from storage.workspace import Workspace
    ws = Workspace(active_projects=[sys.argv[1]])
    print(cypher_command(ws, sys.argv[2]))
