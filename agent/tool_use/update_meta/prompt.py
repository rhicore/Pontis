"""Update meta tool prompt — 更新节点元数据。"""

DESCRIPTION = "更新节点的 brief 和 detail 字段。"

DETAIL = """\
参数：
- ref (必填): 实体名称或 glob 模式（必须唯一匹配）
- fields (必填): 合并写入的字段，允许 brief 和 detail

注意：
- 合并写入：只更新提供的字段，其他字段不变
- 必须先 meta 读取过该节点才能更新（防止覆盖并发修改）
- `ref` 可以直接使用 glob/meta 结果中显示的精确引用
- 如果结果里已经带项目名前缀、路径或标签，就原样使用，不要自行改写
- 只有在当前工作区明确无歧义时，才使用裸名
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
