# storage 设计守则

## 根原则

- 本目录是在实现一个 Cypher 图引擎，不是在实现 ref/name/path 实体解析器。
- `workspace.cypher(...)` 是 storage 对外唯一入口；外界任何对于 storage 的访问都必须通过 `workspace.cypher(...)`。
- 外部代码不得调用 `workspace._*`、`store._*`、`workspace.materialize(...)`，也不得读取 `workspace.project_path`、`workspace.index_root`、`workspace.pontis_exists` 这类 storage 状态属性。
- 实体匹配、实体创建、实体更新、实体删除只能通过图语义和 Cypher 执行路径发生。
- `storage` 根目录只放通用图逻辑，不放 project source 类型逻辑，也不放实体类型逻辑。
- `ref`、`name`、`path` 可以作为普通属性或模块虚属性被 Cypher 查询，但 storage / workspace 不把它们当入口、不把它们当身份、不解析它们。

## 实体字段协议

- storage 层统一实体字段只有 `id`、`project`、`labels`、`src` 这四个核心访问面。
- `id` 是唯一实体身份，只有 storage 在创建或载入实体时写入；Cypher `SET` 不允许修改 `id`。
- `labels` 是实体标签列表，也是 Cypher label 匹配的来源；没有外部 `label`，没有内部/外部两套标签名。
- `project` 是项目归属，由 workspace/store 运行时提供；外部不通过 `SET` 修改。
- `src` 是只读虚属性端口；它不默认出现在实体 dict 中，只有 `RETURN n.src AS src` 时才返回。
- `name`、`ref`、`path`、`row_count`、`column_count`、`from_table` 等都是普通属性或模块虚属性，不参与 storage 核心身份判定。
- 没有 `eid`、没有 `_eid`、没有 `_id` 作为实体属性；没有 `label`、没有 `_labels` 作为实体属性；没有 `_aliases`。
- 下划线字段只能是局部实现细节，不属于 Cypher 实体契约，不作为普通实体属性返回。

## Cypher 引擎边界

- 写操作必须由 Cypher 匹配到的实体或 Cypher 创建出来的实体触发。
- storage 核心不得在 `CREATE`、`SET`、`DELETE` 中通过 `name/ref/path` 隐式定位实体。
- storage 核心不得解析字符串 ref，例如 `a--b--c`、`db/table/column`、文件路径、表列拼接名。
- storage 核心不得维护 `_name_index`、`ref_index`、`path_index` 或任何业务字段索引。
- 如果外层需要用文件路径、DB 表名、列名、业务名做选择，应写成 Cypher 属性查询；storage 核心只按普通属性求值。
- 模块可以提供 `name/path/table/column` 等虚属性，但这些属性的计算、匹配和 source 语义必须留在对应 store module 内部。

## 主图与模块

- `storage/backends/` 只负责图数据持久化，不理解 source/project/module 语义。
- `storage/store.py` 是唯一主图实现，负责通用图语义、索引、边、模块调度。
- `stores/` 顶层文件代表 project type 模块。
- 主图写入统一落到主图持久化存储，不写回模块子图。
- 模块负责产出虚实体/虚边、`src` 绑定、匹配规则。
- 模块虚实体使用同一套实体字段协议：`id`、`project`、`labels`，以及普通虚属性。
- 模块匹配不靠全局唯一实体键；模块可以自行定义匹配规则。
- 模块匹配规则必须通过 `match_query(vnode)` 返回声明式 Cypher 查询表达，不把 source 匹配逻辑散落到 storage 核心。
- 中心层只负责执行模块返回的 Cypher，并统一处理 `0/1/N` 匹配结果。

## Cypher 写路径物化

- 所有物化都由 Cypher 写路径触发。
- `Workspace.materialize(...)` 不存在公开入口语义；tool / extractor 层不得手动调用物化。
- tool / extractor 层如需更新、删除、连边，必须发 Cypher 写语句。
- Cypher 写路径在执行 `SET`、`DELETE`、`CREATE edge` 前，先用 merged read view 解析 MATCH 命中的虚实体，再在内部完成物化。
- 物化匹配已有持久实体时，只使用 module `match_query(vnode)` 返回的 Cypher；中心层不解析 `name/ref/path` 作为身份。
- 物化时不是只物化当前节点，而是沿虚链闭包一起物化相关实体。
- 中心层物化时应同时补齐虚链上的边。
- 物化时虚属性优先级高于已物化内容；`labels` 取并集。

