"""Update meta tool prompt — 更新节点元数据。"""

DESCRIPTION = "更新节点的 brief 和 detail 字段。"

DETAIL = """\
参数：
- ref (必填): 可唯一匹配的实体 ref 或 ref 模式
- fields (必填): 合并写入的字段，允许 brief 和 detail

硬约束：
- 合并写入：只更新提供的字段，其他字段不变
- 更新前先用 meta 读取该节点
- `ref` 使用图谱路径；可来自 `find` 第一列，或由主节点 ref 追加 `meta` Related 邻接名称得到
- fields 只接收 brief/detail
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
