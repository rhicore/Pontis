"""Add Edge tool prompt — 为已有实体添加关系边。"""

DESCRIPTION = "为知识图谱中已有的两个节点添加无向边。"

DETAIL = """\
参数：
- edges (必填): 边列表，每条边包含：
  - a (必填): 节点 ref
  - b (必填): 节点 ref

硬约束：
- 两个端点使用已存在节点
- 端点 ref 使用图谱路径；可来自 `find` 第一列，或由主节点 ref 追加 `meta` Related 邻接名称得到
- 已存在的边由工具保持幂等
- 归属边由系统自动管理\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
