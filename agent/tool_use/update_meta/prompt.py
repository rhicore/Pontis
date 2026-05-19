"""Update meta tool prompt — 更新节点元数据。"""

DESCRIPTION = "更新节点的 brief 和 detail 字段。"

DETAIL = """\
参数：
- ref (必填): 实体 ref 或 ref 模式（必须唯一匹配）
- fields (必填): 合并写入的字段，允许 brief 和 detail

硬约束：
- 合并写入：只更新提供的字段，其他字段不变
- 必须先 meta 读取过该节点才能更新（防止覆盖并发修改）
- `ref` 直接复制 find/meta 结果中显示的精确引用
- fields 只接收 brief/detail
- 当前工作区唯一实体可使用裸名；多源或同名实体使用 path-style ref
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
