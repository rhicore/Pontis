# Column Overlap Extractor Design

本文档是 `db_column_overlap` 的权威设计说明。实现发生变化时，必须同步更新本文档；不得让代码、Spider 运行配置和本文档描述三者出现不同口径。

最后同步日期：2026-07-13。

## 目标

`db_column_overlap` 的目标不是仅凭列名猜测外键，而是发现两个物理列或逻辑列域之间是否存在可用于 Join 的值域重叠，并将可审计证据写入 `overlap` 图谱实体。

Pontis 当前关心的主要值域指标是：

```text
overlap_min(A, B) = |distinct(A) intersection distinct(B)| / min(|distinct(A)|, |distinct(B)|)
```

该指标也称 containment-style overlap。它与 Jaccard 不同：一个小维表的 key 被大事实表完全包含时，`overlap_min` 可以等于 1，而 Jaccard 可能很低。

## 设计约束

1. Extractor 通过 `Workspace` 和 storage 层暴露的数据库句柄访问图谱及数据库，不自行维护数据源连接逻辑。
2. Snowflake、SQLite 等数据源共享同一个 matcher 接口；数据源方言差异封装在 SQL/profile 构建函数中。
3. 所有 filter 都是有序、可配置、可记录证据的平等 pipeline stage。
4. filter 只能将保留的候选及其 evidence 传给下一阶段。
5. 最终只有通过全部启用阶段的候选才写成 `overlap` 实体。
6. Spider 的 table group/column domain 是认知压缩，不得丢弃成员列的真实值域。
7. 对逻辑列域计算值重叠时，必须使用所有成员列 distinct values 的并集语义。
8. 语义值域分类只能用于 blocking、排序或可证明互斥的保守过滤，不能因为两个列主分类不同就直接断言不可 Join。

## 模块结构

| Module | Responsibility |
|---|---|
| `extractor/db_column_overlap.py` | Storage-backed facade、读取图谱、调用 pipeline、写 overlap 实体 |
| `extractor/utils/overlap_options.py` | 配置、数据结构和 filter specification |
| `extractor/utils/overlap_candidates.py` | 物理列候选、table group 和 logical column domain 压缩 |
| `extractor/utils/overlap_filter_pipeline.py` | 按配置顺序运行 filter 并记录 evidence |
| `extractor/utils/domain_profile.py` | 基于全列统计的格式/范围兼容性判断 |
| `extractor/utils/semantic_domain.py` | 基于列名、类型、官方说明和样本的多标签语义值域分类 |
| `extractor/utils/overlap_value_matchers.py` | SQL、hash、MinHash、Bloom 等值域验证实现 |
| `extractor/utils/overlap_evidence.py` | pair evidence 聚合及 overlap group 构建 |
| `scripts/spider/extract_gold_value_overlaps.py` | 从 Spider2-Snow gold SQL 提取真实物理列值域比较 |
| `scripts/spider/audit_overlap_pipeline.py` | 统计每阶段候选数和 gold recall |

## 输入认知单位

### Physical column

没有被 table group 模式压缩的列，本身构成一个逻辑列域。

### Logical column domain

同一 table group 或同构 table pattern 中承担相同角色的成员列被合并为一个 logical column domain。例如：

```text
ZCTA5_2015.geo_id
ZCTA5_2017.geo_id
ZCTA5_2018.geo_id
```

逻辑值域定义为：

```text
domain_values = union(distinct(member_column_values))
```

它不是选一张代表表，也不是忽略组内列。逻辑列域与其他列域比较时，sample、cardinality 和 membership evidence 都必须按成员并集处理。

完成 logical column domain 构建后，后续所有 overlap 操作都将其视为普通列：value candidate、name overlap、overlap graph 分组、强边分区及 entity naming 均使用 logical ref。物理成员不再参与第二轮分组，只在最终写图时展开，用于把 overlap 实体连接到真实物理表和物理列。

## 当前候选 Pipeline

Spider2-Snow 当前运行配置位于 `scripts/spider/extract_spider2_snow.py`，阶段顺序为：

1. `same_schema`
   - 只保留同一 Snowflake schema 内的候选。
   - 这是当前 Spider 配置，不是 overlap extractor 的全局固定规则。
