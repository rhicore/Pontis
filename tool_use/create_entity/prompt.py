"""Create entity tool prompt — 创建实体节点。"""

DESCRIPTION = "在知识图谱中创建新的实体节点。"

DETAIL = """\
参数：
- ref (必填): 实体名称（即完整标识），如 'no_null_check.convention'
- meta: 初始元数据（可选），建议包含 brief 和 detail
- edges: 关系边列表（可选），每条边为 {"a": "...", "b": "..."}

## 数据关系实体（需在 .db 下）

### .rel 实体（关系）
ref 格式: [db]::[table1].[col1]__to__[table2].[col2].rel
edges: 连接到涉及的列实体

### .disambig 实体（语义消歧）
ref 格式: [db]::[column_name].disambig 或 [db]::[term].disambig
meta: brief 描述歧义，detail 列出语义差异
edges: 连接到涉及的列或表实体

## 知识实体（全局或项目级）

后缀决定类型，entity_name 即完整标识：
- .convention: 命名约定，如 'no_concat.convention'
- .pattern: 查询模式，如 'count_with_group_by.pattern'
- .term: 术语解释，如 'fiscal_year.term'
- .lesson: 经验教训，如 'avoid_null_in_join.lesson'
- .example: 示例，如 'case_when_example.example'

知识实体的内容存放在 meta.detail 字段中：
- brief: ≤50字摘要
- detail: 完整内容（多行自由文本）

注意：
- 如果实体已存在会报错，如需更新请使用 update_meta
- 创建前先 glob 检查是否已存在同名实体
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
