# Pontis 系统审计：BIRD Extract、图谱与 Agent

> 初次审计：2026-07-13
>
> 当前行为核对：2026-07-15
> 范围：当前代码、11 个 BIRD dev project、BIRD runtime

现行入口和契约见 [文档索引](../README.md)。本文只描述当前代码行为，不再保留已经修复的旧工具行为或旧 `overlap/value_domain` 实体口径。

## 1. 当前结论

- `pontis.yml` 中 11 个 BIRD project 均显式命名，`source.type=sqlite`，并直接指向唯一 SQLite 文件。
- 数据库节点是唯一 source 和图导航根；BIRD 不创建 FS、CSV 或文件节点。
- description CSV 只负责把 official metadata 写入实际 `table/col`。
- runtime 只暴露 `find`、`meta`、`query`。
- BIRD 加载紧凑数据库 ontology，不加载文件系统或大型 schema 导航概念。
- `hints` 只在 `meta` 中展示，不进入 semantic embedding 或 BM25。
- 静态 extract、official metadata、agent explorer、embedding 和 readiness 使用同一 project 配置与 source。

## 2. BIRD Extract 链路

```text
SQLiteSchemaModule
  -> db / table / col / fk

STATIC
  -> db_column_stats
  -> db_fk_validate
  -> db_column_domain

OFFICIAL
  -> bird_official_description_extract
  -> database_description CSV 写入 table/col official metadata

AGENT
  -> schema_prepare
  -> column_domain_review
  -> disambiguate
  -> bird_profile
  -> description_audit
  -> readme

EMBEDDING
  -> semantic_embedding

READINESS
  -> 唯一 db source、表列、brief/detail、official metadata、README
  -> 所有可检索实体的 embedding 内容 hash、模型和维度均为当前值
```

BIRD 的 `db_column_domain` 使用精确 SQL 值匹配和单个 `value_overlap` filter；不启用 Spider 的 domain profile、shape、key-like、name-token 或 pattern-table 策略。两套数据集共用同一模块化执行器，但配置彼此独立。

## 3. 当前实体与导航

核心拓扑：

```text
db
├── table / view
│   └── col
└── knowledge
```

语义层可能包含 `fk`、`rel`、`column_domain`、`disambig` 和 `knowledge`。它们都是普通实体：工具不会为某一类型添加中括号、端点前缀或专用排版。

`find` 返回从唯一 `db` source 回溯得到的完整 ref。列实体固定使用结构归属路径 `db -> table/view -> col`，不会因为同时邻接关系实体而改变坐标。同名实体分别返回，例如：

```text
california_schools.sqlite:db/frpm:table/CDSCode:col
california_schools.sqlite:db/schools:table/CDSCode:col
```

返回 ref 可直接交给 `meta`。`meta` 展示实体自身公开 metadata、邻接类型计数和普通邻居；`neighbor_label` 用于定向分页。`query` 对当前数据库执行只读 SQL，descendant ref 只选择所属数据库，不限制 SQL 的表范围。

## 4. Metadata 规则

存储规则是：

> 能由实体间边关系唯一推出的信息，不作为公开实体 metadata 重复持久化。

因此：

- db 的表/视图数量由邻接边计算；
- table/view 的列数量由邻接边计算；
- fk 的 source/target 端点由带 role 的边表达；
- column_domain 的成员列和所属表由边表达；
- disambig 的成员由边表达。

仍保留不能从边推出的事实：表行数、主键、official description、列统计和值样例、关系质量指标、候选筛选证据、review status、brief/detail/hints。

以下字段只服务于寻址、数据库访问或幂等写入，并对 Agent 隐藏：

```text
_ref / _db_ref / _db_connect / embedding / embedding_hash
```

`col.table_name`、FK 内部端点 cache 等运行字段只有在消费者改为从带角色边读取后才能物理删除，不能仅按“公开 metadata 去冗余”直接删掉。

## 5. Agent 当前行为

BIRD runtime 的 prompt 由以下部分组成：

```text
base -> tool -> database_ontology -> sql -> bird
     -> effort -> guardrail -> project -> readme
```

数据库 ontology 只说明 `db/table/view/col` 和普通语义实体。工具工作流从 question/evidence 中的业务概念开始定向检索，不再提示 BIRD agent 先探索 `schema_landscape/table_group`、文件、CSV 或 JSON。

Agent 每题最多 40 次工具调用，其中 `query` 最多 24 次；相同工具名和规范化参数复用已有结果。BIRD 只使用 round、工具额度和探索完成度 guard，不使用面向历史 strict 指标的文本 SQL 拦截。候选 SQL 会实际执行，最终以 result-based business-correct evaluator 为主判断业务等价。

## 6. 验收边界

本轮提交前的确定性验收结果：

- 11/11 个 BIRD project 均可解析唯一且存在的 SQLite source；
- static 与 agent pipeline 注册顺序和 module 名称核对通过；
- BIRD column-domain、runtime policy 和 prompt 回归 8/8 通过；
- 相关 Python package 编译通过，`git diff --check` 无格式错误。

column-domain 重构提交还对 11/11 个 BIRD 库执行过静态 pipeline 验收。本轮 prompt/docs 整理没有重新写图，也没有重新调用 LLM。

这不等同于重新调用 LLM 生成 11 库全部 detail。完整重跑时仍需执行 agent explorer 和 embedding，再由 readiness 检查保证所有必需实体和检索向量完整。

## 7. 仍可继续优化

1. source-rooted ref 目前仍可能按输出实体逐个查询路径，宽结果集存在 N+1 成本；
2. 运行期内部 cache 可在 typed/role edge 消费者迁移完成后继续归一化；
3. 对 detail 中“最大/最小/最多/最常见/唯一/全部”等高风险统计词增加事实一致性 gate。
