"""Grep tool prompt — 文件内容搜索。"""

DESCRIPTION = "在文本文件内容中搜索匹配的文本模式。"

DETAIL = """\
参数：
- pattern (必填): 正则表达式（ripgrep 语法）
- ref (必填): 搜索范围，文本文件或目录的图谱 ref；需要全项目文本时用 `*:file:text`
- output_mode: "content" 显示匹配行 | "files_with_matches" 仅文件名（默认）| "count" 计数
- file_pattern: 文件名过滤，如 "*.py", "*.{ts,tsx}"
- ignore_case: 忽略大小写
- head_limit: 限制输出条数，默认 250，最大 1000
- offset: 起始位置（从 0 开始），默认 0

content 模式返回格式: `文件路径:行号:匹配内容`

硬约束：
- 通过 storage 的 open_file 句柄读取 `:text` 文件
- 搜索范围是文本文件实体；二进制或未标记 text 的文件不进入结果
- 结果截断时会提示总数和当前范围，使用 offset 参数翻页。\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
