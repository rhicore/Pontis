"""Add Edge tool prompt — 为已有实体添加关系边。"""

DESCRIPTION = "为知识图谱中已有的两个节点添加无向边。"

DETAIL = """\
用于在已存在的实体之间建立关系。创建实体时的边通过 create_entity 自动添加，此工具用于给已有实体补充关系。

参数：
- edges (必填): 边列表，每条边包含：
  - a (必填): 节点 ref
  - b (必填): 节点 ref

注意：
- 确保两个节点都存在（先用 glob 或 meta 确认）
- 重复边会自动跳过
- 归属边由系统自动管理，不要手动添加\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
