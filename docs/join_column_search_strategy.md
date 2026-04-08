# MyTableRAG 两阶段流水线中 Join列搜索策略调研文档

## 1. 系统架构概述

MyTableRAG 采用**两阶段流水线**架构：

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         阶段一：表关系图建立 (Offline)                         │
├─────────────────────────────────────────────────────────────────────────────┤
│  输入: tables.json                                                          │
│       ↓                                                                     │
│  JCS (Joinable Column Search)                                               │
│       ↓                                                                     │
│  LLM 过滤/打分                                                              │
│       ↓                                                                     │
│  输出: table_graph_ids.json (表关系图)                                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         阶段二：图检索 (Online)                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  输入: Query + 语义索引 + 表关系图                                           │
│       ↓                                                                     │
│  语义粗召回 (FAISS) → Top-K 表作为种子                                        │
│       ↓                                                                     │
│  Personalized PageRank 在图上扩散                                            │
│       ↓                                                                     │
│  输出: 重排序后的 Top-K 表                                                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Join列搜索策略详解 (JCS)

### 2.1 核心文件

| 文件 | 功能 |
|------|------|
| `data_process/JCS.py` | 基于启发式规则的Join列搜索 |
| `data_process/LLM_JCS.py` | LLM辅助的Join边过滤与置信度打分 |
| `data_process/golden_edge_extra.py` | 从SQL中提取Golden Edge（历史已知Join关系） |

---

### 2.2 JCS 算法流程 (JCS.py)

#### Step 1: 数据预处理 - 上下文计算

```python
def analyze_tokens(text):
    """
    分词分析器，返回两套Token：
    1. raw_tokens: 仅分词+小写，保留所有词 (用于列名比对)
    2. strict_tokens: 剔除停用词 (用于Context计算)
    """
```

**停用词分类**：

| 类别 | 示例 |
|------|------|
| **结构停用词** | `id`, `ids`, `key`, `pk`, `fk`, `code`, `uuid`, `guid`, `index` |
| **通用名词** | `name`, `title`, `description`, `date`, `time`, `value`, `type`, `status` |
| **NLP停用词** | `of`, `the`, `and`, `in`, `on`, `at`, `to`, `from` |

**Context计算**：
```
表级Context = 标题有效词(strict_tokens) + 所有列名有效词(strict_tokens)
```

#### Step 2: 漏斗筛选模型

```
┌──────────────────────────────────────────────────────────────┐
│  Step 0: Context Check (表级过滤)                              │
│  ├── 两张表必须有共同的非停用词                                 │
│  ├── 例：User表和Order表共享"user"等词 → 通过                  │
│  └── 例：User表和Weather表无共享词 → 直接过滤                   │
└──────────────────────────────────────────────────────────────┘
                              ↓ 通过
┌──────────────────────────────────────────────────────────────┐
│  Step 1: Value Overlap Check (值级硬门槛)                       │
│  ├── 计算两列值的交集                                          │
│  ├── 使用集合快速判断：`if set1.isdisjoint(set2): continue`     │
│  └── 无值重叠 → 直接放弃该列对                                  │
└──────────────────────────────────────────────────────────────┘
                              ↓ 通过
┌──────────────────────────────────────────────────────────────┐
│  Step 2: Column Name Check (列名分类)                           │
│  ├── STRONG_MATCH: 列名有共同token (raw_tokens交集)             │
│  │   └── 例：`user_id` ↔ `id` 共享 `id`                        │
│  └── WEAK_MATCH: 无共同列名token，仅值+上下文匹配                │
│      └── 例：`customer_id` ↔ `uid` 无共同token                 │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  Step 3: 详细指标计算                                          │
│  ├── 行级覆盖率: rows_overlap_A_in_B, rows_overlap_B_in_A       │
│  ├── 基数(Unique值): card_A, card_B, card_overlap               │
│  └── Jaccard相似度: card_overlap / |A ∪ B|                     │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  Step 4: 排序与Top-K                                           │
│  ├── 排序规则: (match_type优先级, card_overlap)                │
│  └── 保留每对表Top-3 Join列组合                                 │
└──────────────────────────────────────────────────────────────┘
```

