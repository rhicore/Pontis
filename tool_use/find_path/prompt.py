"""Find_path tool prompt — 图谱路径发现。"""

DESCRIPTION = "图谱路径发现工具，查找两个实体之间的连接路径。"

DETAIL = """\
参数：
- from_ref (必填): 起始实体 ref
- to_ref (必填): 目标实体 ref
- max_depth: 最大搜索深度（跳数），默认 3

在知识图谱上执行 BFS，找出两个实体之间的最短路径。返回路径中经过的实体。

示例用途：例如，当你需要 JOIN 两个没有直接 FK/rel 关系的表时，用此工具查找中间桥接表和连接关系。\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
