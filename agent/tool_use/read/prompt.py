"""Read tool prompt — 按行号读取文本文件。"""

DESCRIPTION = "按行号范围读取文本文件原文。"

DETAIL = """\
参数：
- ref (必填): 文本文件图谱 ref，例如 `README.md:file:text`
- start_line: 起始行号，默认 1
- end_line: 结束行号；单次最多返回 500 行

硬约束：
- 只读取 storage 标记为 `:text` 的文件
- 通过文件节点的 open_file 句柄读取，不直接绕过 storage
- 输出会带行号，格式为 `行号 | 内容`
- 输出大小受单次行数和字符数限制\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
