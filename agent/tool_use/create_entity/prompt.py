"""Create entity tool prompt — 创建实体节点。"""

DESCRIPTION = "在知识图谱中创建新的实体节点。"

DETAIL = """\
参数：
- ref (必填): 实体引用
  - name 是实体名称（精确名称，不允许通配符）
  - :tag 为实体打标签（可多个），类型通过标签区分
- meta: 初始元数据（可选），常用字段为 brief 和 detail
- edges: 关系边列表（可选），每条边为 {"a": "...", "b": "..."}

## 硬约束
- ref 使用精确名称；通配匹配属于 find
- 来源关系通过 edges 表达
- 派生实体的 meta 写实体自身语义；来源文件、路径、父节点由边表达
- edges 两端 ref 使用图谱路径；可来自 `find` 第一列，或由 Related 组合为 `主节点ref/邻接名称:分组标签`
- 多实体决策知识统一创建为 `name:hint`，并通过 edges 连接所有相关实体
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