2. `different_table_group`
   - 同一 table group 内同角色列已经被 logical column domain 吸收，不再创建成员列之间的冗余 overlap。
3. `name_token_overlap`
   - 当前 Spider 配置要求至少一个规范化列名 token 重叠。
   - 已知会漏掉 `submitter_id = case_barcode`、`geo_id = zip_code` 等语义别名，因此后续要由多标签 domain blocking 替代单一名称硬门槛。
4. `domain_compatible`
   - 使用 `domain_profile` 排除可证明不兼容的格式或数值范围。
   - 缺失、mixed 或仅有弱证据时必须保留。
5. `shape_compatible`
   - 使用字符串格式和长度范围进行保守排除。
6. `value_overlap`
   - 运行配置指定的 value matcher。
   - 将估计值、样本量、命中数和方法写入 filter evidence。

每个 stage 的输出都记录：

```text
score
threshold
metric
stage-specific evidence
```

## 语义值域分类

`semantic_domain.py` 当前为每列生成以下 metadata：

```text
physical_family
primary_role
join_likelihood
classification_confidence
semantic_domains[]
representation_domains[]
entity_tokens[]
blocking_keys[]
evidence
```

分类采用多标签而不是互斥聚类。一列可以同时属于：

```text
identifier
geographic
code
sample:digits
fixed_length:N
```

当前分类器已对 Spider2-Snow 152 个库完成离线审计：

- 物理列：1,248,030。
- 去重后的 `database/schema/name/type` 签名：36,115。
- 无法可靠判断语义角色的独立签名：12,984。

逐列审计结果位于：

```text
workspace/baselines/pontis/analysis/spider2_snow/column_domains/
```

当前状态：分类 metadata 已接入 `spider2_snow_schema`，但尚未作为 overlap pipeline 的硬过滤器。

## Value Matcher 状态

### 当前 Spider 运行方法

当前 `extract_spider2_snow.py` 已显式配置：

```text
value_match_method = snowflake_adaptive_probe
sample_bloom_sample_size = 4096
adaptive_sample_initial_size = 256
adaptive_sample_size = 1024
adaptive_sample_max_size = 4096
adaptive_sample_min_overlap = 0.01
adaptive_sample_confidence = 0.99
adaptive_probe_parallel_queries = 2
adaptive_probe_tables_per_query = 8
sample_bloom_max_domain_members = 8
```

旧的 `snowflake_minhash` 和 `adaptive_sample_bloom` 仍作为可选 matcher 保留，但不再是 Spider 默认方法。前者只估计 Jaccard；后者首次运行需要把完整 distinct 值域下载到本地 Bloom filter，对 PATENTSVIEW 等大列不可接受。

### 已有 Sample-Bloom Profile

`sample_bloom` 已实现以下基础能力：

1. 每个物理列读取 distinct value hash。
2. 保留确定性的 bottom-k hash sample。
3. 为完整列值域构建可复用 scalable Bloom membership filter。
4. 从基数较小的一侧取 sample，查询其是否存在于较大一侧 Bloom filter。
5. logical column domain 合并成员 sample 和 Bloom layers。

旧 `sample_bloom` 和 `adaptive_sample_bloom` 继续保留，保证已有调用方不被静默改变。Spider 当前使用后文的 `snowflake_adaptive_probe`。

需要明确：Sample-Bloom 避免的是“每个候选对执行完整集合 Join”，但首次构建 membership profile 仍需要扫描每个参与列的完整值域。它不是零成本，也不能依靠两侧各自少量样本替代完整 membership index。

## Snowflake Adaptive Probe

`snowflake_adaptive_probe` 将完整值域留在 Snowflake，只把每列最多 4096 个 bottom-k hash 和 cardinality 缓存在本地。它不下载完整 distinct 集合。

### 统计逻辑

对于候选列域 A、B：

