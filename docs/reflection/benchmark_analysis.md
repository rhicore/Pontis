# BIRD Benchmark Analysis

本文档记录 Pontis 当前在 BIRD dev 上的主要观察、错误分类和论文实验指标。它只保留当前结论，不再保存逐轮长日志。

## 1. 当前定位

BIRD 对 Pontis 的价值不只是最终 EX 分数，而是把 data agent 能力拆成三层：

- **数据库理解**：是否找到正确表、列、值、join path、粒度。
- **SQL 组织**：是否正确处理 SELECT、WHERE、JOIN、GROUP、ORDER、DISTINCT、日期函数、比例分母。
- **benchmark 风格适配**：是否贴合 BIRD gold SQL 的输出字段、top-k、id/name、公式翻译等口径。

Pontis 当前更强的是第一层：帮助 agent 探索数据库结构和语义证据。最近几轮错误显示，很多失败已经不是“完全没找到 schema”，而是“证据找到了，但 final SQL synthesis 没稳定使用”。

## 2. BIRD 数据集特征

| Split | DB 数 | Query 数 | 当前用途 |
|---|---:|---:|---|
| Train | 69 | 9428 | 外部参考；当前默认 benchmark 不使用 train gold SQL 或答案 |
| Dev | 11 | 1534 | 主评测集 |

train 和 dev 数据库不重叠，所以 train 中的具体 SQL 不能直接迁移。可迁移的是：

- 通用 SQL 模式
- 审题和输出契约
- 常见错误规避规则
- value / date / ratio / top-k 等 benchmark 风格经验

当前默认运行关闭 BIRD global，不把 train 的具体 SQL、gold answer 或 dev 前序题结果写回给后续题。BIRD 适配只应通过外部可声明的 README/规则文档进入系统；通用 agent、guardrail、multi-report controller 不应包含 BIRD 专用表名、字段名或题号逻辑。

## 3. 当前运行结论

旧完整日志显示，错题中大多数都实际读取过局部 schema 语义。关键问题通常不是“完全没读知识”，而是：

- 读到 `disambig` 后把说明误当成强过滤条件。
- 读到 join/fk 后仍然选错输出列或聚合粒度。
- BIRD gold 偏好的 `id/name`、`ORDER BY LIMIT`、`DISTINCT`、ratio 分母没有被稳定执行。
- 最终 SQL synthesis 没有稳定使用已经读到的证据。

在一次暂停前的部分新日志中，31 个完成但错误的题大致可分为：

| 错误类型 | 数量 | 说明 |
|---|---:|---|
| 数据库结构/语义理解错误 | 约 9/31 | 错表、错列、错 join key、错实体粒度 |
| SQL 口径/组织错误 | 约 22/31 | 表列基本找到，但输出字段、聚合、top-k、distinct、日期片段等不符合 gold |

这说明 Pontis 的瓶颈已经部分转移到 evidence-to-SQL 决策，而不是纯 schema retrieval。

## 3.1 机制边界

近期实验把机制边界重新收窄：

- BIRD README final reviewer 只负责输出契约、SQL 风格和 README 中可声明的 benchmark 规则，不负责显式替 agent 做 schema linking。
- schema challenger / multi-report controller 是通用候选路径生成机制，适合比较表、列、值、join path、row grain、aggregation grain 等候选，但不应写入 BIRD 专用规则。
- final SQL validity check 只检查最终答案是否是可解析、只读、结构有效的 SQL，不根据题号或数据集风格强行改 SQL。
- value grounding 不能作为通用零命中拦截规则，因为某些正确 SQL 的结果本身可以是零命中或依赖空值逻辑。
- 所有可迁移的 BIRD 适配只能进入 BIRD README；README 应保持扁平、短规则、可检索、无数据库专名。

## 3.2 已合并的逐轮实验结论

旧的逐轮长文和 CSV 已合并为以下当前结论：

- `20260529` 531 错题分析说明，README 确实进入了最终审查流程；问题主要是同一 agent 自查会受既有 SQL 锚定，且部分 README 规则本身和 gold 口径冲突。
- `20260603` 108 题回归说明，去掉硬编码拦截后，主要漏洞不是某个单点规则，而是 reviewer 召回/审查不稳定、主 agent 执行 reviewer 建议不稳定，以及 guardrail 之间可能互相冲突。
- `20260604` schema-linking 修复调研说明，DB-exploration-fixable 题应优先通过更好的局部 hints、消歧和 challenger 解决；不要把具体数据库事实塞进通用 guardrail。
- `20260605` schema-linking full-pool 审计说明，多智能体 challenge 能提高某些候选路径出现概率，但 final judge 仍可能选错；因此要分开报告 schema-linking 是否找到正确路径和 execution 是否最终正确。
- explorer 相关计划已收敛到通用 `hints` 视图：写入 DB 可验证事实，例如 row grain、predicate landing、field role、join coverage、value format；不写 query/gold priors。

