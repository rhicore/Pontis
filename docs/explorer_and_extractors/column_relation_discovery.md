# Column Domain 提取与审核

Pontis 使用一个统一 extractor `extractor/db_column_domain.py` 生成候选共享值域实体。BIRD 和 Spider 不再分别生成 `overlap` 与 `value_domain`；它们通过配置选择不同候选策略，最终都写成 `column_domain:domain`。

最后同步：2026-07-15。

## 统一流程

```text
storage-backed db
-> database_catalog：读取表、物理列和静态列证据
-> comparison units：physical col 或 logical_col
-> configured strategy：pairwise_filter 或 online_clustering
-> retained member groups
-> column_domain:domain
-> agent_column_domain_review
-> accepted / needs_split / rejected
-> 必要时创建 rel / disambig
```

共同基础模块：

| 模块 | 责任 |
|---|---|
| `extractor/utils/database_catalog.py` | 统一发现数据库、表、列和 table group |
| `extractor/utils/distinct_value_index.py` | 为需要精确匹配的本地库提供完整 distinct hash set |
| `extractor/utils/overlap_value_matchers.py` | 为 Snowflake 提供有界、多列批量行采样及样本缓存 |
| `extractor/utils/overlap_filter_pipeline.py` | pairwise 策略的有序 filter stages |
| `extractor/utils/online_value_domains.py` | online 策略的 union + anchor 聚类 |
| `extractor/utils/column_domain_entities.py` | 统一实体身份、成员边、upsert 和 stale cleanup |
| `explorer/column_domain_review.py` | 统一审核候选域并生成必要的 `rel/disambig` |

### Explorer 职责边界

关系发现只保留两个互补的在线 explorer：

| 模块 | 输入范围 | 唯一职责 | 可写实体 |
|---|---|---|---|
| `explorer/column_domain_review.py` | extractor 生成的全部 pending `column_domain` | 逐域完成 accepted / needs_split / rejected 审核；把值域候选支持的非 FK 连接或选择边界落图 | `column_domain` metadata、`rel`、`disambig` |
| `explorer/disambiguate.py` | 已有 disambig、表列名称、结构角色和 official description | 审计已有消歧义的准确性、完整性和重复项，并补共享值域无法提出的纯语义选择问题 | `disambig` |

`fk` 是 schema 已声明连接的唯一 owner；`column_domain_review` 不把已有 FK 复制成 rel。`disambiguate` 是 disambig 的最终审计者，但不重新审核 domain 或创建 rel，也不执行数据查询。这样 domain 的语义判断只做一次，消歧义产物仍有一个明确的最终质量负责人。

旧 `relation_disambiguation_review.py` 基于旧 `overlap` 实体，和统一 domain review 重复，已经移入 `explorer/useless/`，不注册也不参与 BIRD/Spider pipeline。它专用的 `overlap_candidates.py` 同时退役。

`db_column_overlap.py` 和 `db_value_domain.py` 是纯候选策略模块，不写图、不注册到 preprocessing registry。唯一公共入口 `db_column_domain.py` 选择一个策略，并统一负责实体写入。

## 配置结构

主 pipeline 只使用一个模块：

```python
module_kwargs = {
    "db_column_domain": {
        "strategy": "pairwise_filter",  # 或 online_clustering
        "strategy_options": {...},
    }
}
```

`strategy` 是必填项，避免数据库在未声明规模和召回策略时悄悄使用默认算法。

## BIRD：`pairwise_filter`

BIRD 配置位于 `scripts/BIRD/extract.py`：

```text
strategy                = pairwise_filter
comparison unit         = physical col
value matcher           = exact SQL
explicit filter stages  = value_overlap(threshold=0)
same-table pairs        = enabled
same-schema restriction = disabled
semantic hard filter    = disabled
shape hard filter       = disabled
column compaction       = disabled
candidate pair cap      = 1,000,000
```

候选流程：

```text
all physical columns
-> enumerate undirected pairs
-> exact distinct-value overlap
-> value_overlap stage
-> merge value/name evidence
-> member groups
```

重叠系数为：

```text
|distinct(A) intersection distinct(B)| / min(|distinct(A)|, |distinct(B)|)
```

`threshold=0` 只表示 pipeline 不增加额外 coefficient 下限。exact matcher 仍执行固定的值证据质量判定：交集至少 10 个 distinct 值，或至少覆盖任意一侧 80%；同时排除单值、纯布尔小域、短码碰撞和默认的纯数值/纯时间碰撞。

`name_overlap_enabled=true` 会提供独立名称证据，但列名不是 value pair 的前置硬筛选。

## Spider：`online_clustering`

Spider 配置位于 `scripts/spider/extract_spider2_snow.py`：

```text
strategy                  = online_clustering
comparison unit           = logical_col 优先，未覆盖列使用 physical col
value reader              = snowflake_adaptive_probe
sample rows per table     = 4096
columns per query         = 16
sample hashes per column  = at most 4096
overlap_threshold         = 0.5
min_anchor_support        = 0.75
max_anchors               = 8
min_members               = 2
match_policy              = union_and_anchor
max_logical_members       = 8
```

候选流程：

