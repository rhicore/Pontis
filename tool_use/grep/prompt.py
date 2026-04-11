"""Grep tool prompt — 文件内容搜索。"""

DESCRIPTION = "在文件内容中搜索匹配的文本模式（基于 ripgrep）。"

DETAIL = """\
参数：
- pattern (必填): 正则表达式（ripgrep 语法）
- path: 搜索范围（文件或目录），默认搜索整个项目
- output_mode: "content" 显示匹配行 | "files_with_matches" 仅文件名（默认）| "count" 计数
- glob: 文件名过滤，如 "*.py", "*.{ts,tsx}"
- ignore_case: 忽略大小写
- head_limit: 限制输出条数，默认 250，最大 1000
- offset: 起始位置（从 0 开始），默认 0

content 模式返回格式: `文件路径:行号:匹配内容`

结果截断时会提示总数和当前范围，使用 offset 参数翻页。

注意：
- grep 搜索的是原始物理文件的内容，不是元数据
- 如果要在实体元数据中搜索，用 meta 工具配合 property 参数
- 默认输出模式是 files_with_matches，需要看具体内容时设为 content\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
