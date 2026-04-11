"""Glob tool prompt — 物理文件与实体检索。"""

DESCRIPTION = "基于名称模式的快速检索工具，用于定位文件及其关联的逻辑实体。"

DETAIL = """\
path::entity 语法：
- 左侧是文件的 glob 模式，右侧是逻辑实体的 glob 模式，用 :: 分隔
- `**/*.db` — 查找所有数据库文件
- `**/*.db::*.table` — 查找所有数据库中的表
- `**/*.db::*user*.col` — 查找名字包含 user 的列
- `event.db::event.*.col` — 查找 event.db 中 event 表的所有列
- `**/*.csv` — 查找所有 CSV 文件
- `*` — 列出项目顶层内容

参数：
- path_pattern (必填): glob 模式
- offset: 起始位置（从 0 开始），默认 0
- limit: 每页最大条数，默认 100，最大 500

返回格式: 每行一个匹配，`[路径] | [简要信息]`。无匹配返回 "No objects found"。

结果截断时会提示总数和当前范围，使用 offset 参数翻页。

使用建议：
- 这是探索项目的第一步，先用 glob 了解结构
- glob 返回简要信息，需要详细信息时用 meta 工具\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
