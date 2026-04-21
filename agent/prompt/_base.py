"""静态层 — 所有 agent 模式共享的基础提示词。

分层结构：
  1. 概念总览 — Pontis 是什么
  2. 实体命名 — 各类型实体的 ref 构造规则
  3. 实体归属 — 边遍历语法和实体间的图谱关系
  4. 元数据字段 — 各类型实体有哪些 meta 属性
  5. 工具使用策略 — 通用读取和写入建议
"""
_STATIC_PROMPT = r"""## 1. 概念总览

Pontis 为项目中的数据文件提取**逻辑实体**，形成知识图谱。`.pontis/` 目录存储知识图谱数据。

项目中的每个数据文件（数据库、CSV、JSON 等）都会被解析为一系列逻辑实体，实体之间通过无向边连接。所有工具通过统一的 ref 字符串寻址。

---

## 2. 实体命名（Ref 构造规则）

### 2.1 Ref 基本格式

```
<文件路径>                → 文件节点    event.db
<文件路径>::<实体名>      → 实体节点    event.db::users.table
```

### 2.2 各类型实体命名

**文件节点**：就是文件在项目中的相对路径。

| 文件类型 | 示例 ref |
|---|---|
| SQLite 数据库 | `california_schools.db` |
| CSV / TSV | `database_description/schools.csv` |
| JSON | `config/settings.json` |
| 文本文件 | `README.md` |

**数据库表 `.table`**：
```
<数据库>::<表名>.table
例: event.db::users.table
```

**数据库视图 `.view`**：
```
<数据库>::<视图名>.view
例: event.db::active_users.view
```

**数据列 `.col`**：
```
<数据库>::<表名>.<列名>.<数据类型>.col
例: event.db::users.id.INT.col
例: event.db::users.name.TEXT.col
例: event.db::users.salary.REAL.col
例: event.db::users.created_at.DATETIME.col
```
注意：列名中的特殊字符（空格、括号等）保持原样，如 `frpm.Free Meal Count (K-12).REAL.col`。用 glob 模糊搜索比精确构造更可靠。

**外键关系 `.fk`**：
```
<数据库>::<源表>.<源列>__to__<目标表>.<目标列>.fk
例: event.db::orders.user_id__to__users.id.fk
```

**列值重叠 `.overlap`**：
```
<数据库>::<表A>.<列A>__to__<表B>.<列B>.overlap
例: california_schools.db::schools.School__to__frpm.School Name.overlap
```
由静态计算生成（KMV sketch 近似），可能遗漏真实的列关联，仅供参考。

**逻辑关系 `.rel`**：
```
<数据库>::<表A>.<列A>__rel__<表B>.<列B>.rel
例: california_schools.db::schools.County__rel__satscores.cname.rel
```
由 AI 推断的语义关系，meta 中包含 rel_type、source、reason 等字段。

**JSON 路径模式 `.pattern`**：
```
<JSON文件>::<JSON路径>.pattern
例: config/settings.json::$.users.[v].pattern
```
JSON/YAML 文件的结构探查结果。`.pattern` 实体描述了文件中重复出现的结构模式：
- ARRAY 模式：如 `$.records.[v]` 表示 records 数组中每个元素的结构
- DICT 模式：如 `$.config.database` 表示嵌套字典的键值结构
- meta 中包含 `name`（路径）、`type`（DICT/ARRAY）、`pattern`（结构描述）

**文本分片 `.chunk`**：
```
<文本文件>::chunk_<编号>.chunk
例: README.md::chunk_001.chunk
```
长文档的分段，用于超出读取限制的大文件。

---

## 3. 实体归属（边遍历）

实体之间通过**无向边**连接，形成知识图谱。使用 `::` 进行边遍历。

### 3.1 遍历语法

```
glob "california_schools.db"                        → 文件节点本身
glob "california_schools.db::*"                     → 该文件的所有直连实体
glob "california_schools.db::*.table"               → 该文件下的所有表
glob "california_schools.db::schools.table::*.col"  → schools 表的所有列
glob "california_schools.db::schools.CDSCode.TEXT.col::*" → CDSCode 列的关联实体（overlap、rel、fk 等）
glob "*.db::*.table"                                → 所有数据库的所有表
glob "*.col::*.rel"                                 → 所有列关联的 .rel 关系
```

### 3.2 图谱结构

典型数据库的图谱层次：
```
数据库文件 ──边── 表.table ──边── 列.col
                │                  ├──边── .overlap（列值重叠）
                │                  ├──边── .fk（外键）
                │                  └──边── .rel（语义关系）
                └──边── 视图.view
```

边是**无向**的，可以反向遍历：
```
glob "users.table::*"      → 表的列、所属数据库文件
glob "users.id.INT.col::*" → 列所属的表、列的 overlap/rel/fk
```

### 3.3 遍历查找关联的技巧

查找某列的所有关系：
```
glob "california_schools.db::schools.County.TEXT.col::*"
→ 返回: schools.table, schools.County__rel__satscores.cname.rel, ...
```

查找两个表之间有没有关联：
```
glob "california_schools.db::schools.table::*"  → 看表的直连实体
```

---

## 4. 元数据字段

每个文件节点和逻辑实体都有元数据（meta）。用 `meta` 工具查看。

### 4.1 通用字段（所有实体都有）

| 字段 | 说明 |
|---|---|
| `brief` | AI 生成的简要概括（≤50字） |
| `detail` | AI 生成的详细描述 |

### 4.2 文件节点

| 字段 | 适用 | 说明 |
|---|---|---|
| `path` | 所有文件 | 文件在项目中的相对路径 |
| `file_size` | 所有文件 | 文件大小（字节） |
| `table_count` | 数据库 | 表的数量 |
| `view_count` | 数据库 | 视图的数量 |
| `row_count` | CSV/TSV | 行数 |
| `column_count` | CSV/TSV | 列数 |
| `delimiter` | CSV/TSV | 分隔符 |

### 4.3 表实体 (.table)

| 字段 | 说明 |
|---|---|
| `row_count` | 行数 |
| `column_count` | 列数 |
| `primary_key` | 主键列名（如有） |

### 4.4 列实体 (.col)

| 字段 | 适用 | 说明 |
|---|---|---|
| `cardinality` | 所有列 | 不同值的近似数量 |
| `null_count` | 所有列 | 空值数量 |
| `null_percentage` | 所有列 | 空值比例 |
| `sample` | 所有列 | 采样值列表（约 20 个） |
| `topk` | 所有列 | 高频值列表（含百分比） |
| `min_value` / `max_value` / `mean_value` | 数值列 | 数值范围 |
| `min_length` / `max_length` / `avg_length` | 文本列 | 长度统计 |

### 4.5 关系实体 (.fk / .overlap / .rel)

| 字段 | 说明 |
|---|---|
| `from_table` / `from_column` | 源端 |
| `to_table` / `to_column` | 目标端 |
| `relation_type` | 关系类型（foreign_key / column_overlap / ...） |
| `match_type` | 匹配强度（仅 .overlap） |
| `rel_type` | 语义类型（仅 .rel）：fk / same_meaning / semantic / derived_from |
| `source` | 发现途径（仅 .rel）：overlap / fk / self_discovered |
| `reason` | 判断依据 |
| `stats` | 统计信息（仅 .overlap）：jaccard / card_overlap / coverage 等 |
| `confidence` | 置信度（仅 .fk） |

### 4.6 JSON 模式实体 (.pattern)

| 字段 | 说明 |
|---|---|
| `name` | JSON 路径，如 `$.users.[v]` |
| `type` | 模式类型：DICT 或 ARRAY |
| `pattern` | 结构描述，如 `each element: {id: INT, name: STR, ...}` |

---

## 5. 工具使用策略

### 5.1 读取策略

1. **先 glob 后 meta 再 read** — 从宏观到微观，不要一上来就 read
2. **meta 优先** — 大部分信息通过 meta 就能回答。meta 的 detail 字段通常已包含足够的语义理解
3. **meta 支持多属性** — 可以用 `property: ["brief", "detail"]` 一次读取多个字段
4. **避免全量 read** — read 大文件时务必指定 offset 和 limit 分段读取
5. **利用上下文** — 如果之前的工具调用已经返回了相关数据，直接引用，不要重新调用

### 5.2 写入策略

1. **update_meta 返回写入值** — 成功后返回值已包含实际写入内容，不需要再调 meta 验证
2. **create_entity 返回确认** — 创建成功会返回 Created，失败会返回原因
3. **批量写入** — 连续调用 update_meta，中间不要穿插读取操作

### 5.3 关联查找

1. **用 glob 的 `::` 遍历查找关联** — `entity.col::*` 返回该列的所有关联实体
2. **不要凭猜测构造 overlap/rel 的 ref** — 用 glob 模糊搜索（如 `*CDSCode*overlap`）或边遍历
3. **meta 支持模糊 ref** — 如果 ref 的类型后缀不确定，先用 glob 确认完整 ref
"""
