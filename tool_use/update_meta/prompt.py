"""update_meta tool prompt."""

DESCRIPTION = "更新文件或逻辑实体的元数据字段（合并写入，不覆盖未指定的字段）。"

DETAIL = """
使用场景:
- 为缺少 brief/detail 的实体补充 AI 总结
- 更新实体的描述性元数据

参数说明:
- path: 文件路径
- entity_path: (可选) 实体路径，如 'users.table'。不提供则为文件级 meta
- fields: 要更新的字段键值对字典，如 {"brief": "用户表", "detail": "存储所有注册用户的基本信息..."}

当前常用字段:
- brief: 简要概括（≤50字）
- detail: 详细描述

注意:
- 只更新 fields 中指定的字段，不影响其他已有字段
- 更新前应先用 meta 读取当前值，确认需要更新
- brief 应精炼，detail 应完整但不过长
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n{DETAIL}"
