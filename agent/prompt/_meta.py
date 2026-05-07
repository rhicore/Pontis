"""元数据层 — 不同类型实体的元数据字段。"""


def get_meta_prompt() -> str:
    return r"""## 实体元数据字段

### 通用字段

所有实体都可能有：

| 字段 | 说明 |
|---|---|
| `brief` | ≤50字摘要 |
| `detail` | 详细语义描述 |
| `created_at` | 创建时间 |

### 数据库文件

| 字段 | 说明 |
|---|---|
| `path` | 文件相对路径 |
| `file_size` | 文件大小（字节） |
| `table_count` | 表数量 |
| `view_count` | 视图数量 |

### 表

| 字段 | 说明 |
|---|---|
| `row_count` | 行数 |
| `column_count` | 列数 |
| `primary_key` | 主键列名 |

### 列

| 字段 | 适用 | 说明 |
|---|---|---|
| `cardinality` | 所有列 | 不同值数量 |
| `null_count` | 所有列 | 空值数量 |
| `null_percentage` | 所有列 | 空值比例 |
| `sample` | 所有列 | 采样值列表（约 20 个） |
| `topk` | 所有列 | 高频值列表（含百分比） |
| `min_value` / `max_value` / `mean_value` | 数值列 | 数值范围 |
| `min_length` / `max_length` / `avg_length` | 文本列 | 长度统计 |

### 关系实体（fk / rel / overlap）

| 字段 | 说明 |
|---|---|
| `detail` | 关系描述（含置信度、发现方式） |
| `stats` | overlap 统计：jaccard / card_overlap / coverage |
| `match_rate` | fk 数据校验匹配率 |
| `format_hint` | 格式问题提示（如前导零缺失） |

### 消歧实体

| 字段 | 说明 |
|---|---|
| `brief` | ≤50字描述歧义核心 |
| `detail` | 客观列出每个实体的具体语义差异 |

### 知识实体

| 字段 | 说明 |
|---|---|
| `brief` | ≤50字摘要 |
| `detail` | 完整内容（规则描述、SQL 模板、术语解释等） |
"""
