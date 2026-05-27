"""Update meta tool prompt — 更新实体元数据。"""

DESCRIPTION = "更新实体元数据。"

DETAIL = """\
参数：
- ref (必填): 可唯一匹配的实体 ref 或 ref 模式
- fields (必填): 要覆盖写入的字段和值

硬约束：
- 覆盖写入：只更新提供的字段，其他字段不变
- ref 使用图谱路径；调用时必须唯一匹配一个实体
- 字段名、字段值和保留策略由当前任务的上层指令决定
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
