"""Glob tool prompt — 图谱节点检索。"""

DESCRIPTION = "基于名称模式的快速检索工具，用于定位文件并沿边遍历实体。"

DETAIL = """\
参数：
- path_pattern (必填): glob 模式
- offset: 起始位置（默认 0）
- limit: 每页最大条数（默认 100，最大 500）

语法：
- 两阶段工作：第一段 pattern 匹配物理文件，后续段通过 :: 沿边遍历实体
- :: 第一段必须是文件级 pattern（如 *.db），不能直接用实体后缀（如 *.table）
- :: 是双向边遍历操作符，每跳自动去重

返回：每行 `[ref] | [简要信息]`，截断时提示 offset 翻页。

使用示例：
- `*` — 所有文件
- `*.db` — 所有 .db 文件
- `*.sqlite` — 所有 .sqlite 文件
- `**/*.csv` — 递归查找 CSV
- `*.db::*.table` — 所有数据库的所有表
- `*.db::*.table::*.*.*.col` — 多跳：文件 → 表 → 列
- `*.db::*user*.col` — 模糊匹配
- `*.db::*.fk` — 所有外键关系s
- `*.db::*.rel` — 所有语义关系
- `*.db::*.overlap` — 所有列值重叠关系
- `col::*` — 某列的所有关联实体








\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