1. Snowflake 对每个物理列执行 `DISTINCT HASH(normalized_value) ORDER BY hash LIMIT 4096`，生成可复用 KMV/bottom-k profile。
2. logical column domain 对最多 8 个均匀选取的成员 profile 做 signed-hash bottom-k 并集；成员限制是可配置的成本边界。
3. 先根据两侧 KMV profile 估计 Jaccard 及其 99% 上界，再结合两侧 cardinality 转换成 `overlap_min` 上界。
4. 只有上界仍可能达到 1% 的候选才进入精确 membership probe。Jaccard 在这里只做保守预筛，不替代最终 `overlap_min` 指标。
5. 根据 cardinality 选择较小值域 S，从 S 使用最多 1024 个 hash probe，在 Snowflake 中检查这些 hash 是否存在于 L。
6. probe 通过一个 gzip TSV、一次 `PUT` 和一次 `COPY INTO` 装入 session temporary table，避免逐行或小批量 INSERT。
7. 同一物理表上的所有目标列在一次扫描中计算；最多 8 张表用 `UNION ALL` 合并成一个查询，同时最多运行 2 个异步查询。
8. 计算：

```text
estimated_overlap_min = sample_hits / sample_size
```

9. 使用 Wilson 区间判断 sample evidence；当前云端 probe 上限为 1024，4096 profile 主要用于前置 KMV 上界。

### 默认目标参数

```text
initial_sample_size = 256
sample_size = 1024
max_sample_size = 4096
min_overlap = 0.01
confidence = 0.99
parallel_queries = 2
profile_sample_rows = 4096
profile_columns_per_query = 16
full_membership_enabled = false
name_fallback_enabled = false
```

`min_overlap = 0.01` 表示系统关注至少覆盖较小值域 1% 的关系。有限采样无法保证发现“只有一个共同值”的任意低重叠关系；如果产品要求任何非零交集都不能漏，则必须使用完整集合或无损索引。

### Gold 依据

当前 Spider2-Snow gold 审计结果：

- 127 个唯一物理列对。
- 124 个完成精确计算。
- 121 个存在非空交集。
- 112 个 `overlap_min >= 0.5`。
- 最低的正 raw/raw `overlap_min = 0.014905707`。
- 最低总体值 `0.000235746777` 来自带表达式转换的 lineage，不是普通 raw/raw 物理列等值比较。

因此 `1024 + min_overlap=0.01` 适合作为直接物理列 Join 的初始目标。表达式 Join 必须由 SQL lineage/expression analysis 单独处理，不能要求普通 raw-column overlap 完整覆盖。

### PATENTSVIEW Benchmark

在相同 6936 个 pre-value candidates 和已缓存 bottom-k profile 下：

| Implementation | Value stage | Total | Final candidates | Gold recall |
|---|---:|---:|---:|---:|
| Per-table sequential probe | 247.4s | 253.7s | 1326 | 6/6 |
| Async per-table probe | 222.2s | 228.0s | 1326 | 6/6 |
| KMV upper bound + staged COPY + table UNION | 132.2s | 137.7s | 1326 | 6/6 |

KMV 上界在进入 membership 前排除了 2925/6936 对。最终候选数量保持不变，说明被排除的是确定低于 1% 阈值的候选。

1326 是 matcher 输出的 logical-column pair 数，不是最终图谱实体数。logical-column overlap grouping 后的 PATENTSVIEW 结果为：

```text
logical columns: 292
value pairs: 1326
value overlap groups: 352
logical name groups: 48
merged final overlap entities: 395
largest logical group: 36 columns
```

### 写入的 Evidence

```text
method: snowflake_sample_overlap
overlap_coefficient
sample_intersection
sample_min_cardinality
left_sample_cardinality
right_sample_cardinality
decision: sample_above_threshold | name_fallback_uncertain
fallback_reason
estimated: true
```

## 有界样本与完整 Membership

如果分别从 A、B 抽取少量样本，再求两个样本集合的交集，命中概率同时受两侧采样率影响。对于大基数列，即使真实 containment 很高，两个小样本也可能完全不碰撞。

高召回模式应使用：

```text
uniform distinct sample from smaller side
        +
Snowflake-side membership query against the larger complete domain
```

但 sponsored Small warehouse 的单语句上限为 120 秒。BRAZE 上按 8 表合并的 membership query 超时；拆成单表小批次后预计约 80 分钟，不能作为 53 库默认流程。

当前 Spider 默认因此使用速度优先的有界模式：每张物理表读取最多 4096 行，一次生成最多 16 列的 hash profile，然后在本地计算样本集合的 `intersection/min`。该模式不会触发全表 membership 扫描，但不是无损估计；分别采样两侧会漏掉真实重叠。`adaptive_probe_full_membership_enabled=true` 仍保留为单库高召回审计开关。

