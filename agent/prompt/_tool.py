"""工具层 — 工具选择策略与工作流引导。"""


_TOOL_DESCRIPTIONS = {
    "glob": "发现实体和关系，适合回答“有哪些”“连着什么”",
    "meta": "读取单个实体的 detail / brief 等属性",
    "query": "只在需要真实数据验证时使用",
    "search": "名称不确定时做模糊检索",
    "cypher": "处理 glob 不方便表达的复杂图查询",
    "grep": "最后手段，用于搜索普通文件内容",
    "bash": "最后手段，用于执行 shell 命令",
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

### 使用纪律

- 先发现，再理解，再验证；不要跳过 `glob/meta` 直接猜测写 SQL 或写图谱
- 已有结果直接复用，不要只为“再确认一次”重复调用
- 重要!: 不要把 `glob("*")` 当作起手式,而是要从数据源最底层的核心实体开始逐步展开访问,
    - 例如文件系统你应该优先访问根目录下的目录文件,再逐步访问与其关联的其他实体
    - 例如数据库你应该优先访问数据库本体实体,再逐步访问与其关联的表、列等实体
- 结果截断时优先翻页，不要随意换查询语义
- 用中文回答，基于事实，不补会
"""
