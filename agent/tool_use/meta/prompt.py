"""Meta tool prompt — 元数据查看。"""

DESCRIPTION = "查看单个图谱实体的元数据和邻接节点。"

DETAIL = """\
参数：
- ref (必填): 从 source 节点开始的完整图导航 ref，通常直接复制自 `find` 或 Related
- property (可选): 只读取指定字段；值必须是带引号的字符串或字符串数组，例如 `"brief"` 或 `["brief","detail"]`
- neighbor_label (可选): 只读取指定类型的相邻实体，例如 `"col"`、`"fk"`、`"disambig"`
- offset / limit: 对 neighbor_label 的结果分页；默认 20 条，单页最多 100 条
- ref 使用 `find`、已有 `meta` 或 Related 展示的完整导航路径，并且唯一对应一个实体。

返回：
- 返回实体当前已有的公开元数据；宽邻接默认只展示前 20 条
- Related 按邻接标签分组，每个条目同样返回从 source 开始的完整导航 ref
- 邻接较多时使用 `neighbor_label`、`offset` 和 `limit` 定向分页
- 表访问示例：`meta({"ref":"california_schools.sqlite:db/schools:table"})`
- 同名列通过 source 路径消歧：`meta({"ref":"california_schools.sqlite:db/schools:table/CDSCode:col"})`
- 额外的数据统计通过 `query` 计算。
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
