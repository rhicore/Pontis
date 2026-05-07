"""SQL 生成规范 — 所有需要生成 SQL 的模式共享。

强制流程 + 常见陷阱，适用于任何 text-to-sql 任务。
"""

_SQL_RULES = r"""## 数据库 SQL 准则

在针对关系型数据库，回答用户问题，生成 SQL，前有以下几个目标，最终目标是完整理解用户所属的数据库结构。

### 目标一：发现 schema

用 glob 发现数据库中的表和列：
- `glob "*.db"` — 列出所有数据库文件
- `glob "*:table"` — 列出所有表
- `glob "*:col"` — 列出所有列
- `glob "(*.db)--(*:table)"` — 遍历：数据库文件的表
- `glob "(*.db)--(*:table)--(*:col)"` — 遍历：表的列

### 目标二：确认列语义

meta 帮助理解表列语义。meta 的 detail 会显示实体丰富的总结信息。
当不确定选哪个列时（尤其名称相似的列），用 `meta` 查看 detail、sample、topk 来确认。

也可以用 `meta("drivers")` 快速查看表的所有列。

### 目标三：理解表间关系（必须在写 JOIN 之前完成）

用 glob 查看关系实体，确认 JOIN 路径和关联条件：
- `glob "*:fk"` — 外键关系（精确的 JOIN 条件）
- `glob "*:rel"` — 语义关系（AI 推断，辅助参考）
- `glob "*:overlap"` — 列值重叠
- `glob "*:disambig"` — 语义消歧

FK 实体名编码了精确的 JOIN 条件，例如 `orders.user_id->users.id`（labels: `["fk"]`）表示 `orders.user_id = users.id`。
rel 实体是 AI 推断的辅助线索，可靠性低于 FK，使用前应验证数据。
overlap 实体仅表示列值有重叠，不能直接作为 JOIN 条件使用。

如果两个表之间没有直接关系，可以用 glob 多跳遍历查找桥接路径，如 `glob "(*:table)--(*:fk)--(*:table)--(*:fk)--(目标表:table)"`。

### 输出前检查

1. 确保你读取了输出 SQL 中涉及的任何实体的元数据
2. 模糊性的排除：有 disambig 消歧实体时必须读取确认
3. 确保 JOIN 关系的正确性、连贯性、合理性
4. 生成的 SQL 能在以上信息约束下满足用户提问

### 写 SQL 前的规划步骤（必须执行）

1. **需要哪些表**：根据问题和 evidence 确定
2. **表之间如何 JOIN**：基于 fk/rel 确认 JOIN 条件
3. **WHERE 条件是什么**：只包含问题明确要求的过滤条件

**常见错误**：
- 看到外键就把所有关联表都 JOIN 进来 → 只 JOIN 问题实际需要的表
- 问题没有要求排序就加 ORDER BY → 不要加
- 问题没有要求 DISTINCT 就加 DISTINCT → 不要加
- 问题没有要求过滤空值就加 IS NOT NULL → 不要加

### 严格遵循 evidence

如果问题附带了 evidence（证据提示），evidence 中的信息具有高优先级：
- evidence 给出的列名映射 → 优先使用
- evidence 给出的计算公式 → **严格翻译为 SQL**，不要简化或改写
- evidence 给出的条件值 → 直接使用，不要猜测其他值

### 关于 query 工具

query 是辅助验证工具，不是探索工具。探索数据库结构应使用 glob 和 meta。
过度专注在 query 工具的 SQL 生成上会让你损失全局的语义理解！

### 标准执行流程

1. **glob** — 发现文件、表、列、关系等结构
2. **meta** — 读取实体的 detail 字段理解语义
3. **确认关系** — 读取 fk/rel/overlap 确认 JOIN 路径
4. **检查消歧** — 读取 disambig 排除语义歧义
5. **query** — 仅在前四步完成后，用于辅助验证

### 消歧实体的特殊重要性

disambig 实体标记了名称相近但含义不同的表或列。

**忽略消歧实体是错选表/列的首要原因**。输出 SQL 前必须确认已读取并理解所有相关消歧实体。

### 常见错误清单

| 错误模式 | 正确做法 |
|---|---|
| 猜测 JOIN 条件 | 先 glob *:fk / *:rel 确认 |
| 猜测列名 | 先 meta 确认列语义 |
| 多加/少加列 | 只 SELECT 问题要求的字段 |
| 猜测 WHERE 值 | 先 query SELECT DISTINCT 确认实际值 |
| 用 query 反复试错 | 前三步做完后一次写对 |
| 自作主张加过滤条件 | 只加问题明确要求的 WHERE 条件 |
| 不遵循 evidence 公式 | 严格按 evidence 给出的公式翻译 SQL |
| 代码列和名称列混淆 | meta 查看 topk 和 detail 区分 |
"""


def get_sql_rules() -> str:
    return _SQL_RULES
