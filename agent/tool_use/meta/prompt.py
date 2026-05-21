"""Meta tool prompt — 元数据查看。"""

DESCRIPTION = "查看单个图谱实体的元数据和邻接节点。"

DETAIL = """\
参数：
- ref (必填): 一个确定的图谱实体 ref；通常来自 `find` 第一列，或由主节点 ref 追加 Related 中的邻接名称得到
- property: 指定要查看的属性，支持字符串或列表
  - 字符串: property="sample"
  - 列表: property=["cardinality", "sample", "topk"]
  - 不指定 property 时返回默认概况：brief/detail、核心统计和紧凑邻接
- all: 设为 true 显示全部字段

返回：
- db/table/col 返回 schema、统计、样例、topk、null_count 等可用字段
- fk/rel/overlap/disambig/knowledge 返回 brief/detail 及相关实体摘要
- Related 中的邻接节点只显示邻接名称；访问邻接节点时使用 `主节点ref/邻接名称`
- 列实体从表实体直接访问，例如 `table_ref/column:col`
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
