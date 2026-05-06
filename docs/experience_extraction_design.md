# 经验提取与知识迁移设计

## 背景

BIRD benchmark 的 train 和 dev 使用**完全不同的数据库**（0 重叠）。这意味着传统的 RAG 方案——"从 train 集检索相似 SQL 供 dev 参考"——效果有限，因为表名、列名完全不同，无法直接复用。

核心思路：不让模型记忆具体 SQL，而是让 agent 在 train 集上**主动提炼可迁移的抽象知识**——模式、约定、术语、教训——存入 Pontis 全局知识层，供 dev 测试时检索应用。

---

## 与传统方案的对比

| 维度 | 传统 RAG (mask + retrieve) | Pontis 经验提取 |
|---|---|---|
| 存储内容 | 字段名 mask 后的 golden SQL | 抽象知识（模式、约定、术语） |
| 检索方式 | 向量相似度匹配问题 | agent 按需 glob/meta |
| 跨库迁移性 | 低（依赖 schema 相似度） | 高（知识已脱离具体 schema） |
| 知识类型 | 单一（SQL 片段） | 多维（约定、模式、术语、教训、示例） |
| 质量控制 | 人工审核 | 正确性验证 + agent 反思 |

---

## 知识类型

agent 从 train 经验中提炼的知识分为五类，对应 Pontis 已有的实体类型：

### 1. SQL 约定 (.convention)

**必须遵循或避免的 SQL 写法规则**。从成功和失败的经验中归纳。

```
实体名: no_extra_filter.convention
标签: knowledge, global
meta:
  content: "不要自作主张添加问题未要求的 WHERE 条件（如 IS NOT NULL、> 0、type='active'）。golden SQL 只按问题要求的条件过滤"
  source: "从 rtype/record_type 类列的反复犯错中总结"

实体名: no_concat_columns.convention
meta:
  content: "不要拼接列（如 forename || ' ' || surname），golden SQL 永远不拼接，SELECT 多列即可"
  source: "BIRD golden SQL 的统计特征"
```

### 2. SQL 模式 (.pattern)

**可复用的查询模式**。将具体 SQL 抽象为通用模板。

```
实体名: percentage_calculation.pattern
meta:
  content: "百分比计算模式：CAST(COUNT(条件子集) AS REAL) * 100 / COUNT(总数)。分子分母必须来自同一数据集"
  template: "CAST(COUNT(CASE WHEN <condition> THEN 1 ELSE 0 END) AS REAL) * 100 / COUNT(*)"
  source: "从多个数据库的百分比问题中提取"
```

### 3. 领域术语 (.term)

**跨数据库通用的领域概念**。

```
实体名: frpm.term
meta:
  content: "FRPM = Free or Reduced Price Meals，美国学校指标，衡量学生中符合免费/减价午餐计划的比例。常见于教育数据库"
  source: "california_schools 数据库的经验"
```

### 4. Few-shot 示例 (.example)

**非典型的、容易出错的 question-SQL 对**。只保留"模型容易犯错"的案例。

```
实体名: count_with_join_dedup.example
meta:
  question: "How many patients..."
  golden_sql: "SELECT COUNT(T1.patient_id) FROM ... JOIN ..."
  mistake: "模型容易加 DISTINCT 去重，但 1:N JOIN 后 COUNT 不去重是 golden SQL 的标准约定"
  pattern: "1:N JOIN 场景下的 COUNT 约定"
```

### 5. 教训 (.lesson)

**从错误中提炼的反面经验**。记录"什么情况下会犯什么错"。

```
实体名: time_column_sorting.lesson
meta:
  content: "TEXT 类型的时间列（如 fastestLapTime）不能直接 ORDER BY 排序。字符串排序 ≠ 数值排序。需要用对应数值列或 CAST"
  trigger: "遇到 TEXT 类型的数值/时间列需要排序时"
  source: "formula_1 数据库 fastestLapTime 列的错误"
```

---

## 架构

```
┌─────────────────────────────────────────────┐
│              Train 阶段                      │
│                                              │
│  train.json ──→ Agent ──→ SQL 生成           │
│                    │                         │
│                    ├── vs golden SQL         │
│                    │                         │
│               正确 ─┤  错误                   │
│               │    │    │                    │
│               ▼    │    ▼                    │
│          成功经验   │  失败经验                │
│          收集器    │  收集器                   │
│               │    │    │                    │
│               ▼    ▼    ▼                    │
│           经验缓冲区 (per question)           │
│                    │                         │
│                    ▼                         │
│          知识综合 Agent ──→ 提炼为知识实体     │
│                    │                         │
│                    ▼                         │
│         ~/.pontis/ (global store)            │
│           conventions, patterns,             │
│           terms, examples, lessons           │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│              Dev 阶段                        │
│                                              │
│  dev.json ──→ Agent ──→ SQL 生成             │
│                │                             │
│                ├── glob "knowledge:*"        │
│                ├── meta("xxx.convention")    │
│                └── 应用相关知识指导 SQL 生成   │
└─────────────────────────────────────────────┘
```

---

## 阶段一：经验提取（Train 运行）

### 流程

对 train.json 中的每条问题：

