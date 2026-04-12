"""Update meta tool prompt — 合并写入元数据。"""

DESCRIPTION = "合并写入节点的元数据字段，仅更新指定的字段，保留已有字段。"

DETAIL = """\
参数：
- ref (必填): 节点引用，支持三种格式：
  - 文件路径: "event.db"
  - path::entity: "event.db::users.table"
  - ID 引用: "ent_a3f2c801"
- fields (必填): 要更新的字段键值对，如 {"brief": "...", "detail": "..."}

更新行为：
- 合并写入：只更新 fields 中提供的字段，其他字段保持不变
- 自动维护内部字段（_id, _entity_name, _files, _inode）
- 如果节点不存在且为文件路径，会自动创建节点

典型用法：
- 为表添加描述: update_meta(ref="event.db::users.table", fields={"detail": "用户信息表"})
- 为列添加语义: update_meta(ref="event.db::users.status.TEXT.col", fields={"brief": "用户状态"})
- 标记处理状态: update_meta(ref="event.db", fields={"processed": true})\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
