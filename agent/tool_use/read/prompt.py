"""Read tool prompt — 按行号读取文本文件。"""

DESCRIPTION = "按行号范围读取文本文件原文。"

DETAIL = """\
参数：
- path (必填): 文本文件路径或唯一文件名
- start_line: 起始行号，默认 1
- end_line: 结束行号；单次最多返回 500 行

行为：
- 只读取 storage 标记为 `:text` 的文件
- 通过文件节点的 open_file 句柄读取，不直接绕过 storage
- 输出会带行号，格式为 `行号 | 内容`
- 用于根据 grep/search/chunk summary 找到线索后回查原文\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