名称/键角色 fallback 也保留为可选项。53 库实验中，top-5 fallback 将值阶段可见 Gold recall 从 78/102 提高到 95/102，但最终实体从 10495 增长到 23584，AI review 从 2636 增长到 16283，因此默认关闭。

## Static Group Policy

pair evidence 聚合后执行可配置的静态策略：

1. 删除只有名称证据、没有值证据的 group。
2. 删除纯 local ordinal/sequence group。
3. 删除 overlap 小于 0.1 的纯 title/description/text group。
4. 具有明确 token/key role 且 overlap 足够的 group 标记为 `auto_accept`。
5. 其余保留并标记为 `ai_review`。

策略结果写入 `review_status` 和 `group_policy_evidence`。静态拒绝规则不得依赖 Golden SQL 特判。

## 53 库完整审计

输出目录：`workspace/baselines/pontis/analysis/spider2_snow/full_gold_sample_overlap_group_policy`。

| Metric | Result |
|---|---:|
| Databases completed | 53/53 |
| Pre-value candidates | 265939 |
| Sample value-overlap pairs | 22873 |
| Final overlap entities | 10495 |
| `auto_accept` | 7859 |
| `ai_review` | 2636 |
| Median final entities per DB | 18 |
| Mean final entities per DB | 198.0 |
| Gold physical pairs | 127 |
| Gold retained before value stage | 102/127 (80.32%) |
| Gold retained after sample value stage | 78/127 (61.42%) |

最大的 AI review 库为 WEATHER__ENVIRONMENT 474、F1 310、TCGA_MITELMAN 264、CENSUS_BUREAU_ACS_2 263、TCGA 224。大库的均值被这些长尾库显著拉高。

FINANCE__ECONOMICS、US_ADDRESSES__POI、WEATHER__ENVIRONMENT 的官方 schema 含当前 sponsored account 不可访问的 Cybersyn relations。运行时逐关系探测并跳过不可访问对象；`GEOGRAPHY` 等不能安全 `TO_VARCHAR` 的列写为空 profile，不再导致整库失败。因此这些库是“当前账户可访问范围”统计，不代表官方关系全部完成值域扫描。

## 输出实体

最终 `overlap` 是图谱实体，不是旧架构中的独立 `.overlap` 文件。主要 metadata 包括：

```text
from_ref
to_ref
stats
filter_evidence
filter_pipeline
sources
domain_sides
```

当任一侧是 logical column domain 时，`domain_sides` 必须记录 domain ref、role 和成员列引用，使 Agent 可以从 overlap 回溯到真实物理列。

## Recall 规则

1. 所有新增 hard filter 必须先在当前 Spider2-Snow gold physical pairs 上审计。
2. 需要分别报告：
   - filter 前候选数；
   - filter 后候选数；
   - gold retained/missed；
   - 每个 missed pair 的具体 stage 和 evidence。
3. 不得把 lineage 解析错误当成 filter recall 错误。
4. raw/raw、cast/trim、expression-derived pair 必须分开统计。
5. 出现 timeout 或 profile 缺失时不得静默输出部分结果；该库应标记失败。

## 已知问题

1. 当前 Spider 配置的 `name_token_overlap` 会误杀语义别名。
2. `snowflake_minhash` 仍可选，但估计 Jaccard，而目标指标是 `overlap_min`。
3. Snowflake sponsored account 使用共享 Small warehouse，warehouse timeout 为 120 秒，用户无权修改 warehouse 参数。
4. `snowflake_adaptive_probe` 不下载完整列，但首次 bottom-k profile 仍需 Snowflake 扫描每个参与列。
5. logical column domain 的真实 cardinality 是成员 distinct union，不能简单相加；现有 Sample-Bloom domain cardinality 仍是近似值。
6. 语义 domain classifier 尚未进入正式 candidate pipeline；PATENTSVIEW 的 `domain_compatible` 当前未减少候选。
7. sponsored Small warehouse 对并发查询会排队；单纯提高并发度不能线性提速。
8. 默认有界样本模式的 Gold recall 只有 78/127；它适合生成低成本候选，不能声称无损发现所有真实 Join。
9. 当前 group construction 在 CENSUS_BUREAU_ACS_2 这类大逻辑域上仍有数分钟的后处理开销。