1. **Agent 生成 SQL**：与当前 benchmark 流程一致，agent 读取 schema、meta、关系，生成 SQL
2. **正确性验证**：与 golden SQL 的执行结果比对（exec accuracy），标记正确/错误
3. **经验收集**：
   - 正确：记录 question、evidence、生成的 SQL、涉及的表/列/关系
   - 错误：额外记录 golden SQL、错误类型（列选错、JOIN 错误、多加条件、格式错误等）
4. **批量反思**：每处理 N 条问题（如一个数据库的所有问题），触发一次反思

### 反思机制

反思是知识综合的核心。不是每道题都提炼知识，而是**积累一批经验后统一反思**。

反思触发条件（满足任一）：
- 一个数据库的所有问题处理完毕
- 累积了 M 条同类错误
- 发现了重复出现的模式

反思过程：
1. agent 回顾积累的成功/失败经验
2. 识别**可迁移的**模式（不依赖具体表名列名）
3. 归纳为上述五种知识类型
4. 写入全局知识层

### 正确性判断

```python
def verify(predicted_sql: str, golden_sql: str, db_path: str) -> bool:
    """执行比对，返回是否正确"""
    pred_result = execute(predicted_sql, db_path)
    gold_result = execute(golden_sql, db_path)
    return result_match(pred_result, gold_result)  # BIRD 标准比对
```

---

## 阶段二：知识存储

### 存储位置

全局知识层 `~/.pontis/`，标签含 `knowledge` + `global`。

```python
store = Store(os.path.expanduser("~"), project="bird")
store.create_node(
    "no_extra_filter.convention",
    namespaces=["knowledge", "global"],
    meta={
        "brief": "不要添加问题未要求的 WHERE 条件",
        "detail": "...",
        "content": "...",
        "source": "从 rtype/record_type 反复犯错中总结",
    }
)
```

### 知识去重

同一类知识可能被不同数据库的经验反复触发。写入前检查：
- 同名实体已存在 → 合并/更新 meta
- 不同名但语义重叠 → 保留更全面的版本

### 知识分级

不是所有经验都值得存储。agent 需要判断：

| 信号 | 值得存储 | 不值得存储 |
|---|---|---|
| 出现频率 | 同类错误出现 3+ 次 | 只出现 1 次 |
| 可迁移性 | 不依赖具体 schema | 完全依赖特定列名 |
| 通用价值 | 所有数据库适用 | 仅特定领域适用 |

---

## 阶段三：知识应用（Dev 测试）

### 两种交付模式

基于 `重构计划.md` 中的交付机制分析，采用混合模式：

**1. 强制注入（convention / lesson）**

SQL 约定和教训是**规则性知识**，agent 必须遵循。直接注入 prompt。

```python
# 启动 dev 测试时
conventions = store.find_nodes("knowledge:*.convention")
lessons = store.find_nodes("knowledge:*.lesson")
# 注入到 _benchmark.py 或 _sql.py 的 prompt 中
```

**2. 按需调用（pattern / term / example）**

SQL 模式、术语、示例是**参考性知识**，agent 按需查询。

agent 的 _sql.py 中已有 glob/meta 工具链，知识实体和数据实体用同样的方式访问：
- `glob "knowledge:*.convention"` → 发现所有约定
- `meta("no_extra_filter.convention")` → 读取详情
- `glob "knowledge:*.pattern"` → 发现所有 SQL 模式

### 知识检索时机

agent 在生成 SQL 的过程中，以下时机自然触发知识检索：

1. **开始新问题时**：查看是否有相关的 lesson（防止重蹈覆辙）
2. **需要写 JOIN 时**：查看是否有相关的 pattern
3. **遇到不熟悉的术语时**：查看是否有相关的 term
4. **生成 SQL 后自检时**：对照 convention 检查

不需要额外的检索逻辑——agent 已经有 glob/meta 工具，知识实体和数据实体用同样的方式访问。

---

## 实现计划

### Step 1：Train 运行框架

- 包装现有 benchmark 流程，增加正确性验证环节
- 按数据库分组运行，每组完成后触发反思
- 输出：经验缓冲区（JSON 格式，含 question/evidence/predicted_sql/golden_sql/correct/error_type）

### Step 2：反思 Agent

- 专门的 prompt，输入一批经验，输出结构化知识实体
- 按知识类型分别处理（约定、模式、术语、示例、教训）
- 输出：`create_node` 调用序列

### Step 3：全局知识写入

- Store 以 `~/.pontis/` 为根，`project="bird"`
- 反思 agent 的输出写入全局知识层
- 去重逻辑

### Step 4：知识注入（convention + lesson）

- 从全局知识层读取 convention 和 lesson
- 注入 `_benchmark.py` 或新增 `_knowledge.py` prompt 模块
- dev 测试时自动加载

### Step 5：验证

- 先跑无知识的 dev baseline
- 再跑注入知识的 dev，对比 exec accuracy
- A/B 验证各类知识的贡献

---

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 错误知识污染 | 只从验证过的经验中提炼；lesson 标记为"参考"而非"规则" |
| 知识过度泛化 | 要求 agent 标注 source 和适用范围；过度泛化的知识在 dev 中不生效 |
| 知识量爆炸 | 反思时过滤低频、低迁移性经验；定期清理 |
| 反思 agent 本身出错 | 人工抽检首批知识；建立质量基线 |
