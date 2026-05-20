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

- ref 是图谱访问语法；不带 project 路由时匹配当前打开的全部 Project，`project::ref` 限定单个 Project。
- ref 的 `/` 表示图边路径，`:` 表示当前路径段的标签。
- `find` 第一列与输入 ref 使用同一套路径逻辑；`meta` 的 Related 只显示邻接名称，访问邻接节点时用 `主节点ref/邻接名称`。
- 从具体入口开始探索：先 file，再 table/col/pattern/chunk，再数据验证。
- 已有工具结果可作为证据复用；同一事实用新查询验证时要改变验证角度。
- 结果截断时先按 offset 翻页，保持同一查询语义。
- 回答基于工具返回的事实，用中文表达。

### 常用入口

| 调用 | 用途 |
|---|---|
| `find({{"ref":"*:file"}})` | 列出全部已打开 Project 的文件实体 |
| `find({{"ref":"*:file:db"}})` | 列出全部已打开 Project 的 DB 文件实体 |
| `find({{"ref":"*:file:csv"}})` | 列出全部已打开 Project 的 CSV/TSV 文件实体 |
| `find({{"ref":"results.db:db/*:table"}})` | 列出某个 DB 文件的表实体 |
| `find({{"ref":"data.csv:csv/*:col"}})` | 列出某个 CSV 的列实体 |
| `find({{"ref":"data.json/*:pattern"}})` | 列出某个 JSON 的 pattern 实体 |
| `find({{"ref":"knowledge.md/*:chunk"}})` | 列出某个文本文件的 chunk 实体 |
| `find({{"ref":"*:col", "query":"track number"}})` | 在列实体中匹配 track number 相关摘要 |

### 查询示例

```json
{{"ref":"data.csv:file:csv:text","sql":"SELECT status, COUNT(*) AS n FROM this GROUP BY status"}}
```

```json
{{"ref":".","sql":"SELECT c.name, COUNT(*) AS n FROM customers c JOIN orders o ON c.id = o.customer_id GROUP BY c.name"}}
```

### 写入任务建议

- 创建实体时，把实体自身语义写进 meta，把来源关系写进 edges。
- 批量写 brief/detail 时，连续使用 update_meta。
- 子智能体任务写清目标、已知信息、具体要求和输出格式。
"""
