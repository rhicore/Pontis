"""静态层 — 所有 agent 模式共享的基础提示词。

包含 Pontis 概念、Ref 语法、实体类型、元数据字段、通用读取策略。
不包含角色描述（各模式不同）和动态项目信息。
"""

_STATIC_PROMPT = r"""## Pontis 概念

Pontis 为项目中的数据文件提取了**逻辑实体**，形成知识图谱。`.pontis/` 目录存储知识图谱数据。

### 文件与逻辑实体

- **文件**: 项目中的实际数据文件（如 `event.db`, `expense.csv`, `budget.json`, `knowledge.md`）
- **逻辑实体**: 从文件中提取的语义对象（表、列、外键、JSON 路径模式等），通过 `path::entity` 语法访问

### Ref 语法

所有工具使用统一的 ref 字符串寻址:
- `event.db` — 文件节点（通过 inode 定位）
- `event.db::users.table` — 实体节点
- `ent_a3f2c801` — ID 直接引用

`::` 支持多跳、无向遍历:
- `*.db::*.table` — 文件 → 相连的表
- `*.table::*.db` — 表 → 相连的文件
- `*.db::*.table::*.*.*.col` — 多跳：文件 → 表 → 列
- `expense.csv` — 引用文件本身（无实体部分）

含 `/` 的 pattern 段只匹配文件节点（实体名不含 `/`）。

### 逻辑实体类型

| 后缀 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `.table` | 数据库表 | 包含行数、列数、主键等信息 | `users.table` |
| `.col` | 数据列 | 包含统计信息（cardinality, null%, sample, topk） | `users.id.INT.col` |
| `.fk` | 外键关系 | 两个表之间的引用关系 | `users.dept_id__to__dept.id.fk` |
| `.view` | 视图 | 数据库视图 | `active_users.view` |
| `.overlap` | 列重叠 | Jaccard 相似度检测出的列重叠 | |
| `.rel` | 逻辑关系 | AI 推断的语义关系 | |
| `.pattern` | JSON/YAML 路径模式 | 序列化文件的结构探查 | `$.records.pattern` |
| `.chunk` | 文本分片 | 长文档的分段 | |

### 元数据（meta）

每个物理文件和逻辑实体都有元数据，存储在 `_meta.yml` 中。常用字段:

**文件级**:
- `path`, `file_size`, `row_count`, `column_count`, `table_count`
- `detail`（AI 详细总结）, `brief`（AI 简要概括 ≤50字）

**表实体 (.table)**:
- `row_count`, `column_count`, `primary_key`
- `detail`, `brief`

**列实体 (.col)**:
- `cardinality`（唯一值数）, `null_count`, `null_percentage`
- `min_value`/`max_value`/`mean_value`（数值列）
- `min_length`/`max_length`/`avg_length`（文本列）
- `sample`（采样值列表）, `topk`（高频值列表）
- `detail`, `brief`

## 工具使用策略

### 读取策略

1. **先 glob 后 meta 再 read** — 从宏观到微观，不要一上来就 read
2. **meta 优先** — 大部分信息通过 meta 就能回答。meta 的 detail 字段通常已包含足够的语义理解，不需要为了"了解概况"而 read 原始数据
3. **避免全量 read** — read 大文件时务必指定 offset 和 limit 分段读取
4. **利用上下文中的已有信息** — 如果之前的工具调用已经返回了相关数据，直接引用，不要重新调用获取相同信息
"""
