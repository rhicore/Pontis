"""create_entity tool prompt."""

DESCRIPTION = "在指定文件下创建一个新的逻辑实体，可同时写入元数据和添加关系边。"

DETAIL = """
使用场景:
- 发现隐含的表关联关系时，创建虚拟视图实体
- 创建 AI 推断的语义关系实体
- 创建新的 JSON/YAML 路径模式实体

参数说明:
- path: 文件路径，如 'event.db'
- entity_type: 实体类型后缀，如 'view', 'rel', 'pattern'
- entity_name: 实体名称，如 'user_event_join.view'（名称已含后缀时不重复添加）
- meta: (可选) 初始元数据字典
- edges: (可选) 关系边列表，每条边包含 from, type, to 三个字段

注意:
- 创建前应先用 glob 确认实体不存在
- edges 中的路径使用完整路径格式，如 'event.db::event.table'
- 关系边会自动去重
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n{DETAIL}"