```text
physical column catalog
-> replace table-group members with logical_col
-> evenly sample at most 8 physical partitions for each logical_col
-> group physical columns by table
-> read up to 16 columns in one SELECT ... LIMIT 4096
-> union the bounded samples of selected logical members
-> semantic profile and compatibility blocking
-> deterministic column ordering
-> compare against current domain unions
-> require representative-anchor support
-> retain groups with at least two members
```

semantic profile 只阻断强类型、表示形式或业务语义明显冲突的比较。unknown 或弱证据继续进入值比较。即使语义高度一致，也必须至少共享一个实际值。

`max_logical_members` 限制一个 `logical_col` 最多读取 8 个物理分表。成员按 `_ref` 排序后做等距采样，覆盖分表序列的首、中、尾位置。每个选中物理表也只读取前 4096 行；同表最多 16 列合并进一条 SQL。未被 table group 覆盖的物理列同样走有界行采样，不再建立完整 distinct index。online 聚类内部维护 value-hash 到候选 domain 的倒排索引，只跳过样本上必然零交集的 domain。

大规模分表库不会把全部物理列加载到 Python：catalog 直接读取 graph policy 已维护的 `col:standalone`，`logical_col` 的成员则在 Neo4j 内按批次等距采样。若 Snowflake 明确返回某张表“不存在或无权限”，同一轮内该表其余列不再重复查询，最终只写一条汇总告警；其他列级异常不会触发表级跳过。以上规则只减少图传输和必然失败的请求，不改变可访问列的 distinct 集合或聚类阈值。

Spider 样本缓存复用旧 overlap 的 `snowflake_adaptive_probe/rows4096_k4096` JSON 格式。缓存保存每列有限样本，不写完整 `.u64` distinct index。BIRD 继续使用精确 SQLite matcher，不受 Spider 采样策略影响。

2026-07-15 实测：IOWA_LIQUOR_SALES 冷缓存读取 24 列只执行 2 条采样 SQL，值读取 4.6 秒、模块总耗时 23 秒；CRYPTO 的 497 列冷采样约 2 分 40 秒。修复 column-domain 成员边的批量索引写入后，CRYPTO 缓存重跑总耗时 16 秒。排除不在既定 52 库运行范围内的 `CENSUS_BUREAU_ACS_2` 后，图上预估需要采样 22,704 个物理列成员；按实测吞吐量，冷缓存单 worker 约 1.5–3 小时，4 workers 约 30–60 分钟，网络重试时需预留到 90 分钟。全量运行前应先用代表性大库做冷缓存计时并设置硬超时。

BIRD 的 SQLite `sql` matcher 也会先缓存每列的完整 `DISTINCT` 集合，再计算所有列对交集；它与逐列对执行 SQL JOIN 使用相同的 SQLite 类型相等语义，但避免重复扫描同一列。

## 统一实体契约

所有候选都写为：

```text
labels = [column_domain, domain]
name   = column_domain_<member_digest>
_ref   = <db_ref>--column_domain--<member_digest>
```

成员 `col/logical_col` 和所属 `db` 通过 `RELATED_TO` 边表达。表不重复直连 domain；物理列仍可沿 `logical_col` 的边回溯。

公共 metadata：

```text
extraction_strategy = pairwise_filter | online_clustering
entity_method
member_count
review_status
brief
detail
```

`pairwise_filter` 证据字段：

```text
value_match_method
sources
stats
filter_evidence
filter_pipeline
pair_stats
domain_sides
group_policy_evidence
```

`online_clustering` 证据字段：

```text
grouping_method
union_cardinality
semantic_roles
overlap_metric
overlap_threshold
anchor_overlap_threshold
min_anchor_support
extraction_evidence
value_read_method
```

成员关系使用边表达；metadata 保存算法事实和“为什么形成该候选”的证据。

## 身份和重跑

- `_ref` 只由数据库和排序后的成员 refs 计算，不依赖策略、方向或运行顺序。
- 同一成员集合即同一候选域；切换策略时刷新 extraction evidence，不复制实体。
- 重跑刷新 extractor-owned 字段，保留 `review_status`、`brief` 和 `detail`。
- 当前数据库中不再出现的候选会被清理。
- 重跑统一 extractor 时会删除已被替代的旧 `overlap/value_domain` extractor 节点。

## 审核语义

`column_domain` 只表示成员之间存在共享值域证据：

| 状态 | 含义 |
|---|---|
| `pending_review` | 尚未完成语义审核 |
| `accepted` | 成员使用同一个可复用编码或标识空间 |
| `needs_split` | 值证据把多个业务子域合并在一起 |
| `rejected` | 低基数、数值、时间或偶然碰撞，不构成可复用值域 |

即使 domain 被接受，也不自动意味着成员两两都能 JOIN。稳定行级匹配需要已有 `fk`，或由 review/query 证据创建 `rel`。`disambig` 记录同域但语义、粒度或角色不同的成员边界。

## 回归测试

- `scripts/spider/test_column_domain_entities.py`
- `scripts/spider/test_online_value_domains.py`
- `scripts/spider/test_value_domain_extractor.py`
- `scripts/spider/test_logical_overlap_grouping.py`
- `scripts/spider/test_column_domain_review.py`

算法审计脚本仍可直接导入历史策略模块：

- `scripts/spider/audit_overlap_pipeline.py`
- `scripts/spider/audit_online_value_domains.py`
- `scripts/spider/audit_gold_value_domain_rules.py`
- `scripts/spider/extract_gold_value_overlaps.py`
