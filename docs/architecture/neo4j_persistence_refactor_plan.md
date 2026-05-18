# Neo4j Persistence Refactor Plan

## 背景

Pontis 以 Neo4j 作为持久化图和正式 Cypher 执行面。Neo4j 负责完整
Cypher 查询、索引、向量检索和持久化。

当前第一阶段不引入 `GraphPatch`，也不迁移 explorer。系统先收敛成：

```text
storage
  - 管 Neo4j 查询和写入边界
  - 管 query-time source projection
  - 管 extractor 的执行与触发
  - 管 resolver pointer 后处理

explorer
  - 暂时保持外部 agent 模块
  - 通过 storage tools / Workspace 访问数据
```

核心原则：

```text
读查询可以使用完整 Cypher。
source projection 和 extractor 归入 storage 的 trigger 系统。
普通查询执行中途不调用 Python 模块；模块只在 query 前或任务触发时运行。
resolver pointer 只在 query 返回后解析。
```

## 目标

- 继续使用 Neo4j 作为唯一持久化图和 Cypher 执行面。
- 将当前 `storage/stores/*` 逐步迁移为 storage 内部 source modules。
- 将当前 `extractor/modules/*` 迁入 storage 层，作为 extraction modules。
- 第一阶段不改 explorer；explorer 仍通过 storage 暴露的图和 tool 访问数据。
- 重点建立统一 trigger 机制，区分 query-time、manual、source-changed、
  scheduled、index-build 等触发时机。
- 保留 `src` / preview / sample 等 resolver pointer 机制，用于查询后回源。
- 暂不引入 `GraphPatch` / `GraphCommand`；模块写入可以先通过受控 storage
  内部 Cypher 完成。

## 当前模块分层

```text
storage source module
  读：原始数据源
  写：query 前将轻量结构事实同步到 Neo4j
  例子：fs、csv_schema、db_schema

storage extractor module
  读：Neo4j + 原始数据源
  写：把派生事实写回 Neo4j
  例子：column stats、topk、sample、AI summary、overlap、fk validate

explorer
  读：通过 storage tools 读 Neo4j，必要时通过 src 回源
  写：暂时保持现状，不在第一阶段迁移
  例子：join detect、disambiguate、README、agent analyze
```

source module 和 extractor 的差别不是“是否产图”，而是触发频率和成本：

```text
source module
  轻量、确定性、可 query-time 自动触发

extractor
  成本较高、可能调用 LLM 或扫描数据、一般手动/定时/数据变化触发
```

## 建议目录

第一阶段保持在 `storage/` 内收敛：

```text
storage/
  workspace.py          # cypher / trigger entry / result resolver
  neo4j.py              # Neo4j 连接、执行、结果归一化
  query_inspector.py    # query trigger 的轻量解析
  triggers.py           # TriggerEvent / TriggerPolicy / module selection
  modules/
    base.py             # StorageModule 协议
    source/
      fs.py
      csv_schema.py
      db_schema.py
    extractors/
      db_column_stats.py
      csv_column_stats.py
      ai_summary.py
      overlap.py
```

后续如果需要再把 explorer 也统一进来，可以在第二阶段把 `storage/modules`
进一步抽成顶层 `producers/`。

## TriggerEvent

trigger 是本次重构的核心。它统一描述“为什么某个模块要运行”。

```python
@dataclass
class TriggerEvent:
    type: str
    project: str
    source_scope: str = ""
    query: str = ""
    parsed_query: object | None = None
    reason: str = ""
    payload: dict = field(default_factory=dict)
```

常见类型：

```text
query
  Workspace.cypher() 查询前触发。
  只允许轻量 source module 自动运行。
  例如查询 :file/:db/:csv/:table/:col/:fk 或 RETURN n.src。

manual
  用户或脚本显式触发。
  适合大多数 extractor。

source_changed
  文件 mtime、size、schema fingerprint 变化后触发。
  适合 source module 重新同步结构事实，并标记 extractor 结果过期。

schedule
  定时刷新。
  适合 profile、summary、embedding 等可异步更新的派生事实。

index_build
  创建全文/向量/语义索引前后触发。
  适合提前物化需要索引的属性。

dependency_invalidated
  上游事实变化后触发下游派生结果失效。
  例如 schema/header 变化后，column stats、summary、overlap 需要标记过期
  或重新计算。

cache_miss
  查询发现某个派生属性不存在或已过期时触发。
  第一阶段不建议同步执行重型 extractor；可以记录缺失并投递后台任务。

policy
  达到策略阈值后触发。
  例如某个 source 被频繁访问、benchmark 错误集中出现、某类字段反复缺少
  summary，于是自动补跑相关 extractor。

startup
  workspace 启动或项目首次打开时触发。
  适合预热根目录、README、轻量 schema、全局知识索引等低成本事实。

shutdown
  benchmark 结束、任务结束或系统空闲时触发。
  适合 flush cache、stale cleanup、manifest compaction、写入运行摘要。

event
  外部事件触发，例如文件监听、对象存储事件、Git webhook、数据库 CDC、
  消息队列事件。第一阶段可以把它归约成 source_changed。
```

