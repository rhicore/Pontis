"""find tool prompt — unified graph entity discovery."""

DESCRIPTION = "发现图谱实体。ref 必填；无 query 时列出 ref 匹配实体，有 query 时在实体 name/brief/detail 中排序匹配结果。"

DETAIL = """\
参数：
- ref (必填): 图谱 ref 模式，用于限定实体范围，例如 `*:db`, `db.sqlite:db/*:table`, `db.sqlite:db/yearmonth:table/*:col`, `*:knowledge`
- query: 自然语言检索词，例如 `track number`, `creatinine normal range`
- offset: 起始位置
- limit: 每页最大条数

## 能力边界

| 调用形态 | 含义 |
|---|---|
| 只有 ref | 按 ref 模式列出匹配实体 |
| ref + query | 在 ref 范围内按实体 name / brief / detail 排序 |

find 检索图谱实体和实体元数据，包括 db/table/col/fk/rel/overlap/disambig/knowledge。原始行级过滤、聚合和 join 属于 `query`；JSON 层级浏览属于 `jd`；文本正文定位属于 `grep/read`。

## ref 匹配语法

ref 是图谱路径表达式。未带 project 路由时在当前打开的全部 Project 中匹配；`project::ref` 将匹配范围限定到指定 Project。

| 语法 | 含义 |
|---|---|
| `pattern` | 按实体 name 匹配；支持 `*` 通配 |
| `entity_name:label` | 匹配指定 name 和标签，例如 `yearmonth:table` |
| `entity_name:label1:label2` | 匹配指定 name 和多个标签 |
| `entity_name:label1|label2` | 匹配指定 name 和任一标签 |
| `seg1/seg2/seg3` | 沿图边逐段匹配相邻实体 |
| `db.sqlite:db/*:table` | 匹配某个数据库下的表 |
| `db.sqlite:db/yearmonth:table/*:col` | 匹配 `yearmonth` 表下的列 |

`ref + query` 先用 ref 限定候选实体，再按 query 在候选实体的 name / brief / detail 中排序。
返回结果第一列与输入 ref 使用同一套路径逻辑，可直接给 `meta`。列路径直接扩展表 ref：`db.sqlite:db/yearmonth:table/Consumption:col`。
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