## 同步清单

修改以下任一内容时，必须同时更新本文档：

- filter 名称、顺序、阈值或 evidence；
- value matcher 方法或默认配置；
- table group/column domain 合并语义；
- Spider overlap 运行配置；
- gold pair 数量、指标分布或 recall 结果；
- overlap 实体 metadata；
- Snowflake 执行约束和失败策略。

## Change Log

### 2026-07-13

- 用当前 storage-backed graph extractor 架构重写旧文档。
- 将目标指标明确为 `overlap_min`，而非 Jaccard。
- 记录 logical column domain 的成员值域并集语义。
- 记录语义值域多标签分类器及 152 库全量审计结果。
- 记录当前 `snowflake_minhash` 配置与限制。
- 确定下一步 `adaptive_sample_bloom` 的 256/1024/4096 分阶段设计。
- 实现 `adaptive_sample_bloom`、Wilson 置信区间、Bloom false-positive 修正和阶段 evidence。
- 将 Spider2-Snow 默认 value matcher 从 `snowflake_minhash` 切换为 `adaptive_sample_bloom`。
- 将默认 matcher 进一步切换为 `snowflake_adaptive_probe`，完整值域保留在 Snowflake。
- 增加 KMV/Jaccard 置信上界到 `overlap_min` 上界的保守预筛。
- probe 改为 temporary stage 批量 COPY，并按 8 表 UNION、2 查询并发执行。
- PATENTSVIEW value stage 从 247.4 秒降至 132.2 秒，Gold 保持 6/6。
- logical column domain 改为所有后续 overlap/name/group 操作的唯一认知列单位，物理成员仅在最终写边时展开。
- PATENTSVIEW 的 1326 个 value pairs 最终压缩为 352 个 value groups；合并 logical name groups 后生成 395 个 overlap 实体。
- 增加 pair group 静态策略：删除 name-only、local ordinal 和低重叠 text group，并区分 `auto_accept`/`ai_review`。
- profile 改为每表最多 4096 行、每查询 16 列的有界 hash profile，避免逐列全表 bottom-k 聚合。
- 全表 membership 改为可选开关；Spider 默认采用本地样本集合 `intersection/min`。
- 增加名称 fallback 及每列 top-k 限制，但 53 库实验噪声增长过大，因此默认关闭。
- audit 增加逐库检查点、`--resume`、Golden 漏检列表和最终 group-policy 统计。
- 53 个含 Golden SQL 的库全部完成：22873 个 value pairs、10495 个最终实体、7859 auto-accept、2636 AI-review；Gold 召回 78/127。

## Experimental online value domains

`extractor/utils/online_value_domains.py` tests an alternative to exhaustive
column-pair matching. It scans logical columns once and compares each column to
the accumulated value domains in the same schema/domain bucket. The audit can
use either physical-family buckets or the existing semantic-domain classifier;
physical family alone is too coarse for numeric columns. A matching
column extends the selected domain's distinct-value union; otherwise it starts
a new domain.

Two policies are available:

- `union` implements the direct online-union proposal and exposes its order and
  transitive-chain behaviour.
- `union_and_anchor` also requires direct overlap with a configurable fraction
  of representative member columns. This retains member-level evidence and
  limits a large union from absorbing unrelated columns.

`scripts/spider/audit_online_value_domains.py` reads the existing complete
`uint64` distinct-value indexes and reports domain compression, comparison
count, and co-cluster recall for indexed Golden joins. The shared clustering
utility remains independently testable; `db_value_domain` is its graph-writing
extractor.

Initial complete-index results at threshold `0.5`:

| Database | Indexed columns | Pairwise comparisons | Policy | Value domains | Domain comparisons | Indexed Gold recall |
|---|---:|---:|---|---:|---:|---:|
| AIRLINES | 29 | 406 | union | 12-15 by scan order | 126-164 | 2/2 |
| AIRLINES | 29 | 406 | union + anchors | 15 | 146-187 | 2/2 |
| BANK_SALES_TRADING | 88 | 3,828 | union | 38-43 by scan order | 920-1,033 | 5/5 |
| BANK_SALES_TRADING | 88 | 3,828 | union + anchors | 41-43 | 1,014-1,076 | 5/5 |

