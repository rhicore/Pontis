# Pontis 当前系统审计：BIRD Extract、图谱与 Agent 工具

> 审计日期：2026-07-13
> 审计对象：当前代码、当前 11 个 BIRD dev 图谱、当前 BIRD runtime Agent
> 文档状态：只记录当前行为、已执行验收和仍存在的问题

## 1. 当前结论

当前 11 个 BIRD dev 图谱可正常供 Agent 使用：

- runtime 只暴露 `find`、`meta`、`query`；
- 1462 个实体全部可从数据库根节点导航；
- 1462/1462 个 `find` 返回 ref 可原样交给 `meta`；
- 1451 个需要语义检索的实体全部已有 embedding，待回填数为 0；
- CSV description 只作为 official metadata 输入，不创建 CSV 图节点；
- tool regression 为 86/86，storage/Neo4j integration test 通过。

本轮已修复先前审计中的 overlap ref、关系检索、semantic 分页、参数边界、邻接末页提示、query scope 文档、陈旧向量索引、测试套件和 BIRD 工具调用收敛问题。文档不再保留这些问题的旧行为描述。

仍需继续处理的主要问题只有两类：

1. source-rooted ref 仍按输出实体逐个查询路径，宽结果集存在 N+1 成本；
2. 最新代码尚未重新执行一次带 LLM 的 11 库全量 extract。当前图已完成确定性迁移和 embedding 回填，但这不等于重新生成所有 LLM detail。

## 2. 当前 BIRD Extract 链路

### 2.1 Source 与根节点

`pontis.yml` 中每个 BIRD project 都显式命名，并直接指向唯一 SQLite 文件：

```yaml
california_schools:
  source:
    type: sqlite
    path: ../workspace/.../california_schools/california_schools.sqlite
```

每库只有一个 `_source_anchor=true` 的数据库节点，不增加公开 `source` label。BIRD 不注册 FS、Text 或 CSV schema module。数据库节点就是导航根：

```text
california_schools.sqlite:db
```

Explorer 使用 `workspace.active_projects` 中的显式项目名，不再从 source path basename 猜项目名。

### 2.2 Pipeline

```text
SQLiteSchemaModule
  -> db / table / col / fk

STATIC
  -> db_column_stats
  -> db_fk_validate
  -> db_column_overlap

OFFICIAL
  -> bird_official_description_extract
  -> database_description CSV 写入实际 table/col official metadata

AGENT
  -> schema_prepare
  -> relation_disambiguation_review
  -> disambiguate
  -> bird_profile
  -> description_audit
  -> readme

EMBEDDING
  -> semantic_embedding
```

`database_description/*.csv` 是 importer 输入，不是图实体。README 是 `knowledge/readme` 实体，不是物理 file。

## 3. 当前图谱状态

| 类型 | 节点数 | 有 embedding | find → meta |
|---|---:|---:|---:|
| db | 11 | 0 | 11/11 |
| table | 75 | 75 | 75/75 |
| col | 798 | 798 | 798/798 |
| fk | 104 | 104 | 104/104 |
| overlap | 345 | 345 | 345/345 |
| disambig | 118 | 118 | 118/118 |
| knowledge/readme | 11 | 11 | 11/11 |
| **合计** | **1462** | **1451** | **1462/1462** |

补充健康检查：

- physical col：798；CSV col：0；
- semantic embedding pending：0；
- 104 个 FK 均有一条 `role=source` 列边和一条 `role=target` 列边；
- 345 个 overlap 均与成员 table/col 相连；
- Pontis 管理的无节点历史向量索引已清理；
- `Match.away_team_goal` 已改为“最常见值为 1 球”，不再把众数误写成最大值。

## 4. Agent 工具当前行为

### 4.1 `find`

`find` 返回的第一列是从项目 source 回溯得到的图导航坐标。列实体固定优先走结构归属路径：

```text
db -> table/view -> col
```

不会因为列同时连接 FK、overlap 或 disambig 而显示成 `db/fk/col`。关系或消歧实体则继续从实际图边导航。

同名实体不会被猜测或合并：

```text
find(ref="CDSCode:col")

california_schools.sqlite:db/frpm:table/CDSCode:col
california_schools.sqlite:db/schools:table/CDSCode:col
```

grouped overlap 使用普通稳定名称，例如 `value_domain_1fedb85cc4:overlap`。名称不再枚举成员列，也不包含中括号；成员只从图边读取。全量 1462 实体 roundtrip 失败数为 0。

Semantic find 当前具备：

- CamelCase 拆分，例如 `CDSCode -> CDS + Code`；
- 简单英文单复数归一，例如 `schools -> school`；
- FK/overlap 的结构化检索文本和 embedding；
- 稳定的最多 500 条语义检索窗口，offset 不再改变窗口总数。