第一阶段不把 `agent_task` 纳入 trigger 系统，因为 explorer 暂时不迁移。

推荐第一阶段先实现：

```text
query
manual
source_changed
schedule
index_build
```

后续再扩展：

```text
dependency_invalidated
cache_miss
policy
startup
shutdown
event
agent_task
```

## TriggerPolicy

每个 storage module 声明自己响应哪些 trigger，而不是让 `Workspace`
硬编码 source 类型。

模块实现应保持扁平独立：

- module 不 import 其他 source / extractor module。
- module 不直接调用其他 module 的 `run()`。
- module 之间只通过 Neo4j 中的图事实、`src` handle 和 storage 公共 helper 通信。
- 执行顺序和前置条件交给 TriggerRouter / Orchestrator。

```python
class StorageModule:
    id: str
    kind: str       # source | extractor

    def wants(self, event: TriggerEvent) -> bool:
        return False

    def run(self, event: TriggerEvent, ctx: ModuleContext) -> None:
        ...

    def resolve_pointer(self, kind: str, payload: str, *, node: dict | None = None):
        return None
```

query-time source module 示例：

```python
class DBSchemaModule(StorageModule):
    id = "db_schema"
    kind = "source"

    def wants(self, event):
        if event.type != "query":
            return False
        return event.parsed_query.touches_labels({"table", "view", "col", "fk"})
```

manual extractor 示例：

```python
class DBColumnStatsExtractor(StorageModule):
    id = "db_column_stats"
    kind = "extractor"

    def wants(self, event):
        return event.type in {"manual", "schedule", "source_changed"} and (
            event.payload.get("extractor") in {None, self.id}
        )
```

## Module Dependencies

模块独立不等于没有依赖。依赖不应写成模块之间的直接调用，而应声明给
trigger/orchestrator。

反例：

```python
class AIDBSummaryExtractor:
    def run(...):
        db_schema.run(...)
        db_column_stats.run(...)
        ...
```

推荐：

```python
@dataclass
class ModuleRequirement:
    module: str
    level: str = "hard"       # hard | soft
    freshness: str = "any"    # any | current | max_age
    source_scope: str = ""
    reason: str = ""
```

模块声明依赖：

```python
class AIDBSummaryExtractor(StorageModule):
    id = "ai_db_summary"
    kind = "extractor"

    requirements = [
        ModuleRequirement(
            module="db_schema",
            level="hard",
            freshness="current",
            reason="需要当前数据库的表、列、外键结构",
        ),
        ModuleRequirement(
            module="db_column_stats",
            level="soft",
            freshness="any",
            reason="统计信息可以提升 summary 质量，但不是运行前提",
        ),
    ]
```

执行流程：

```text
TriggerEvent(type="manual", payload={"extractor": "ai_db_summary"})
  -> TriggerRouter 选中 ai_db_summary
  -> Orchestrator 检查 requirements
  -> db_schema 缺失或过期时先运行 db_schema
  -> soft dependency 缺失时跳过、告警或投递后台任务
  -> 运行 ai_db_summary
```

依赖类型：

```text
hard
  不满足则当前模块不能运行。
  例：AI DB summary 必须先有 db_schema。

soft
  有则使用，没有也能运行。
  例：summary 有 column stats 更好，但不是必需。

freshness=current
  依赖必须和当前 source fingerprint 一致。
  例：schema 提取必须匹配当前 sqlite 文件。

freshness=max_age
  允许一定时间内的旧结果。
  例：每日刷新 summary 或 embedding。
```

第一阶段暂不实现依赖编排，只记录该模型。当前可先由 manual 脚本保证顺序：

```text
db_schema -> db_column_stats -> ai_db_summary
```

后续再在 `TriggerRouter` 后增加轻量 `Orchestrator.resolve_requirements(...)`。

## Query-Time Trigger

`Workspace.cypher()` 的执行流程：

```text
Workspace.cypher(query)
  -> parse query
  -> TriggerEvent(type="query", parsed_query=..., query=...)
  -> TriggerRouter 选择需要的 source modules
  -> source modules 同步轻量结构事实到 Neo4j
  -> Neo4j 执行原始 Cypher
  -> ResultResolver 解析返回值里的 resolver pointer
  -> 返回结果
```

query-time 只允许这些行为：

- 文件/目录轻量投影
- CSV/TSV schema 投影
- SQLite schema 投影
- `src` pointer 写入或刷新
- 小文件轻量属性

