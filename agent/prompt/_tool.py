"""工具层 — 工具选择策略与工作流引导。"""


_TOOL_DESCRIPTIONS = {
    "find": "发现图谱实体；按 ref 列实体，或在实体摘要中匹配 query",
    "meta": "读取单个实体的 brief/detail、统计、样例等元数据",
    "query": "对 DB/CSV/TSV/JSON records 执行只读 SQL，支持 workspace 跨源查询",
    "cypher": "执行复杂图查询，适合 find 难以表达的关系遍历",
    "grep": "在 text file ref 的原文中定位字符串或正则模式",
    "read": "按行号回读 text file ref 的原文",
    "jd": "浏览 JSON file ref 的内部层级结构",
    "bash": "执行只读 shell 命令，适合工具无法覆盖的一次性计算",
}


def get_tool_prompt(spec=None) -> str:
    tool_names = list(getattr(spec, "tools", []) or _TOOL_DESCRIPTIONS.keys())
    lines = []
    for name in tool_names:
        desc = _TOOL_DESCRIPTIONS.get(name)
        if desc:
            lines.append(f"- **{name}**：{desc}")
    tool_list = "\n".join(lines)

    return f"""## 工具使用

### 工具选择

{tool_list}

### 路由主线

1. 结构入口：用 `find` 找 file/table/col/pattern/chunk/knowledge，再用 `meta` 读目标实体。
2. JSON 层级：用 `jd` 展开 key/index；行级过滤和聚合交给 `query`。
3. 文本证据：用 `grep` 定位行号，用 `read` 回读上下文。
4. 结构化计算：DB/CSV/TSV/JSON records 默认用 `query`；跨源 join 用 `query(ref=".")`。
5. 图关系：复杂邻接、路径、多跳关系用 `cypher`。
6. shell 计算：`bash` 用于工具无法直接表达的一次性只读计算或抽样验证。

### 工作协议

- ref 是图谱访问语法；后续工具调用直接复制 `find/meta` 返回的完整 ref。
- 从具体入口开始探索：先 file，再 table/col/pattern/chunk，再数据验证。
- 已有工具结果可作为证据复用；同一事实用新查询验证时要改变验证角度。
- 结果截断时先按 offset 翻页，保持同一查询语义。
- 回答基于工具返回的事实，用中文表达。
"""
