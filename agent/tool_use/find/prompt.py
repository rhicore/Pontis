"""find tool prompt — unified graph entity discovery."""

DESCRIPTION = "发现图谱实体。ref 必填；无 query 时列出 ref 匹配实体，有 query 时在实体 name/brief/detail 中排序匹配结果。"

DETAIL = """\
参数：
- ref (必填): 图谱 ref 模式，用于限定实体范围，例如 `*:file`, `*:col`, `context/db/results.db/*:table`
- query: 自然语言检索词，例如 `track number`, `creatinine normal range`
- offset: 起始位置（默认 0）
- limit: 每页最大条数

## 能力边界

| 调用形态 | 含义 |
|---|---|
| 只有 ref | 按 ref 模式列出匹配实体 |
| ref + query | 在 ref 范围内按实体摘要和名称做语义/关键词排序 |

find 的检索对象是图谱实体和实体元数据，包括 file/table/col/pattern/chunk/knowledge。CSV/JSON/DB 的原始行级过滤、聚合和 join 属于 `query`；JSON 层级浏览属于 `jd`；文本正文定位属于 `grep/read`。

## 常用 ref

| 调用 | 用途 |
|---|---|
| `find({"ref":"*:file"})` | 列出当前项目文件实体 |
| `find({"ref":"*:file:db"})` | 列出 DB 文件实体 |
| `find({"ref":"*:file:csv"})` | 列出 CSV/TSV 文件实体 |
| `find({"ref":"context/csv/data.csv/*:col"})` | 列出某个 CSV 的列实体 |
| `find({"ref":"context/json/data.json/*:pattern"})` | 列出某个 JSON 的 pattern 实体 |
| `find({"ref":"context/knowledge.md/*:chunk"})` | 列出某个文本文件的 chunk 实体 |
| `find({"ref":"*:col", "query":"track number"})` | 在列实体中匹配 track number 相关摘要 |
| `find({"ref":"*", "query":"Riverside related school"})` | 在当前图谱实体摘要中匹配相关概念 |

返回结果中的第一列是可继续传给 `meta/read/grep/jd/query` 的 copyable ref。
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