The experiment confirms that online domains can reduce value comparisons, but
value evidence alone does not define a semantic join domain. With only
schema/physical-family blocking, BANK_SALES_TRADING formed a 22-column numeric
domain containing unrelated IDs, measures, ordinals, and indexes. AIRLINES
also merged `boarding_no` into the `flight_id` domain under permissive union
matching. Conversely, using semantic roles as hard buckets removed those
errors but missed both indexed AIRLINES Golden pairs because `airport_code`
and `arrival_airport`/`departure_airport` have different static roles.
Therefore semantic classification must remain a soft constraint or a narrow
incompatibility veto; it cannot be the domain identity.

The production-facing `db_value_domain` extractor uses the database as its
coarse bucket and treats schema, role, entity tokens, Jaccard, and value
coverage as graded evidence rather than universal hard partitions. Matching
uses dynamic overlap thresholds: strong entity/role evidence can tolerate
low observed overlap, weak key aliases require at least `0.3` overlap/min and
Jaccard evidence, and unrelated roles retain the `0.5` default. Cross-schema
matches require shared entity evidence or near-identical sets. Every accepted
assignment must have at least one actually shared distinct value, even when
semantic evidence lowers its coefficient threshold to zero, and must also
satisfy anchor-member support. It writes
`value_domain:domain` nodes with `pending_review`; it does not write overlap
entities. Spider2-Snow invokes this module instead of `db_column_overlap`.

`explorer/value_domain_review.py` consumes the resulting multi-member domains
instead of overlap entities. Each domain must be marked `accepted`,
`needs_split`, or `rejected`. The agent may create `rel` only for the specific
member subset with stable row-level matching evidence, and `disambig` for
members that share values but differ in meaning, grain, role, or coding system.
It never expands a value domain into a complete graph of pairwise relations.
Domain edge deletion and structural splitting remain extractor-owned; the
explorer records a proposed partition in the domain detail when it marks
`needs_split`. Extractor reruns preserve `review_status`, `brief`, and `detail`
for a stable domain ref, refresh deterministic evidence, and delete only stale
domains whose member-derived ref no longer appears.

For Spider2-Snow, semantic explorers run in dependency order: `topic_group`,
`spider_navigation_prepare`, then `value_domain_review`. The value-domain agent
therefore sees table grain, topic boundaries, and table-group descriptions
before deciding whether shared values imply a stable relationship. Large
schemas with at least 20 first-level cognitive units must connect every
table-group or standalone table to at least one topic; the topic explorer
retries and fails preprocessing if coverage remains incomplete.

The benchmark agent follows staged schema linking rather than global column
retrieval: schema landscape -> topic -> table-group/standalone table -> scoped
logical/physical columns -> accepted domain/FK/relationship. When the selected
scope is still wide, a read-only sub-agent returns candidate tables, columns,
grain, filters, joins, and unresolved checks. The main agent remains responsible
for verification and final SQL.

`scripts/spider/evaluate_schema_retrieval.py` evaluates both direct object
recall and staged recall against local Gold SQL. Staged recall counts a Gold
column as reachable after its physical table is retrieved directly or through
a topic/table-group navigation node. This distinguishes a healthy hierarchical
retriever from an unrealistic requirement that every bridge key independently
match the question text.

`db_table_group` now materializes each repeated member-table column role as a
`logical_col`. The logical column connects its `table_group` and all matching
physical `col` nodes. `db_value_domain` consumes these graph nodes directly and
adds uncovered physical columns as standalone comparison units; it no longer
reconstructs table-group logical columns privately.

With the production rules and the non-empty-intersection invariant, clean
physical-index smoke tests produce ten AIRLINES multi-column domains (largest
size three) and nineteen BANK_SALES_TRADING multi-column domains (largest size
six). The prior unrelated 19/22-column numeric domains and the
`flight_id`/`boarding_no` merge are removed. The independent exact-Golden rule
audit (`audit_gold_value_domain_rules.py`) directly admits `126/127` physical
join pairs (`99.21%`). Its sole rejection is the known bad F1 lineage
`RACES.round = CONSTRUCTORS.constructor_id`.
- 不可访问 relation 和不支持字符串归一化的列改为显式空 profile，避免无关对象导致整库失败。
