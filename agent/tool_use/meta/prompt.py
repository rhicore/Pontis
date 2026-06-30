"""Meta tool prompt — 元数据查看。"""

DESCRIPTION = "查看单个图谱实体的元数据和邻接节点。"

DETAIL = """\
参数：
- ref (必填): 一个确定的图谱实体 ref；通常来自 `find` 第一列，或由 Related 组合为 `主节点ref/邻接名称:分组标签`
- property (可选): 只读取指定字段；值必须是带引号的字符串或字符串数组，例如 `"brief"` 或 `["brief","detail"]`
- ref 必须来自 `find` 返回、已有 `meta` 返回或 Related 展示的真实对象；不要按相邻字段名或命名规律猜一个 ref。

返回：
- db/table/col 返回 schema、官方字段、样例、topk、cardinality、not_null、brief/detail 等当前已有字段
- fk/rel/overlap/disambig/knowledge 返回 brief/detail 及相关实体摘要
- Related 按邻接标签分组；访问邻接节点时使用 `主节点ref/邻接名称:分组标签`
- DB 的 table 邻接访问示例：`db.sqlite:db/yearmonth:table`
- table 的 col 邻接访问示例：`db.sqlite:db/yearmonth:table/Consumption:col`
- `linked_disambig` 不是可读取的 meta 字段；需要看消歧连接时，读取实体完整 meta 的 Related/disambig 部分，或用 `find({"ref":"*:disambig"})`。
- 不要假设 null_count、null_percentage、min_value、max_value、mean_value 一定存在；需要这些统计时用 query 计算。

优先级：
- 列实体上的 `official_column_description`、`official_value_description` 来自人工/官方标注，优先于 AI/agent 写入的 `brief/detail`。
- `brief/detail` 是摘要或推断性说明；若和 official 字段冲突，以 official 字段为准。
- official 字段标记 `unuseful`、`not useful`、`unused`、`ignore` 或同类含义的列不用于查询和分析，不读取该列取值分布。
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