## 4. Schema Linking 与等结构性

如果论文目标是证明 Pontis 提升“数据库理解”，不能只看最终 EX。需要引入中间指标。

本文使用工作术语 **SQL 等结构性**：

> 如果两个 SQL 绑定到同一批数据库信息，那么它们在数据库理解层面是等结构的；它们可以在聚合、排序、子查询、输出格式等 SQL 组织方式上不同。

实际评估分三层：

| 指标 | 判定条件 | 含义 |
|---|---|---|
| Schema 等结构性 | 表集合、列集合一致 | 接近传统 schema linking |
| Grounding 等结构性 | 表、列、join pair、过滤值一致 | 数据库 grounding 基本一致 |
| Role-aware 等结构性 | 进一步要求 SELECT/WHERE/JOIN/GROUP/ORDER/聚合角色一致 | 更接近 SQL 结构计划一致 |

等结构性不是 SQL 等价性，也不能替代 EX。它用于定位失败发生在 database grounding 还是 SQL logical form。

## 5. Schema Linking 成功后的失败原因

即使表列找对，Text-to-SQL 仍可能失败：

- **输出契约错**：返回 name 而 gold 要 id，或多返回排序列。
- **聚合粒度错**：按行、实体、去重实体、月份、客户、segment 聚合的口径不同。
- **DISTINCT 口径错**：没有唯一性要求时误加 DISTINCT，或 join 后没有按目标实体去重。
- **value grounding 错**：值不存在、LIKE 太宽、exact/LIKE 选择错误。
- **join path 错**：表列出现了，但 join key 或桥表路径错误。
- **日期/文本数值错**：需要 `SUBSTR`、`STRFTIME`、cast、格式清洗。
- **top-k/极值错**：`MAX()` 和 `ORDER BY ... LIMIT` 的返回对象不同。
- **benchmark convention 错**：gold SQL 偏好某种输出字段或公式翻译。

因此论文中应同时报告 schema linking 和 EX，并分析：

```text
EX | schema linking success
EX | schema linking failure
```

如果 schema linking 成功后 EX 仍低，说明瓶颈主要在 SQL logical form、aggregation grain、output contract 和 BIRD 风格适配。

## 6. 性能指标

除准确率外，当前建议只保留精简的性能指标：

| 指标 | 含义 |
|---|---|
| Preprocess time | 每库离线预处理耗时 |
| Query latency | 每题端到端耗时 |
| LLM cost | prompt/completion token 与调用次数 |
| Tool calls | 工具调用轮数和总次数 |
| SQL execution attempts | query / repair / selection 中实际执行 SQL 的次数 |

公平比较时，每个方法应使用同一数据副本、同一模型配置、同一并发级别、同一网络/API 环境，并分开记录预处理阶段与即席查询阶段。

## 7. Baseline 对比口径

这些 baseline 基本都是 query-level independent：

- **CHESS**：同题内部有候选生成、修正、执行反馈；跨 query 不在线学习。
- **Alpha-SQL**：每题独立 MCTS 搜索和 self-consistency；跨 query 不反馈经验。
- **DeepEye-SQL**：stage pipeline 保存每题中间结果；few-shot 是离线文件，不是 dev 在线学习。
- **Pontis**：当前使用静态预处理图和可选数据集 README 规则；benchmark 中不应把 dev 前序题写回给后续题，除非明确测试 continuous mode。

因此默认公平设定是：

> 每个 query 独立推理，共享同一套静态预处理资源；同题内部允许 execution feedback、revision、selection；跨 query 不允许在线学习。

## 8. 写论文时的主张

建议把 Pontis 表述为：

> Pontis provides a graph-based database understanding layer for test-time Text-to-SQL agents. It improves schema evidence access and grounding observability, while final execution accuracy still depends on SQL synthesis and benchmark-style adaptation.

实验应避免只用 EX 证明一切，而是报告：

- final EX
- schema/table/column recall
- grounding / role-aware structural equivalence
- value grounding 成功率
- tool-call/token/time 成本
- schema linking 成功条件下的 EX

这样可以把 Pontis 的贡献和 SQL 生成器的局限拆开。