分页参数统一要求 `offset >= 0`、`limit >= 1`，非法值返回明确错误。

### 4.2 `meta`

`meta` 支持 db/table/col/FK/overlap/disambig/knowledge 的完整 source-rooted ref。同名短 ref 会列出所有可复制候选，不会选择隐含起点。

FK 和 overlap 没有工具层特殊输出格式：不添加 `[source]`、`[target]`、`FK ...`、`value overlap ...` 等装饰，也不合成关系专用 detail。它们与其他实体一样显示普通 `name:tag`、自身公开属性和普通邻居。边上的 role 只属于图数据，不参与工具排版。

默认输出展示实体自身 metadata、邻接类型计数和主要邻居。`neighbor_label` 可按类型分页；无论是否还有下一页，都会显示总数和当前区间。

内部字段不向 Agent 暴露，包括：

```text
_ref / _db_ref / _db_connect / embedding / embedding_hash
```

### 4.3 `query`

`query` 使用 SQL AST 判定只读语句，支持 SELECT、CTE、join、聚合、排序和只读 PRAGMA，并对错列、歧义列和可用表提供提示。SQLite `replace()` 是普通函数，不会再被误判为 `REPLACE INTO`；多语句或在 CTE 中嵌入写操作仍会被拒绝。

任何位于数据库 source 下的 descendant ref 都只是选择所属数据库，不限制 SQL 的表范围：

```text
db/table/col/fk/overlap/disambig/knowledge ref
    -> owning SQLite database
```

该行为已写入工具 schema 和 Agent prompt。`limit` 要求至少为 1。

### 4.4 BIRD 工具调用收敛

BIRD Agent 当前有三层收敛机制：

1. 相同工具名和规范化参数的调用直接复用已有结果；
2. 每题最多执行 40 次工具调用，其中 `query` 最多 24 次；
3. 达到工具额度或 round limit 后，下一轮移除全部工具定义，只允许模型基于已有事实生成最终回复。

BIRD prompt 加载精简的数据库 ontology，保留 `db -> table/view -> col` 拓扑以及 `fk/rel/overlap/disambig/knowledge` 的实体语义；不加载 FS、CSV/JSON、Snowflake 和大型数据库分组导航。工具工作流从 question/evidence 中的业务概念开始定向检索，并把“候选 SQL 已成功执行且满足问题”作为完成条件。数据库和全表枚举仍是 `find` 的能力，但不再作为默认起手入口。

## 5. Metadata 去冗余审计

### 5.1 规则

本轮采用以下存储规则：

> 能由现有实体间边关系唯一推出的语义事实，不再作为实体 metadata 重复持久化。

需要区分三类字段：

1. **语义 metadata**：Agent 可读的业务含义、统计、证据；
2. **图关系事实**：成员、归属、端点，优先由边表达；
3. **内部运行字段**：寻址、数据库连接和幂等 upsert 所需，以 `_` 开头且不向 Agent 暴露。

### 5.2 已删除的冗余字段

当前 11 库中下列属性计数均为 0：

| 删除字段 | 原实体 | 当前事实来源 |
|---|---|---|
| `table_count` | db | db 与 table 的边 |
| `view_count` | db | db 与 view 的边 |
| `column_count` | table/view | table/view 与 col 的边 |
| `table_scope` | overlap | overlap 连接成员列所属的 table |
| `from_table/from_column` | fk | `role=source` 的列边 |
| `to_table/to_column` | fk | `role=target` 的列边 |

tool 与 storage 测试已改成从边计算这些信息，不再断言重复属性。

### 5.3 各实体当前 metadata 判断

#### db

保留 `name/path/file_size/modified_at/index_count` 等文件身份和物理属性。表、视图数量已删除，改由邻接计算。

#### table/view

保留 `row_count`、`primary_key`、`brief/detail` 和 official description。列数量已删除。`primary_key` 目前没有对应的 PK 角色边，因此暂不属于可由现有边推出的冗余。

#### col

保留 official description、值域统计、NULL 统计、sample/topk、brief/detail。这些不是图边能够表达的事实。

`table_name` 仍作为隐藏的数据库访问缓存存在，运行时 sample/topk 查询会使用它；公开 meta 已隐藏。若要物理删除，需要先让所有 extractor 和 runtime SQL helper 都通过 table 边取得表名。

#### fk

公开端点字段已删除。source/target 由有角色的边表达；保留 `confidence/match_rate/total_count/violation_count` 等关系质量事实。

`_from_col_ref/_to_col_ref` 仍是隐藏的 upsert/validator 键，不属于 Agent metadata。后续若改为以 FK id 加角色边幂等 upsert，可再移除这两个内部缓存。

