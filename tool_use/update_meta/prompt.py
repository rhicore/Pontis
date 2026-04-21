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

更新行为：
- 合并写入：只更新 fields 中提供的字段，其他字段保持不变
- 尝试修改其他字段会被拒绝

典型用法：
- update_meta(ref="event.db::users.table", fields={"brief": "用户表", "detail": "存储所有注册用户的基本信息"})
- update_meta(ref="event.db::users.status.TEXT.col", fields={"brief": "用户状态"})\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
