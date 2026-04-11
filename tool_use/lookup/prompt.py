"""Lookup tool prompt — 值检索工具。"""

DESCRIPTION = "值检索工具，在数据列中按条件筛选值。"

DETAIL = """\
参数：
- file_pattern (必填): 文件 glob 模式，如 "**/*.db", "**/*.csv"
- type (必填): 数据类型: INT, TEXT, REAL, BOOL, STR, FLOAT
- predicate (必填): 筛选表达式，格式为 `类型 运算符 值`
  - 数值: "INT > 100", "REAL < 50.0"
  - 文本: 'STR = "active"', 'TEXT contains "user"'
  - 布尔: "BOOL = true"
- output_mode: "distinct_count" (默认) 列出匹配值 | "file_count" 仅显示匹配数
- offset: 起始位置（从 0 开始），默认 0
- limit: 每页最大条数，默认 50，最大 200

示例：
- 查找大于 100 的整数列值: lookup(file_pattern="**/*.db", type="INT", predicate="INT > 100")
- 查找包含 "active" 的文本列: lookup(file_pattern="**/*.db", type="TEXT", predicate='TEXT = "active"')

结果截断时会提示总数和当前范围，使用 offset 参数翻页。

注意：lookup 操作的是元数据中的 sample/topk 值，不是全表扫描，适用于快速值探测。\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