#### overlap

成员 table/col 和 `table_scope` 不再重复存储。保留 `sources/stats/filter_evidence/filter_pipeline`，因为这些是产生 overlap 的证据和质量信息，不可从“相连”这一事实推出。

group overlap 的 `domain_sides/pair_stats` 含成员角色和成对指标；当前无角色的 overlap 成员边不能完整表达这些证据，因此暂时保留。若未来把 side、pair metric 放到关系或独立 evidence entity 上，可继续归一化。

#### disambig

成员列由边表达；实体只保留 `brief/detail/hints`。detail 描述字段之间的语义边界，不只是重复成员列表，因此应保留。

#### knowledge/readme

保留 `brief/detail`。它们是数据库级语义说明，连接 db 的边只表达归属，不能替代内容。

### 5.4 当前仍可进一步归一化的内部缓存

以下不是公开 metadata 冗余，但在物理属性层仍可继续优化：

- table/col 的 `_db_ref`、`_db_connect`；
- col 的隐藏 `table_name`；
- FK 的 `_from_col_ref/_to_col_ref`；
- overlap 的 `domain_sides/pair_stats` 中重复出现的 ref。

这些字段分别承担跨 storage 连接、运行时数据库访问、幂等写入或证据角色，不应直接删除。下一步应先为边增加足够的类型/角色，再迁移消费者，最后删除缓存。

## 6. Overlap 当前设计

`db_column_overlap.py` 当前是编排层：

```text
candidate generation
-> ordered filter pipeline
-> value matcher
-> evidence grouping
-> group policy
-> graph write
```

BIRD 配置使用 SQL value overlap 的单值 filter；domain、shape、key-like 和 name-token 强制筛选关闭。Spider2 使用独立配置，不会被 BIRD 参数覆盖。

当前结构仍可把 `generate()` 的大量 keyword 进一步收敛成 `OverlapOptions + overrides`，但这属于代码接口整理，不影响当前 BIRD 结果。

## 7. Schema RAG 基线

评测 query 为 `question + evidence`，golden 包含 SQL 在 SELECT、JOIN、WHERE、GROUP BY、HAVING、ORDER BY 和子查询中使用的全部物理表列。

- 1534 题；
- 平均 1.94 个表、4.50 个列、6.44 个表列对象；
- 最多 5 个表、11 个列、15 个表列对象；
- golden 不可达问题：0。

| Top-N | 表+列 Perfect Recall | 仅列 Perfect Recall |
|---:|---:|---:|
| 5 | 5.74% | 13.82% |
| 10 | 19.30% | 31.23% |
| 20 | 40.29% | 49.41% |
| 50 | 72.16% | 76.60% |
| 100 | 92.89% | 95.70% |
| 150 | 98.11% | 99.48% |
| 完美召回所需 | 191 | 185 |

关系和 disambig 现在已具备一致的 ref 与 embedding，可以开始公平比较 all-entity retrieval。建议下一阶段实验 query rewrite、typed quotas、图扩展和 rerank，同时继续报告最终上下文实体数。

## 8. 验收记录

已执行：

```text
scripts/tool/test_tools.py              86 passed, 0 failed
scripts/storage/test_store.py           passed
scripts/agent/test_tool_control.py       3/3 passed
query AST focused regression            7/7 passed
BIRD prompt regression                  passed
11 库 find -> meta roundtrip            1462/1462
semantic embedding pending              0
metadata forbidden redundant fields     0
```

全量 roundtrip 使用每库复用一个 Workspace、四库并发完成。曾尝试用 `allShortestPaths` 解决列导航同长路径，但在 overlap 密集图上性能不可接受，已撤销；当前实现通过 table/view 容器边确定列坐标。

## 9. 剩余问题与建议顺序

### P1：最新代码的 extract 再生成验收

先对 California 运行一次完整 `--force`，确认 storage 重新生成时不会恢复已删除的冗余 metadata；再决定是否对 11 库执行带 LLM 的全量重跑。

### P2：批量构造 public ref

当前列表中的每个实体仍会单独查询 source path。应把同一页节点一次性送入 Cypher，以批量返回容器路径和 display ref，避免 N+1。

### P2：继续归一化内部运行缓存

先设计 typed/role edges 和批量数据库访问上下文，再迁移 `table_name`、FK ref cache 与 overlap evidence consumers。不要在消费者迁移前直接删内部字段。

### P2：metadata 事实 gate

`away_team_goal` 已修正，但仍建议对“最大/最小/最多/最常见/唯一/全部”等高风险词增加统计一致性检查，阻止同类 detail 错误再次进入 embedding。
