"""jd tool prompt — JSON 内部结构探查。"""

DESCRIPTION = "浏览 JSON 文件内部结构，按 JSON VFS 路径展示下一层内容。"

DETAIL = """\
参数：
- path (必填): JSON VFS 路径，如 `data.json`、`data.json#/records`、`data.json#/records/0`
- limit: 当前层最多展示多少个子项，默认 50
- offset: 分页起点，默认 0
- max_value_chars: 标量值预览最大长度，默认 120

行为：
- 通过 JSON 文件节点的 open_file 句柄读取，不直接拼物理路径
- 只展示当前路径的直接子项，不递归展开整个 JSON
- dict key 中的空格或斜杠会在 path 中 URL encode
- 大数组用 limit/offset 翻页；遇到 list[dict] 会显示 schema keys 摘要\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
