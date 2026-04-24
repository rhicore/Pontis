"""Create entity tool prompt — 创建实体节点。"""

DESCRIPTION = "在知识图谱中创建新的实体节点（.rel 或 .disambig）。"

DETAIL = """\
参数：
- ref (必填): 实体引用，允许创建 .rel 和 .disambig 实体，格式为 *.db::**.rel 或 *.db::**.disambig
- meta: 初始元数据（可选），如 {"brief": "...", "detail": "..."}
- edges: 关系边列表（可选），每条边为 {"a": "...", "b": "..."}

创建行为：
- 自动生成 ent_id 并写入 .pontis/nodes/{ent_id}/_meta.yml
- 自动添加归属边（文件 ↔ 实体）
- 用户提供的 edges 会一并添加

## .rel 实体（关系）

ref 格式: [db]::[table1].[col1]__rel__[table2].[col2].rel
edges: 每条边连接"列"和"rel 实体"（不是列到列）：
  {"a": "db::table1.col1.TYPE.col", "b": "db::table1.col1__rel__table2.col2.rel"}
  {"a": "db::table2.col2.TYPE.col", "b": "db::table1.col1__rel__table2.col2.rel"}

## .disambig 实体（语义消歧）

当数据库中存在名称相同或相近但语义不同的实体时，创建消歧实体。

### 列级消歧
ref: [db]::[column_name].disambig
meta:
  level: column
  brief: ≤50字描述歧义
  detail: 列出每个表中该列的具体语义差异
edges: 连接到每个涉及歧义的列实体
  {"a": "[db]::[table].[col].TYPE.col", "b": "[db]::[column_name].disambig"}

### 表级消歧
ref: [db]::[term].disambig
meta:
  level: table
  brief: ≤50字描述歧义
  detail: 列出每个表的具体语义差异
edges: 连接到每个涉及的表实体
  {"a": "[db]::[table].table", "b": "[db]::[term].disambig"}

注意：
- 如果实体已存在会报错，如需更新请使用 update_meta
- 创建前先 glob 检查是否已存在同名实体
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
