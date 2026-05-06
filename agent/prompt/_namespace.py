"""命名空间层 — 标签系统与作用域。

实体通过 _labels 字段标记类型。标签支持层级（/ 分隔）。
"""

_NAMESPACE_PROMPT = r"""## 标签体系

### 层级标签

实体通过 `_labels` 字段标记类型。标签支持 `/` 层级：

| 标签 | 含义 | 典型实体 |
|---|---|---|
| `file/db` | 数据库文件 | `formula_1.db` |
| `file/csv` | CSV 文件 | `data.csv` |
| `file/json` | JSON 文件 | `config.json` |
| `file/text` | 文本文件 | `README.md` |
| `dir` | 目录节点 | `data` |
| `table` | 数据库表 | `drivers` |
| `view` | 数据库视图 | `active_users` |
| `col/INT` | 整数列 | `driverId` |
| `col/TEXT` | 文本列 | `driverRef` |
| `col/REAL` | 浮点列 | `lapTime` |
| `fk` | 外键关系 | `orders.user_id__to__users.id.fk` |
| `rel` | 逻辑关系 | `schools.County__to__satscores.cname.rel` |
| `overlap` | 列值重叠 | `...` |
| `disambig` | 语义消歧 | `points.disambig` |
| `knowledge/convention` | SQL 约定 | `no_concat.convention` |
| `knowledge/pattern` | SQL 模式 | `ranking_top_n.pattern` |
| `knowledge/term` | 领域术语 | `points.term` |
| `knowledge/lesson` | 经验教训 | `join_cardinality.lesson` |
| `knowledge/example` | Few-shot 示例 | `top_n_ranking.example` |

### 标签过滤

用 glob 按标签过滤（Cypher 风格，标签在模式之后）：
- `glob "*:table"` → 找所有表
- `glob "*:col"` → 找所有列（匹配 col/INT, col/TEXT 等）
- `glob "*:file"` → 找所有文件（匹配 file/db, file/csv 等）
- `glob "*:dir"` → 找所有目录
- `glob "*:file|knowledge"` → OR：file 或 knowledge 标签

### 作用域

| 作用域 | 标签前缀 | 说明 |
|---|---|---|
| 项目 | file, dir, table, col, fk, rel, overlap, disambig | 数据实体，仅当前项目可见 |
| 全局 | knowledge/* | 知识实体，所有项目共享 |

知识实体存储在全局知识库中，不依赖特定表名列名，可迁移到任何项目。
"""


def get_namespace_prompt() -> str:
    return _NAMESPACE_PROMPT
