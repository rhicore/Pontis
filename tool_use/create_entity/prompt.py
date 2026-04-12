"""Create entity tool prompt — 创建实体节点。"""

DESCRIPTION = "在知识图谱中创建新的实体节点（如视图、关系、模式等）。"

DETAIL = """\
参数：
- ref (必填): 实体引用，格式为 path::entity_name，如 "event.db::user_event_join.view"
- meta: 初始元数据（可选），如 {"row_count": 100, "brief": "..."}
- edges: 关系边列表（可选），每条边为 {"from": "...", "type": "...", "to": "..."}

创建行为：
- 自动生成 ent_id 并写入 .pontis/nodes/{ent_id}/_meta.yml
- 自动添加 contains 边（从文件节点指向实体）
- 自动维护 _entity_name 和 _files 内部字段
- 用户提供的 edges 会一并添加

注意：
- ref 必须含 :: ，纯文件路径不能通过此工具创建（由 extractor 的 skeleton 模块处理）
- 如果实体已存在会报错，如需更新请使用 update_meta\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