#### Step 3: 并行处理

```python
NUM_WORKERS = max(1, cpu_count() - 2)  # 使用多进程并行

# 生成所有表对组合
for t1, t2 in combinations(table_ids, 2):
    if not clean_tables[t1]['context'].isdisjoint(clean_tables[t2]['context']):
        tasks.append((t1, clean_tables[t1], t2, clean_tables[t2]))

# 多进程并行计算
with Pool(NUM_WORKERS) as pool:
    results = pool.imap_unordered(compute_matches, tasks, chunksize=500)
```

---

### 2.3 LLM 辅助过滤 (LLM_JCS.py)

#### 设计动机

启发式JCS会产生大量**假阳性**边（如自增ID巧合重叠），需要LLM进行语义验证。

#### 评分策略（扣分制）

```
初始分数: 1.0
├── 语义一票否决: 直接扣至 0.0 (如用户表与日志类型表)
├── 数据类型风险:
│   ├── 整数/自增ID: 扣 0.4 (易巧合重叠)
│   ├── 枚举/布尔值: 扣 0.8 (重叠无意义)
│   └── UUID/复杂编码: 不扣分 (重叠是强证据)
└── 统计显著性:
    ├── 绝对数量过少
    ├── 相对占比过低
    └── 覆盖率 < 10%
    └── 扣 0.3 - 0.6

最终标准：
├── 0.0 - 0.2: 绝大多数情况（语义不匹配）
├── 0.3 - 0.6: 语义相关但证据不足（如自增ID重叠）
└── 0.9 - 1.0: 语义完美 + UUID/极高覆盖率
```

#### LLM Prompt 设计

```
角色: 极其严苛的数据库审计员

核心原则:
1. 语义一票否决 - 业务逻辑不匹配直接拒绝
2. 数据类型风险 - 整数/自增ID易巧合
3. 统计显著性 - 检查重叠数量和覆盖率

输出格式:
{
    "can_join": true/false,
    "confidence": 0.0-1.0,
    "reason": "..."
}
```

#### 阈值过滤

```python
if conf >= 0.5:  # 仅保留置信度 >= 0.5 的边
    pair_candidates_results[pair_key].append({...})
```

---

### 2.4 Golden Edge 提取 (golden_edge_extra.py)

从SQL查询中提取**已知的正确Join关系**：

```python
# 输入: queries.json (含SQL字段)
# 输出: golden_edges.json

{
    "0": {
        "42": [{"cols": ["custid", "custid"]}],
        "308": [{"cols": ["custid", "custid"]}]
    }
}
```

**解析逻辑**：
1. 提取SQL中的表别名映射
2. 正则匹配 `alias1.col1 = alias2.col2`
3. 将别名解析回表ID
4. 构建双向边

---

## 3. 图检索策略 (PageRank)

### 3.1 核心文件

| 文件 | 功能 |
|------|------|
| `pagerank_retrieve.py` | 基础PageRank检索 |
| `history_sql_pagerank_retrieve.py` | 混合Golden Edge的PageRank |

### 3.2 基础PageRank流程

```
输入: Query向量
    ↓
┌─────────────────────────────────────┐
│ A. 语义粗召回 (FAISS)               │
│    - 召回Top-K_COARSE (200)张表      │
│    - 获取相似度分数                  │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ B. 构造个性化向量 (Personalization) │
│    - 对粗召回分数做softmax归一化      │
│    - Temperature = 0.05              │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ C. 执行PageRank迭代                 │
│    r = α * M * r + (1-α) * p        │
│    - α (阻尼系数) = 0.3              │
│    - 迭代50次或收敛                   │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ D. 提取Top-K_FINAL结果              │
│    - 按分数排序取Top-K              │
│    - 过滤分数 < 1e-9 的节点           │
└─────────────────────────────────────┘
    ↓
输出: Top-K表ID列表
```

