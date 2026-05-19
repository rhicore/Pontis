# Neo4j 持久化重构计划

## 决策

第一阶段只做 storage source module 的整理：

- Neo4j 是唯一持久化图和 Cypher 执行面。
- `project` 是 Cypher 执行参数，用来选择目标 Neo4j database；不再作为节点属性写入。
- 一个 Cypher 查询只访问一个 project database。工具层可以 fan-out 多个 project，但每次实际执行仍是单库查询。
- 配置必须同时支持两种部署：Community 本地多进程多端口，Enterprise 单进程多 database。
- 不引入 `GraphPatch` 或 `GraphCommand`。
- 不迁移 `extractor`。
- 不迁移 `explorer`。
- 扁平化当前 store/source modules。
- 给 source modules 增加统一 trigger 入口。
- 完善 `src` / resolver pointer 逻辑。
- 不再使用 `match_query` 做读前匹配。
- 不再强制中心层统一生成 `MERGE`。
- 每个 source module 自己返回 Cypher 提交语句，Store 只负责顺序执行。

核心边界：

```text
读：
  Workspace.cypher(..., project=project) -> 对应 Neo4j database -> result resolver

query-time 写：
  只有轻量 source module 可以在 Neo4j 查询前刷新事实

extractor：
  第一阶段保持外部模块，通过 Workspace / src handle 访问数据

explorer：
  第一阶段保持外部 agent 模块，通过 storage tools 访问数据
```

## 模块分层

```text
source module
  位于 storage 内部。
  读取原始数据源。
  在查询前刷新轻量结构事实。
  例：fs、text、csv_schema、db_schema。

extractor
  第一阶段不迁移。
  通过 Workspace.cypher() 读图，通过 src handle 回源。
  例：column stats、topk、sample、AI summary、overlap、fk validate。

explorer
  第一阶段不迁移。
  通过 agent tools 访问 storage。
```

当前只重构 source module。extractor 和 explorer 的访问方式保持不变。

## 建议结构

```text
storage/
  workspace.py          # cypher、trigger 入口、result resolver
  neo4j/
    graph.py            # Neo4j 连接与执行
    instances.py        # 本地多实例运行时管理
  query_inspector.py    # query trigger 的轻量解析
  triggers.py           # TriggerEvent 和 TriggerRouter
  modules/
    base.py
    source/
      fs.py
      csv_schema.py
      db_schema.py
```

`extractor/` 和 `explorer/` 暂不移动。后续如果要统一，再单独设计第二阶段。

## Trigger

`TriggerEvent` 描述 source module 为什么运行。

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

第一阶段实现：

```text
query
  Workspace.cypher() 前触发。
  只允许轻量 source module 运行。
```

先记录但暂不实现：

```text
manual
source_changed
schedule
index_build
dependency_invalidated
cache_miss
policy
startup
shutdown
event / CDC
agent_task
```

其中 `agent_task` 只在未来迁移 explorer 时考虑。

## Source Module 协议

每个 source module 自己声明何时运行。

```python
class StorageModule:
    id: str
    kind: str = "source"

    def wants(self, event: TriggerEvent) -> bool:
        return False

    def cypher_statements(self) -> list[CypherStatement]:
        return []

    def resolve_pointer(self, kind: str, payload: str, *, node: dict | None = None):
        return None
```

规则：

- module 不 import 同级 source module。
- module 不直接调用其他 module。
- module 只依赖 base protocol、ModuleContext、source adapter 和 storage 公共 helper。
- module 之间通过 Neo4j 事实和 `src` handle 通信。
- TriggerRouter 负责选择模块。

```text
TriggerRouter 选中 module
  -> module 生成 CypherStatement
  -> Store 按顺序执行这些 Cypher
  -> Neo4j 执行原始查询
```

## Query Path

```text
Workspace.cypher(query, project=project)
  -> 按 project 选择 Store / Neo4j database
  -> parse query
  -> TriggerEvent(type="query")
  -> TriggerRouter 选择轻量 source modules
  -> 被选中的模块返回 CypherStatement
  -> Store 执行模块声明的 Cypher 写入
  -> Neo4j 执行原始 query
  -> ResultResolver 替换返回结果里的 pointer
```