## 读路径

- `Workspace` 读查询时使用 merged read view，把主图和模块子图合并成统一只读视图。
- merged read view 中，核心只特殊处理 `id/project/labels/src`；模块虚属性按普通 Cypher 属性暴露。
- merged read view 会执行 module `match_query(vnode)`，唯一命中时把虚属性合并到对应持久实体；多命中不合并。
- `n.src` 在 merged read view 中优先由模块提供。
- `n.id`、`n.project`、`n.labels`、`n.src` 是外层可稳定依赖的核心访问面；其他字段没有核心特殊语义。

## 图结构表达

- 结构关系应该用边表达，不应该拍平成属性。
- 例如 `fk` 应该通过连接列、表的边来表达，而不是靠 `from_table/to_table` 这类属性当主建模。
- 对外展示时，同一结构信息不要同时出现在静态字段和相邻实体里。
- 对复杂结构实体，后续应让模块提供更强的结构匹配 query，而不是把结构压平。

## 旧接口与外层使用方式

- 旧的 `open_db/open_file/resolve_data_path/data_exists` 顶层接口已经废弃并删除，不再作为 storage 主线。
- tool 层和 extractor 层的数据访问应通过 `cypher + n.src` 走通，而不是重新发现实体。
- tool 层和 extractor 层不得调用 storage 私有方法完成写入；写入必须走 `workspace.cypher(...)`。
- tool 层和 extractor 层如需物理文件路径，也必须通过 `RETURN n.src AS src` 获取端口后读取，不能读取 `workspace.project_path`。
- extractor 层应主要负责派生知识，例如 stats / sample / topk / overlap / AI summary；不要再承担 DB schema 基础结构建模。

## 测试

- storage 综合测试需要持续覆盖 CRUD、Cypher、虚实体、`src`、模块匹配、中心物化、持久化、并发、跨项目边。
- storage 相关测试必须走 `workspace.cypher(...)` 或 store 的 `cypher(...)` 公共入口，不直接调用内部方法验证行为。

# TODO