### 3.3 混合Golden Edge的PageRank

**核心思想**：结合不确定的JCS边和确定的Golden Edge

```
边权重计算:
┌─────────────────────────────────────────────────────────┐
│ 1. JCS边: 读取table_graph_ids_LLM.json中的置信度         │
│ 2. Golden Edge: 置信度强制设为1.0                        │
│                                                          │
│ 归一化: Softmax(边权重, temperature=0.1)                │
│ - 温度越小，Golden Edge权重越集中                        │
└─────────────────────────────────────────────────────────┘

阻尼系数 α 的意义:
┌─────────────────────────────────────────────────────────┐
│ α 越大 → 越相信图中的边信息（结构）                       │
│ α 越小 → 越相信初始语义信息（Personalization Vector）    │
│ 实验配置: α = 0.6                                        │
└─────────────────────────────────────────────────────────┘
```

---

## 4. 关键参数总结

| 参数 | 值 | 说明 |
|------|-----|------|
| **JCS阶段** |||
| Context停用词 | 3类 | 结构词 + 通用名词 + NLP停用词 |
| 值交集判断 | set交集 | 使用Python集合快速判断 |
| Top-K边保留 | 3 | 每对表保留最多3条Join列组合 |
| LLM置信度阈值 | 0.5 | 低于此值的边被过滤 |
| **PageRank阶段** |||
| 粗召回数量 (K_COARSE) | 200 | 语义召回候选表数 |
| 最终输出数量 (K_FINAL) | 20 | 返回给用户的Top-K |
| 阻尼系数 (α) | 0.6 | 0.3-0.6范围实验 |
| Query温度 | 0.05 | 语义分数softmax温度 |
| Edge温度 | 0.1 | 边权重softmax温度 |
| 最大迭代次数 | 50 | PageRank收敛条件 |

---

## 5. 设计亮点

### 5.1 多层级过滤策略

```
Context过滤 → Value过滤 → LLM过滤
(快速剪枝)   (硬约束)    (语义验证)
```

### 5.2 双Token策略

| Token类型 | 用途 |
|-----------|------|
| raw_tokens | 列名匹配（允许`id`↔`id`） |
| strict_tokens | Context计算（剔除停用词） |

### 5.3 混合图构建

结合两种边：
- **JCS边**: 算法发现，置信度不确定
- **Golden Edge**: SQL历史提取，置信度=1.0

### 5.4 可解释性

每对Join列保留详细统计：
```json
{
    "match_type": "STRONG_MATCH",
    "reason": "Context shared: [user] | Col tokens shared: [id]",
    "stats": {
        "card_overlap": 150,
        "jaccard": 0.75,
        "rows_overlap_A_in_B": 145
    }
}
```

---

## 6. 文件依赖关系

```
tables.json ──┬──► JCS.py ──────────────────────┬──► table_graph_ids_LLM.json ───┐
              │    (启发式Join列搜索)            │    (LLM过滤后)                  │
              │           ↓                     │                                 │
              │    LLM_JCS.py (过滤+打分)       │                                 │
              │                                 │                                 │
              └──► golden_edge_extra.py ────────┴──► golden_edges.json ──────────┤
                   (从SQL提取已知边)                                               │
                                                                                   ▼
queries.json ────► queries_index_create.py ─────► queries_index.faiss ───────► pagerank_retrieve.py
                                                                                  (图检索)
                                                                                       ↓
                                                                               retrieval_output.json

tables.json ────► tables_index_create.py ───────► tables_index.faiss ────────────────┘
```

---

## 7. 总结

MyTableRAG的Join列搜索策略采用**"漏斗式多级过滤"**设计：

1. **粗粒度过滤**: Context重叠快速剪枝无关表对
2. **中粒度过滤**: 值交集确保数据层面可Join
3. **细粒度过滤**: LLM语义验证排除假阳性
4. **图检索增强**: PageRank利用图结构扩散语义

这种设计在保证召回率的同时，有效控制计算成本，适合大规模数据库场景的表检索任务。
