"""Delete tool prompt."""


def get_description() -> str:
    return """删除知识图谱中的节点。

删除规则：
- 删除节点时，自动删除其所有关联边（保证无悬挂边）
- 如果相连的邻居是派生实体（如 .rel、.fk、.overlap、.disambig），会级联删除
- 例如：删除数据库文件节点 → 级联删除所有子表/列 → 级联删除关联的 .rel/.fk 节点

注意：此操作不可逆，删除前请确认。"""