query-time 不允许：

- 大表全量扫描
- topk/cardinality/profile
- LLM summary
- overlap 计算
- embedding 生成
- agent explorer

这些应通过 manual / schedule / source_changed / index_build 触发。

## Extractor Trigger

extractor 迁入 storage 后，不再是外部脚本随意调用 `workspace.cypher`
写图，而是由 storage trigger 运行。

推荐入口：

```python
workspace.run_extractors(
    names=["db_column_stats", "overlap"],
    project="california_schools",
    source_scope="california.sqlite",
)
```

内部转成：

```text
TriggerEvent(type="manual", payload={"extractor": name})
  -> TriggerRouter.select(...)
  -> extractor.run(event, ctx)
```

extractor 可以读完整 Neo4j，也可以通过 `src` 回源：

```text
extractor.run(...)
  -> workspace.cypher("MATCH ... RETURN ...")
  -> RETURN n.src AS src
  -> ResultResolver 生成 SrcHandle
  -> extractor 读取源数据
  -> extractor 用 storage 内部写接口更新 Neo4j
```

第一阶段可以继续复用受控 Cypher 写入，但写入口应集中在 storage，
避免 extractor 到处散落 `CREATE / SET / DELETE` 字符串。

## Source Changed Trigger

source module 应记录轻量 fingerprint：

```text
path
mtime_ns
size
schema_version / header hash / table list hash
```

当 source 变化：

```text
TriggerEvent(type="source_changed", source_scope=...)
  -> source module 刷新结构事实
  -> storage 标记依赖该 source_scope 的 extractor 结果可能过期
```

第一阶段可以只做“重新运行 extractor”的粗粒度策略，不必立即实现复杂依赖图。

## Dependency Invalidated Trigger

当 source module 刷新结构事实后，应能产生一个依赖失效事件。

示例：

```text
CSV header hash changed
  -> TriggerEvent(type="dependency_invalidated", source_scope="schools.csv")
  -> 标记 csv_column_stats / ai_summary / overlap 结果过期

SQLite table list changed
  -> TriggerEvent(type="dependency_invalidated", source_scope="california.sqlite")
  -> 标记 db_column_stats / table summary / overlap 结果过期
```

第一阶段不需要实现精细依赖图。可以先采用粗粒度策略：

```text
source_scope 变化
  -> 标记同一 source_scope 下所有 extractor-owned 属性为 stale
  -> 等 manual / schedule 再重算
```

## Cache Miss Trigger

`cache_miss` 用于“查询时发现缺失”，但不等于 query-time 直接跑重任务。

示例：

```text
query 需要 c.topk，但 topk 不存在
  -> 返回当前结果
  -> 记录 TriggerEvent(type="cache_miss", payload={"property": "topk"})
  -> 后台或下一轮 manual/schedule 补跑 topk extractor
```

这个 trigger 适合改善体验，但必须避免在普通 `Workspace.cypher()` 中阻塞运行
LLM、全表扫描或 embedding。

## Policy Trigger

`policy` 用于系统根据运行状态主动补齐图谱。

示例：

```text
某个数据库被访问超过 N 次
  -> 自动补跑 README / table summary

benchmark 中某类错误集中出现
  -> 自动补跑 disambiguation / column summary

某类查询频繁读取 src
  -> 预热相关 source projection 或 sample
```

第一阶段只需要保留事件类型和日志，不必实现复杂策略引擎。

## Scheduled Trigger

定时任务不应该走 query-time 路径。

适合 schedule 的任务：

- column stats refresh
- AI summary refresh
- embedding refresh
- stale cleanup
- index maintenance

建议先实现进程内调度入口，不引入 Kafka / Pulsar：

```text
storage scheduler
  -> TriggerEvent(type="schedule", ...)
  -> TriggerRouter
  -> extractor.run(...)
```

## Startup / Shutdown Trigger

`startup` 适合做低成本预热：

- 打开 workspace 后同步根目录和 README
- 同步数据库文件和轻量 schema
- 预热全局知识项目的索引入口

`shutdown` 适合做收尾：

- flush module cache
- compact manifest
- 清理过期临时索引
- 写入 benchmark/extraction 运行摘要

这些 trigger 不应该影响普通查询延迟。第一阶段可以只保留入口，不自动启用。

## Event / CDC Trigger

外部事件包括：

- 文件系统 watch
- 对象存储事件
- Git webhook
- 数据库 CDC
- 消息队列事件

第一阶段不引入 Kafka / Pulsar。外部事件统一转换成：

```text
TriggerEvent(type="source_changed", ...)
```

后续如果需要分布式部署，再把 `TriggerRouter` 前面替换成事件总线。

## Index Build Trigger

