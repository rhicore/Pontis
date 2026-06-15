# Join列搜索模块设计文档

## 模块概述

当前 Join 列检测采用两阶段设计：

1. **db_column_overlap.py** - 硬性规则检测（Jaccard相似度）
2. **db_column_rel.py** - LLM打分软性筛选

## 文件输出格式

### 1. Overlap 文件（硬性规则）

**文件名**: `[表名].[列名]__to__[表名].[列名].overlap`

**位置**: `.pontis/[db_name].db/`

**Meta 结构**:
```yaml
relation_type: column_overlap
from_table: users
from_column: id
from_type: INT
to_table: orders
to_column: user_id
to_type: INT
match_type: STRONG_MATCH  # 或 WEAK_MATCH
reason: "Context shared | Col tokens shared: ['id']"
stats:
  card_overlap: 150        # 重叠基数
  jaccard: 0.75           # Jaccard相似度
  cardinality_A: 200      # 列A基数
  cardinality_B: 150      # 列B基数
  coverage_A_in_B: 0.75   # A在B中的覆盖率
  coverage_B_in_A: 1.0    # B在A中的覆盖率
created_at: "2026-01-..."
```

### 2. Rel 文件（LLM打分）

**文件名**: `[表名].[列名]__to__[表名].[列名].rel`

**位置**: `.pontis/[db_name].db/`

**Meta 结构**:
```yaml
relation_type: column_relation
from_table: users
from_column: id
from_type: INT
to_table: orders
to_column: user_id
to_type: INT
confidence: 0.85          # LLM评分 (0.0-1.0)
can_join: true
reason: "Semantic match: user_id references users.id"
heuristic_score: 0.82     # 启发式分数（辅助）
overlap_stats:            # 引用overlap的统计
  card_overlap: 150
  jaccard: 0.75
  ...
created_at: "2026-01-..."
```

## 检测流程

### Phase 1: Overlap 检测（db_column_overlap.py）

```
┌──────────────────────────────────────────────────────────────┐
│  Step 1: Context Check (表级过滤)                              │
│  ├── 两张表必须有共同的非停用词                                 │
│  └── 快速剪枝：无共享Context的表对直接跳过                       │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  Step 2: Value Overlap Check (值级硬门槛)                       │
│  ├── 计算两列值的交集（使用集合操作）                            │
│  └── 无值重叠 → 直接放弃该列对                                  │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  Step 3: Column Name Check (列名分类)                           │
│  ├── STRONG_MATCH: 列名有共同token                              │
│  └── WEAK_MATCH: 无共同列名token，仅值+上下文匹配                 │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  Step 4: 详细指标计算                                           │
│  ├── Jaccard相似度: card_overlap / |A ∪ B|                    │
│  └── 行级覆盖率: coverage_A_in_B, coverage_B_in_A               │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  Step 5: 排序与Top-K                                           │
│  ├── 排序规则: (match_type优先级, -card_overlap)               │
│  └── 保留每对表Top-3 Join列组合                                 │
└──────────────────────────────────────────────────────────────┘
```

### Phase 2: Relation 评分（db_column_rel.py）

**输入**: 所有 `.overlap` 文件

**评分策略**（扣分制）:
```
初始分数: 1.0
├── 语义一票否决: 直接扣至 0.0
├── 数据类型风险:
│   ├── 整数/自增ID: 扣 0.4
│   ├── 枚举/布尔值: 扣 0.8
│   └── UUID/复杂编码: 不扣分
└── 统计显著性:
    ├── 覆盖率 < 10%: 扣 0.6
    ├── 覆盖率 < 30%: 扣 0.3
    └── Jaccard < 0.1: 扣 0.3
```

**LLM Prompt 设计**:
- 角色：严格的数据库审计员
- 输入：列信息、统计指标、启发式分数
- 输出：JSON格式 `{can_join, confidence, reason}`

**阈值过滤**: 仅保留 `confidence >= 0.5` 的边

## 停用词定义

```python
STRUCTURE_STOPWORDS = {'id', 'ids', 'key', 'pk', 'fk', 'code', 'uuid', 'guid', 'index'}
COMMON_NOUNS = {'name', 'title', 'description', 'date', 'time', 'value', 'type', 'status'}
NLP_STOPWORDS = {'of', 'the', 'and', 'in', 'on', 'at', 'to', 'from', 'a', 'an'}
```

## 使用方式

### 独立执行

```bash
# 生成overlap文件
python -m extractor.db_column_overlap ./my_data

# 生成rel文件（需要配置LLM）
python -m extractor.db_column_rel ./my_data
```

### 集成到主流程

```python
from extractor import extract
extract("./my_data")  # 自动执行所有阶段
```

### 配置LLM（pontis.yml）

```yaml
llm_enabled: true
llm_provider: "https://api.deepseek.com"
llm_model: "deepseek-chat"
llm_api_key: "your-api-key"
```

## 依赖关系

```
db_column_stats_approx ──► db_column_overlap ──► db_column_rel
          │                                          │
          └──────── 提供近似 cardinality ──────────────┘
                     提供 overlap stats
```

- `db_column_overlap` 依赖 `db_column_stats_approx`（需要近似 cardinality 数据）
- `db_column_rel` 依赖 `db_column_overlap`（读取.overlap文件）
