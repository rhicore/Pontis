"""Meta tool prompt — 元数据查看。"""

DESCRIPTION = "查看单个图谱实体的元数据和邻接节点。"

DETAIL = """\
参数：
- ref (必填): 一个确定的图谱实体 ref；通常来自 `find` 第一列，或由 Related 组合为 `主节点ref/邻接名称:分组标签`

返回：
- db/table/col 返回 schema、官方字段、统计、样例、topk、null_count 等可用字段
- fk/rel/overlap/disambig/knowledge 返回 brief/detail 及相关实体摘要
- Related 按邻接标签分组；访问邻接节点时使用 `主节点ref/邻接名称:分组标签`
- DB 的 table 邻接访问示例：`db.sqlite:db/yearmonth:table`
- table 的 col 邻接访问示例：`db.sqlite:db/yearmonth:table/Consumption:col`

优先级：
- 列实体上的 `official_column_description`、`official_value_description` 来自人工/官方标注，优先于 AI/agent 写入的 `brief/detail`。
- `brief/detail` 是摘要或推断性说明；若和 official 字段冲突，以 official 字段为准，并用 `query` 验证值域。
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