语义检索、全文索引、向量索引要求参与索引的属性已经在 Neo4j 中。

流程：

```text
index build request
  -> TriggerEvent(type="index_build", payload={"property": "detail_embedding"})
  -> 运行需要的 extractor / embedding module
  -> storage 内部执行受控 Cypher 创建索引
```

索引创建本身可以直接使用 storage 内部 Cypher；不需要 GraphCommand。

## Resolver Pointer

resolver pointer 用于查询后把普通字符串替换成运行时 Python 对象。

第一阶段推荐格式：

```text
<pontis:project:module:kind:payload>
```

示例：

```text
<pontis:california_schools:fs:src:README.md>
<pontis:california_schools:db_schema:src:california.sqlite::table::satscores>
<pontis:california_schools:csv_schema:src:data/schools.csv::School Name>
```

字段含义：

```text
project
  当前 project scope

module
  storage module id，例如 fs / db_schema / csv_schema

kind
  module 内部分发类型，例如 src / preview / sample / connect

payload
  module 私有数据，中心层不解析
```

解析流程：

```text
Neo4j 返回 row
  -> ResultResolver 递归扫描完整字符串匹配 <pontis:...>
  -> ModuleRegistry.get(project, module)
  -> module.resolve_pointer(kind, payload, node=node)
  -> 用返回的 Python 对象替换原字符串
```

pointer 规则：

- Neo4j 内部只把 pointer 当普通字符串。
- pointer 只在查询返回后解析。
- pointer 不参与 identity。
- pointer 不参与 `MATCH / WHERE / ORDER BY / index / vector search` 语义。
- pointer 不作为 stale / tombstone / source sync 的依据。
- 一个节点可以有多个 pointer 属性，例如 `src`、`sample_ref`、`preview_ref`。

## src

`src` 是 resolver pointer 的主要用例。

Neo4j 存储：

```python
{
    "src": "<pontis:california_schools:db_schema:src:california.sqlite::table::satscores>"
}
```

查询：

```cypher
MATCH (t:table {name: 'satscores'})
RETURN t.src AS src
```

返回后：

```text
ResultResolver
  -> module=db_schema
  -> kind=src
  -> payload=california.sqlite::table::satscores
  -> db_schema.resolve_pointer(...)
  -> SrcHandle(...)
```

`resolve_pointer` 应返回 lazy handle，不应立即扫大文件或加载整表。

## 写入边界

第一阶段不引入 `GraphPatch`。但仍应避免 extractor 和 source module 到处
手写写入 Cypher。

建议先提供 storage 内部写辅助：

```python
storage.upsert_node(...)
storage.upsert_edge(...)
storage.set_props(...)
storage.mark_stale(...)
```

这些 helper 内部可以直接使用参数化 Cypher。模块可以读完整 Cypher，
但写入优先通过 helper，以便后续如果重新引入 patch 或 transaction，
迁移成本较低。

硬规则：

- 写入必须参数化，禁止 f-string 拼业务值。
- query-time trigger 不做重型写入。
- extractor 默认不 hard delete，只标记 stale/tombstone。
- `src` pointer 不作为 identity 或删除依据。
- explorer 第一阶段不纳入该写入边界。

## 实施步骤

1. 增加 `storage/triggers.py`，定义 `TriggerEvent`、`TriggerRouter`。
2. 将当前 `storage/stores/*` 视为 source modules，改成实现
   `wants(event)` / `run(event, ctx)`。
3. 将 `Workspace.cypher()` 里的 query-time module 选择逻辑改为
   `TriggerEvent(type="query") -> TriggerRouter`。
4. 在 `storage/modules/extractors/` 中迁入当前 extractor 的核心逻辑。
5. 给 `Workspace` 增加 `run_extractors(...)` 或 `trigger(...)` 入口。
6. 将 extractor 写入逻辑收敛到 storage 内部 helper，不再散落直接
   `workspace.cypher("CREATE/SET/DELETE ...")`。
7. 将 resolver pointer 格式迁移到
   `<pontis:project:module:kind:payload>`。
8. 用 `ModuleRegistry.resolve_pointer(...)` 替换 workspace 内部的 module map resolver。
9. 保持 explorer 现状，等 storage/extractor trigger 稳定后再评估是否迁移。

## 非目标

- 第一阶段不实现 `GraphPatch`。
- 第一阶段不实现 `GraphCommand`。
- 第一阶段不迁移 explorer。
- 不实现完整分布式 Cypher。
- 不用 JSON API 复刻 Cypher / SQL 子集。
- 不让 Neo4j 存储 Python 对象。
- 不让 resolver pointer 参与查询语义、身份匹配或同步删除。
- 不在 Neo4j 查询执行中途调用 Python module。
- 不在第一阶段引入 Kafka / Pulsar；先实现进程内 trigger。
