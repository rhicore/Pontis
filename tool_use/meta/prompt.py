"""Meta tool prompt — 元数据查看。"""

DESCRIPTION = "查看文件或逻辑实体的元数据，是了解数据概况的首选工具。"

DETAIL = """\
path::entity 语法：
- `event.db` — 数据库文件元信息（表数量、文件大小等）
- `event.db::event.table` — 表的元信息（行数、列数、主键、语义描述）
- `event.db::event.status.TEXT.col` — 列的统计信息（cardinality, null%, sample, topk）
- `expense.csv` — CSV 文件元信息（行数、列数、编码、分隔符）
- `json/budget.json` — JSON 文件元信息（结构类型、顶层 key）
- `json/budget.json::$.records.pattern` — 序列化结构的模式信息

参数：
- path (必填): 文件或 path::entity 路径
- all: 设为 true 显示所有元数据字段（包括 sample、topk 等）
- property: 查看特定属性，如 "sample", "topk", "cardinality", "detail", "brief"

注意：直接调用 meta(path="X") 不指定 property 时，会显示该类型的默认字段集（通常包含 brief 和 detail）。大部分情况下一次调用即可获取概况，无需分别查询 detail 和 brief。

典型用法：
- "这个表有多少行多少列？" → meta(path="event.db::event.table")
- "这列的取值分布？" → meta(path="event.db::event.status.TEXT.col", property="topk")
- "这列的采样值？" → meta(path="event.db::event.status.TEXT.col", property="sample")
- "AI 总结？" → meta(path="event.db::event.table", property="detail")
- "JSON 文件的结构？" → meta(path="json/budget.json")\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