- source 更新同步应由 storage 维护，不交给外部手动清理，也不在 extractor 里做。
- source module 需要为可物化虚实体提供稳定来源信息，例如 source module、source ref、source fingerprint、source state、source seen time。
- source 同步流程应基于当前虚实体 snapshot 与已物化 source-owned 实体对比：新增实体物化，变更实体更新 source metadata，消失实体标记 stale。
- 默认策略应是软删除：source 消失时先设置 stale 状态，不直接物理删除节点。
- 实体所有权需要区分 source-owned、user-owned、mixed；用户或 agent 补充过的知识内容不能被 source 同步自动删除。
- 纯 source 派生实体可以更激进清理，例如 fk、index、临时统计节点；表、列、文档等容易承载用户知识的实体应更保守。
- 后续应提供显式 GC 入口，例如 `Workspace.gc_source(..., dry_run=True)`，只清理无用户内容、无跨项目引用、无手工边的 stale source-owned 实体。
- Cypher 或 tool 展示层后续需要明确 stale 行为：默认是否过滤 stale、如何显式查询 stale、如何提示用户源实体已失效。
- 为可物化实体追加最小 provenance 字段；这些字段是 source lifecycle 实现细节，不改变实体公共字段协议。
- 定义 source-owned 字段集合：source module 需要声明哪些字段由 source 管理，例如 `path`、`type`、`nullable`、`primary_key`、source fingerprint。
- 定义 user-owned 字段集合：用户知识字段不应被 source sync 覆盖，例如 `brief`、`detail`、`note`、`rule`、`decision`、`examples`。
- 为 `StoreModule` 增加 snapshot 约定：module 应能产出当前 source 可见的虚实体列表、虚边列表、fingerprint、ownership、source-owned keys。
- 实现 `Workspace.sync_source(...)`：统一执行 source snapshot、匹配已物化实体、更新 active 实体、标记 stale 实体。
- `sync_source` 默认只更新 source-owned 字段，不删除用户字段，不删除用户手工创建的实体。
- `sync_source` 对新增 source 实体可以选择只记录为虚实体，也可以按参数物化；默认策略应保守，避免一次扫描把大 source 全量写入图。
- `sync_source` 对变更实体应比较 source fingerprint，fingerprint 未变时跳过写入，减少版本号抖动和无意义 commit。
- source rename / schema rename 在第一阶段不应自动当作同一实体合并；默认策略是旧 source ref 变 stale，新 source ref 作为新实体出现。
- `column_count`、`table_count`、`view_count` 这类 source schema 事实应来自当前 active source snapshot，不应通过数主图里残留的 stale 实体得到。
- 实现 `Workspace.gc_source(..., dry_run=True)`；GC 默认必须 dry-run，真正删除需要显式参数。
- 为 source lifecycle 增加测试：source 新增实体后能发现或物化，source 删除实体后标记 stale，source 恢复后重新 active。
- 为 mixed 实体增加测试：用户补充过 `detail` 后，source 删除只标记 stale，不删除用户字段。
- 为 GC 增加测试：纯 source-owned stale 实体可清理，mixed stale 实体不可清理，带跨项目引用的 stale 实体不可清理。
- 为 match 冲突增加测试：一个虚实体匹配多个持久实体时不自动合并，sync/materialize 应报告冲突。
- 把 DB schema 产出迁到 storage module：SQLite table、column、fk、index、view 应作为虚实体/虚边由 module 产出，而不是由 extractor 直接写入主图。
- DB schema module 应复用 `n.src` 或模块内部连接能力读取 schema，不应把 DB 访问逻辑写入 `Store` 主体。
- DB schema module 应把结构关系表达为边，例如 database--table、table--column、column--fk、fk--column，而不是只拍平成属性。
- DB schema module 不应默认计算昂贵 profile，例如全表 row_count、高基数统计、topk；这些应由 extractor/profile job 或显式 include 触发。
- 为批量 schema 物化补 transaction 或 batch upsert，避免几千个 table/column/fk 节点逐条 commit。
- GraphBackend 后续需要批量接口，例如 `write_nodes`、`delete_nodes`、`add_edges`、事务 begin/commit/rollback。
- 当前 `iter_virtual_nodes()` 是全量扫描接口，大 source 下会慢；后续需要懒加载、缓存、分页或 query-aware discovery。
- `Workspace.cypher(... RETURN ...)` 当前仍会频繁新建 `MergedStoreView` 并触发模块全量虚图构建；后续需要最小缓存或懒加载。
- source module 应能声明虚属性成本，例如 cheap、expensive、async；Cypher 默认只取 cheap 元数据，昂贵 profile 不应隐式触发。
- `CypherExecutor` 当前仍直接依赖 store 私有结构；后续应抽最小 `GraphView` 协议，让持久 Store 和 MergedStoreView 通过稳定接口被 Cypher 执行器消费。
- 引入正式 `SourceIdentity` / `VirtualRef` 类型是中长期方向，用结构化 identity 替代 `name`、`path`、字符串 ref 的隐式约定。
- 对外仍可保留字符串 ref 作为 agent-facing 操作面，但 storage 内部匹配和同步不应长期依赖字符串拼接。
- Cypher 作为 storage 公共 API 后续可能需要扩展 `MERGE`、`ORDER BY`、`LIMIT`、聚合、路径返回、批量删除；原则是 Cypher 不足时补 Cypher，不回退到外部私有接口。
- `Store` 仍承担 backend 委托、解析、模块调度、fallback、边管理、cross-project、Cypher 入口等多种中心职责；中长期应拆成更小的内部组件。
- 多 source 挂到同一个 project 是更大的架构议题，需要处理 source 命名空间、冲突优先级、跨 source entity resolution、统一展示。