工具层语法：

```text
project::pattern
  -> 整条 tool 查询路由到该 project

pattern
  -> tool 在当前打开的多个 project 上分别执行，再合并展示结果
```

这不是节点属性过滤。模块生成的节点不写 `project` 属性，节点身份只在单个
project database 内成立。

## Neo4j 部署模式

```text
Community / 本地模式
  每个 project 一个 Neo4j 进程、一个 data 目录、一个 bolt 端口。
  graph.uri 不同，graph.database 都是 neo4j。
  storage.neo4j.instances 负责 start / stop / status。

Enterprise / 单实例模式
  一个 Neo4j 进程，多个 database。
  graph.uri 相同，graph.database 不同。
  不需要本地多进程管理脚本。
```

两种模式对 storage 透明，因为 Store 只接收 `GraphConfig(uri, database,
user, password)`。区别只在部署配置，不进入 source module。

query-time 允许：

- file / dir 投影
- text 轻量元信息投影
- CSV / TSV schema 投影
- SQLite schema 投影
- `src` pointer 刷新
- 小型轻量 source 属性

query-time 禁止：

- 全表扫描
- cardinality / topk / profile
- LLM summary
- overlap 检测
- embedding 生成
- agent explorer

## Resolver Pointer 与 src

resolver pointer 在 Neo4j 中只是普通字符串，只在查询返回后解释。

目标格式：

```text
<pontis:project:module:kind:payload>
```

示例：

```text
<pontis:california_schools:fs:src:README.md>
<pontis:california_schools:db_schema:src:california.sqlite::table::satscores>
```

解析：

```text
Neo4j row
  -> ResultResolver 发现 pointer 字符串
  -> ModuleRegistry.get(project, module)
  -> module.resolve_pointer(kind, payload, node=node)
  -> 用返回的 Python 对象替换字符串
```

要求：

- pointer 必须带 project，避免多 project 下 module 路由不清。
- pointer 里的 project 只用于 result resolver 回到正确 module/source，不参与 Neo4j 查询匹配。

规则：

- pointer payload 归 module 私有。
- pointer 不参与 identity、delete、index、vector search 或 Cypher 语义。
- `src` 应返回 lazy handle，不应立即打开大文件或加载整表。

## 非本阶段内容

extractor 保持外部模块，但可以继续通过 storage 提供的 `src` 回源：

```text
extractor
  -> Workspace.cypher(...)
  -> RETURN n.src AS src
  -> ResultResolver 生成 SrcHandle
  -> extractor 使用 SrcHandle 读取源数据
```

后续如果要迁移 extractor，再考虑：

- manual / schedule / source_changed trigger
- extractor dependency
- extractor 写入 helper
- stale / freshness

explorer 保持现状。它已经通过 tool 封装访问 storage，不在第一阶段调整。

## 实施步骤

1. 增加 `storage/triggers.py`，定义 `TriggerEvent` 和 `TriggerRouter`。
2. 给 source module 增加 `wants(event)`。
3. 让 `Workspace.cypher()` 的 query-time module 选择逻辑走 `TriggerRouter`。
4. 将 `Store.publish_modules(...)` 从中心合并改为执行模块自己的 Cypher 提交。
5. 将 resolver pointer 迁移为 `<pontis:project:module:kind:payload>`。
6. `parse_pointer(...)` 只解析带 project 的 pointer。
7. 用 `ModuleRegistry.resolve_pointer(...)` 或等价逻辑处理 project-aware 路由。
8. 更新文档和 storage README。

## 非目标

- 第一阶段不做 `GraphPatch`。
- 第一阶段不做 `GraphCommand`。
- 第一阶段不迁移 extractor。
- 第一阶段不迁移 explorer。
- 不实现分布式 Cypher。
- 不用 JSON API 复刻 Cypher / SQL。
- 不在 Neo4j 中存 Python 对象。
- pointer 不参与查询语义、identity 或删除。
- Neo4j 查询执行中途不运行 module。
- 第一阶段不引入 Kafka / Pulsar。
