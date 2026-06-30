"""工具层 — 工具选择策略与工作流引导。"""


_TOOL_DESCRIPTIONS = {
    "find": "发现图谱实体；按 ref 列实体，或在实体摘要和官方字段中匹配 query",
    "meta": "读取单个实体的官方字段、brief/detail、统计、样例等元数据",
    "query": "对 DB/CSV/TSV/JSON records 执行只读 SQL，支持 workspace 跨源查询",
    "grep": "在 text file ref 的原文中定位字符串或正则模式",
    "read": "按行号回读 text file ref 的原文",
    "jd": "浏览 JSON file ref 的内部层级结构",
}


def get_tool_prompt(spec=None) -> str:
    tool_names = list(getattr(spec, "tools", []) or _TOOL_DESCRIPTIONS.keys())
    query_mode = getattr(spec, "query_mode", "")
    lines = []
    for name in tool_names:
        desc = _TOOL_DESCRIPTIONS.get(name)
        if name == "query" and query_mode == "single_table_fact_check":
            desc = "执行结构化单表局部事实验证；用于行数、表样例行、枚举、值存在性、单字段条件计数、字段样例和极值样例"
        if desc:
            lines.append(f"- **{name}**：{desc}")
    tool_list = "\n".join(lines)
    if query_mode == "single_table_fact_check":
        workflow_lines = [
            "- `find` 定位实体，`meta` 读取实体语义和邻接，`query` 用结构化参数验证单表局部数据事实。",
            "- ref 中 `/` 表示图边路径，`:` 表示当前路径段标签，`*` 表示名称通配，`project::ref` 限定项目。",
            "- 路径段采用 `实体名:标签` 顺序，例如 `yearmonth:table`、`Consumption:col`、`status:col`。",
            "- `find` 接受通配 ref，用来列实体和搜索；`meta` 接受单个实体 ref，用来读取该实体。",
            "- `table` 只表示数据库表；CSV/TSV 表状摘要使用 `csv_table`。",
            "- `find` 返回的第一列可直接给 `meta`；`meta` 的 Related 按标签分组，访问时用 `主节点ref/邻接名称:分组标签`。",
            "- 列实体从表实体访问，形如 `db.sqlite:db/yearmonth:table/Consumption:col`；关系列实体和知识实体也遵循同一套图路径。",
            "- 复用已有工具事实；新查询用于确认单表内的局部事实。",
        ]
        exploration_lines = [
            "- 结构入口：`find` 找 db/table/col/fk/rel/knowledge/disambig，`meta` 读目标实体。",
            "- 值验证：先用 `meta` 查看列语义、统计、样例和提示，再用 `query` 补充单表内的值事实；不同值数量用 `cardinality`，范围边界用 `extreme_values`。",
            "- 常规实体定位和字段边界核对使用 `find`/`meta`。",
        ]
    else:
        workflow_lines = [
            "- `find` 定位实体，`meta` 读取实体语义和邻接，`query` 验证原始数据事实、局部条件行数、跨表匹配基数和值分布。",
            "- ref 中 `/` 表示图边路径，`:` 表示当前路径段标签，`*` 表示名称通配，`project::ref` 限定项目。",
            "- 路径段采用 `实体名:标签` 顺序，例如 `yearmonth:table`、`Consumption:col`、`status:col`。",
            "- `find` 接受通配 ref，用来列实体和搜索；`meta` 接受单个实体 ref，用来读取该实体。",
            "- `table` 只表示数据库表；CSV/TSV 表状摘要使用 `csv_table`。",
            "- `find` 返回的第一列可直接给 `meta`；`meta` 的 Related 按标签分组，访问时用 `主节点ref/邻接名称:分组标签`。",
            "- 列实体从表实体访问，形如 `db.sqlite:db/yearmonth:table/Consumption:col`；关系列实体和知识实体也遵循同一套图路径。",
            "- 复用已有工具事实；新查询应验证新信息或排除具体疑点。",
        ]
        exploration_lines = [
            "- 结构入口：`find` 找 db/table/col/fk/rel/knowledge/disambig，`meta` 读目标实体。",
            "- 值验证：先用 `meta` 查看列语义、统计、样例和提示，再用 `query` 验证值是否存在、局部条件行数、连接前后行数和字段统计值。",
            "- 文本证据：`grep` 定位，`read` 回读上下文；JSON 层级用 `jd` 展开。",
            "- 图关系：常规实体定位和字段边界核对使用 `find`/`meta`。",
        ]
    workflow = "\n".join(workflow_lines)
    exploration = "\n".join(exploration_lines)

    return f"""## 工具使用

### 工具选择

{tool_list}

### 工作协议

{workflow}

### 调用格式

- 工具参数必须是合法 JSON；字符串值必须加双引号，例如 `{{"property": "brief"}}`，不要写 `{{"property": brief}}`。
- 使用 ref 时优先复制 `find` 返回的第一列或 `meta` 输出中的完整 ref。
- 不要自己拼 `source::`、重复项目名前缀、`:db:db`，也不要给 `overlap`、`rel`、`disambig` ref 追加 `/*` 来猜邻接。

### 探索主线

{exploration}

### 常用入口

| 调用 | 用途 |
|---|---|
| `find({{"ref":"*:db"}})` | 列出数据库入口 |
| `find({{"ref":"db.sqlite:db/*:table"}})` | 列出数据库表 |
| `find({{"ref":"db.sqlite:db/yearmonth:table/*:col"}})` | 列出表列 |
| `find({{"ref":"db.sqlite:db/*:fk"}})` | 列出结构关系 |
| `find({{"ref":"db.sqlite:db/*:rel"}})` | 列出语义关系 |
| `find({{"ref":"*:knowledge", "query":"term or rule"}})` | 搜索项目知识 |
| `find({{"ref":"*:disambig", "query":"ambiguous term"}})` | 搜索消歧信息 |
"""
