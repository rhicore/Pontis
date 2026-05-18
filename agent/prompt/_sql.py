"""SQL 生成规范 — 所有需要生成 SQL 的模式共享。

强制流程 + 常见陷阱，适用于任何 text-to-sql 任务。
"""

_SQL_RULES = r"""## 数据库 SQL 准则

在关系型数据库任务中，目标是先吃透 schema 与关系，再输出一条正确 SQL。

### 工作流

1. **发现 schema**
   - 用定向 `glob` 找数据库、表、列
   - 不要用 `glob("*")` 做全图枚举

2. **确认列语义**
   - 用 `meta` 看 detail / sample / topk
   - 名称相似的列，先确认再选

3. **确认 JOIN 路径**
   - 写 JOIN 前先读 `fk` / `rel` / `overlap` / `disambig`
   - `fk` 可靠性最高；`rel` 只作辅助；`overlap` 不能直接当 JOIN 条件

4. **必要时再 query**
   - `query` 用于验证值域、分布、空值和结果，不是探索 schema 的首选工具

### 关系理解

写 JOIN 前必须通过图谱确认连接关系：
- **fk** — 外键关系，可靠性最高，实体名直接编码 JOIN 条件（如 `orders.user_id->users.id` 表示 `orders.user_id = users.id`）
- **rel** — AI 推断的语义关系，仅作辅助，使用前需验证
- **overlap** — 列值重叠，不能直接作为 JOIN 条件
- **disambig** — 语义消歧，输出 SQL 前必须读取并理解所有相关消歧实体

使用 glob 按 URN 语法查询这些关系；如需限定到具体数据库，使用多跳路径。

### 输出前检查

1. 确保你读取了输出 SQL 中涉及的任何实体的元数据
2. 模糊性的排除：有 disambig 消歧实体时必须读取确认
3. 确保 JOIN 关系的正确性、连贯性、合理性
4. 生成的 SQL 能在以上信息约束下满足用户提问

### 写 SQL 前先规划

1. **需要哪些表**：根据问题和 evidence 确定
2. **表之间如何 JOIN**：基于 fk/rel 确认 JOIN 条件
3. **WHERE 条件是什么**：只包含问题明确要求的过滤条件

**常见错误**：
- 看到外键就把所有关联表都 JOIN 进来 → 只 JOIN 问题实际需要的表
- 问题没有要求排序就加 ORDER BY → 不要加
- 问题没有要求 DISTINCT 就加 DISTINCT → 不要加
- 问题没有要求过滤空值就加 IS NOT NULL → 不要加

### 关于 query 工具

query 是辅助验证工具，不是探索工具。探索数据库结构应使用 glob 和 meta。

### 消歧实体

disambig 实体标记了名称相近但含义不同的表或列。

**忽略消歧实体是错选表/列的首要原因**。输出 SQL 前必须确认已读取并理解所有相关消歧实体。
"""


def get_sql_rules() -> str:
    return _SQL_RULES
