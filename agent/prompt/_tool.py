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
    prompt_names = set(getattr(spec, "prompts", []) or [])
    compact_database = "database_ontology" in prompt_names
    query_mode = getattr(spec, "query_mode", "")
    lines = []
    for name in tool_names:
        desc = _TOOL_DESCRIPTIONS.get(name)
        if name == "query" and compact_database:
            desc = "对当前数据库执行只读 SQL，核验原始数据事实和值分布"
        if name == "query" and query_mode == "single_table_fact_check":
            desc = "执行结构化单表局部事实验证；用于行数、表样例行、枚举、值存在性、单字段条件计数、字段样例和极值样例"
        if desc:
            lines.append(f"- **{name}**：{desc}")
    tool_list = "\n".join(lines)
    if query_mode == "single_table_fact_check":
        workflow_lines = [
            "- 从任务中的业务概念开始，用 `find` 定位 schema_landscape/topic/table_group/table，再用 `meta` 理解实体和邻接。",
            "- 已知目标字段时用 `property` 定向读取；已知目标关系类型时用 `neighbor_label` 定向读取。",
            "- `query` 用结构化参数验证单表局部事实，每次查询解决一个尚未确定的问题。",
            "- 当完成任务所需的信息已经确定，直接生成结果。",
        ]
        exploration_lines = [
            "- 结构入口：`find` 找 schema_landscape/topic/table_group/table/logical_col/col/fk/rel/disambig，`meta` 读目标实体。",
            "- 值验证：先用 `meta` 查看列语义、统计、样例和提示，再用 `query` 补充单表内的值事实；不同值数量用 `cardinality`，范围边界用 `extreme_values`。",
            "- 常规实体定位和字段边界核对使用 `find`/`meta`。",
        ]
    elif compact_database:
        workflow_lines = [
            "- 从任务中的业务概念开始，用 `find` 定位相关 table/col/关系实体，再用 `meta` 理解实体和邻接。",
            "- 已知目标字段时用 `property` 定向读取；已知目标关系类型时用 `neighbor_label` 定向读取。",
            "- `query` 验证原始数据事实、局部条件行数、跨表匹配基数和值分布，每次查询解决一个尚未确定的问题。",
            "- 当表列、关系、筛选条件、计算口径和输出粒度已经确定，直接生成结果。",
        ]
        exploration_lines = [
            "- 结构入口：`find` 找 table/col/fk/rel/disambig/knowledge，`meta` 读目标实体。",
            "- 值验证：先用 `meta` 查看列语义、统计、样例和提示，再用 `query` 验证值、行数、连接基数和字段统计。",
            "- 图关系：关系实体和普通实体采用相同的 `find`/`meta` 流程，再沿邻接实体继续探索。",
        ]
    else:
        workflow_lines = [
            "- 从任务中的业务概念开始，用 `find` 定位 schema_landscape/topic/table_group/table，再用 `meta` 理解实体和邻接。",
            "- 已知目标字段时用 `property` 定向读取；已知目标关系类型时用 `neighbor_label` 定向读取。",
            "- `query` 验证原始数据事实、局部条件行数、跨表匹配基数和值分布，每次查询解决一个尚未确定的问题。",
            "- 当表列、关系、筛选条件、计算口径和输出粒度已经确定，直接生成结果。",
        ]
        exploration_lines = [
            "- 结构入口：`find` 找 schema_landscape/topic/table_group/table/logical_col/col/fk/rel/disambig，`meta` 读目标实体。",
            "- 值验证：先用 `meta` 查看列语义、统计、样例和提示，再用 `query` 验证值是否存在、局部条件行数、连接前后行数和字段统计值。",
            "- 文本证据：`grep` 定位，`read` 回读上下文；JSON 层级用 `jd` 展开。",
            "- 图关系：常规实体定位和字段边界核对使用 `find`/`meta`。",
        ]
    workflow = "\n".join(workflow_lines)
    exploration = "\n".join(exploration_lines)

    if compact_database:
        common_calls = r"""| 调用 | 用途 |
|---|---|
| `find({"ref":"*:table|col", "query":"business concept"})` | 按问题概念检索相关表列 |
| `find({"ref":"*:fk|rel|disambig", "query":"join concept"})` | 检索连接关系和消歧义 |
| `find({"ref":"*:knowledge|disambig", "query":"term or rule"})` | 检索业务知识和消歧信息 |
| `meta({"ref":"完整实体路径", "property":["brief","detail"]})` | 定向读取所需属性 |
| `meta({"ref":"完整实体路径", "neighbor_label":"col"})` | 定向读取某类邻接实体 |"""
    else:
        common_calls = r"""| 调用 | 用途 |
|---|---|
| `find({"ref":"*:table|col", "query":"business concept"})` | 按问题概念检索相关表列 |
| `find({"ref":"*:schema_landscape|topic|table_group", "query":"business concept"})` | 在大型数据库中先定位压缩导航范围 |
| `find({"ref":"*:logical_col|col", "query":"business concept"})` | 在已命中的范围内定位物理列或逻辑列 |
| `find({"ref":"*:fk|rel|disambig", "query":"join concept"})` | 检索连接关系和消歧义 |
| `find({"ref":"*:knowledge|disambig", "query":"term or rule"})` | 检索业务知识和消歧信息 |
| `meta({"ref":"完整实体路径", "property":["brief","detail"]})` | 定向读取所需属性 |
| `meta({"ref":"完整实体路径", "neighbor_label":"col"})` | 定向读取某类邻接实体 |"""

    return f"""## 工具使用

### 工具选择

{tool_list}

### 工作协议

{workflow}

### 调用格式

- 工具参数使用合法 JSON，字符串值使用双引号，例如 `{{"property": "brief"}}`。
- ref 使用 `find` 或 Related 返回的完整 source 导航路径。

### 探索主线

{exploration}

### 问题驱动的常用调用

{common_calls}
"""
