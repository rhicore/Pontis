"""Cypher 查询工具 — 标准 Cypher 的透传封装。"""


def cypher_command(workspace, query: str, offset: int = 0,
                   limit: int = 100, params: dict = None) -> str:
    """执行标准 Cypher 查询，直接透传给 workspace。

    Args:
        workspace: Workspace 实例
        query: 标准 Cypher 查询语句
        offset: 起始偏移
        limit: 最大返回条数
        params: 参数化查询参数
    """
    results = workspace.cypher(query, params=params)

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
                labels_str = "".join(f":{l}" for l in labels)
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
