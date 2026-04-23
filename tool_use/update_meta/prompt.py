"""Update meta tool prompt — 合并写入元数据。"""

DESCRIPTION = "更新节点的 brief 和 detail 字段。"

DETAIL = """\
参数：
- ref (必填): 节点引用，支持三种格式：
  - 文件路径: "event.db"
  - path::entity: "event.db::users.table"
  - ID 引用: "ent_a3f2c801"
- fields (必填): 只允许更新 brief 和 detail：
  - brief: 简短描述（≤50字）
  - detail: 详细描述

注意：
- 合并写入：只更新 fields 中提供的字段，其他字段保持不变
- 成功后返回值已包含实际写入内容\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
