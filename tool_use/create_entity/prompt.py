"""Create entity tool prompt — 创建实体节点。"""

DESCRIPTION = "在知识图谱中创建新的逻辑关系实体（.rel）。"

DETAIL = """\
参数：
- ref (必填): 实体引用，目前只允许创建 .rel 实体，格式为 *.db::**.rel
  例如: "event.db::users.id__rel__orders.user_id.rel"
- meta: 初始元数据（可选），如 {"brief": "用户与订单的关系"}
- edges: 关系边列表（可选），每条边为 {"a": "...", "b": "...", "required_by": [...]}
  - a/b 为节点 ref，required_by 指定依赖方（值为 ["a"] 或 ["b"]）

创建行为：
- 自动生成 ent_id 并写入 .pontis/nodes/{ent_id}/_meta.yml
- 自动添加归属边（文件 ↔ 实体，实体标记为依赖方）
- 用户提供的 edges 会一并添加

注意：
- 目前只允许创建 .rel（逻辑关系）实体
- ref 必须含 :: 且以 .rel 结尾
- 如果实体已存在会报错，如需更新请使用 update_meta
- **edges 正确用法**: 每条边应该连接"列"和"rel 实体"，而不是连接两个列。这样通过 glob 的 `col::*` 遍历才能找到这个 rel。
  正确: {"a": "db::table1.col1.TYPE.col", "b": "db::table1.col1__rel__table2.col2.rel"}
  正确: {"a": "db::table2.col2.TYPE.col", "b": "db::table1.col1__rel__table2.col2.rel"}
  错误: {"a": "db::table1.col1.TYPE.col", "b": "db::table2.col2.TYPE.col"}  ← 这样列无法遍历到 rel
  通常需要两条边，把两端的列分别连到 rel 实体上\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
