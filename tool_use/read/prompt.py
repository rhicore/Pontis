"""Read tool prompt — 读取文件内容或实体数据。"""

DESCRIPTION = "读取文件内容或逻辑实体的数据。"

DETAIL = """\
path::entity 语法：
- `knowledge.md` — 读取文本文件内容（带行号）
- `event.db::event.table` — 读取数据库表的样本行（默认 50 行）
- `event.db::event.event_name.TEXT.col` — 读取列的值列表

参数：
- file_path (必填): 文件路径或 path::entity
- offset: 起始行号（1-indexed），默认从第 1 行开始
- limit: 最大读取行数（文本文件默认 2000，数据库表默认 50）

注意：
- 数据库实体会直接查询原始 .db 文件，返回真实数据
- 文本文件超过 2000 行会截断，用 offset/limit 翻页\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
