# Pontis Storage / Source Boundary 重构计划

## 目标

这份文档重新定义当前代码库里 `storage`、`workspace`、`tool`、`extractor` 的边界。

核心目标只有两条：

1. **图谱访问统一收口到 `workspace.cypher(...)`**
2. **原始数据访问统一收口到 `workspace.src(...)`**

其中：

- `cypher` 只负责图谱查询与图谱写入
- `src` 只负责把图中的 source 节点绑定到系统/标准库/官方驱动已经提供好的**原生访问端口**

`src` 不负责：

- 元数据解释
- schema 推理
- grep / query / read_rows 这类高阶逻辑

这些都应交给上层 tool / extractor。

---

## 一、总设计原则

## 1.1 Workspace 只保留两类公共能力

最终 `Workspace` 的核心公共接口应收口为：

- `workspace.cypher(query, params=None, project=None)`
- `workspace.src(cypher_query, params=None)`

其中：

- `cypher` 是图谱接口
- `src` 是 source binding 接口

其他顶层 path-first 接口：

- `open_db(...)`
- `open_file(...)`
- `resolve_data_path(...)`
- `data_exists(...)`

都应逐步降级为兼容层，最终退出公共 API。

## 1.2 ref 只留在 tool 层

这条边界必须保持硬：

- `tool` 层可以继续使用 `ref`
- `storage / workspace` 层不认 `ref`
- `workspace.src(...)` 的输入也只认 `cypher`

这意味着：

- `meta / glob / update_meta / add_edge / delete / create_entity`
  可以在 tool 层继续使用 `ref`
- 但 `workspace` 不应该再出现：
  - `source(ref)`
  - `open_db(rel_path)`
  - `open_file(rel_path)`

## 1.3 src 是“原生端口绑定”，不是新的高阶抽象层

`src` 的职责不是重新发明一套：

- `query()`
- `grep()`
- `read_rows()`
- `schema()`

而是：

**根据图谱中的 source 节点，返回这个实体在现实世界里可用的原生访问端口。**

例如：

- SQLite 文件节点：
  - 可以返回 `sqlite3.connect(...)`
  - 也可以返回操作系统文件路径

- 文本文件节点：
  - 可以返回 `open(path, ...)`
  - 也可以返回文件路径本身

- 远程 Postgres source 节点：
  - 返回官方驱动连接端口

也就是说，`src` 不提供“业务能力”，只提供“原生入口”。

---

## 二、当前边界判断

## 2.1 已经完成的部分

当前已经完成的收口包括：

1. `tool` 主流程已经基本统一走 `workspace.cypher(...)`
   - `meta`
   - `glob`
   - `create_entity`
   - `update_meta`
   - `add_edge`
   - `delete`
   - `resolve`

2. 之前几个高风险写入 bug 已修复
   - `add_edge` 错误依赖公开 `id`
   - `update_meta` 错误依赖公开 `id`
   - `create_entity` 只按 `name` 判重导致 `rel/overlap` 冲突

3. guardrail 访问图谱时已经基本走 cypher

这些不再是当前重构的主阻塞点。

## 2.2 当前真正未收口的问题

### 问题 A：Workspace 仍暴露 path-first 的 source 访问接口

当前 `Workspace` 仍暴露：

- `open_db(...)`
- `open_file(...)`
- `resolve_data_path(...)`
- `data_exists(...)`

这些接口的问题不是不能工作，而是：

- 它们属于 source 访问，不属于图谱访问
- 它们是 path-first，而不是 graph-first
- 将来接多种数据源时，扩展点不清楚

### 问题 B：source 访问还没有统一的 binding 模型

现在 extractor / tool 访问原始数据时，大致是：

- 先拿一个相对路径
- 再调用 `workspace.open_db/open_file`

这意味着 source 节点和原始访问之间没有显式的“绑定层”。

### 问题 C：一个 source 节点可能对应多个原生端口，但当前模型表达不了

这点很关键。

例如一个 SQLite 文件节点：

- 既是一个普通文件
- 又是一个数据库

所以它天然可能暴露多个端口：

- 操作系统文件端口
- SQLite 连接端口

这说明 source binding 不能设计成：

- “一个实体只对应一个固定 handle”

而应该设计成：

- “一个实体返回一组可用端口”

---

## 三、src 的正确定位

## 3.1 src 的输入

`workspace.src(...)` 只接受：

- `cypher query`
- `params`

例如：

```python
workspace.src('MATCH (n:file {name: "financial.sqlite"}) RETURN n')
```

而不是：

```python
workspace.src("financial.sqlite")
```

因为后者把 `ref` 语义泄露到了 storage 层。

## 3.2 src 的输出

`workspace.src(...)` 不直接返回一个数据库连接或文件对象，而应返回：

- `SourceBindingResult`

它表示：

- 0 个命中
- 1 个命中
- 多个命中

并允许上层：

- `.one()`
- `.first()`
- `.all()`
- 迭代

这样它天然兼容 cypher 的 0 / 1 / N 语义。

## 3.3 src 的本质

`src` 的本质是：

**把图谱里的 source 节点，绑定成它所支持的一组原生访问端口。**

注意这里是“一组端口”，不是“唯一端口”。

---

## 四、端口模型

## 4.1 端口不是按实体标签写死的

不要设计成：

- `file:text` 只有 `read`
- `file:db` 只有 `query`

因为现实并不是这样。

一个实体可能同时支持多种端口。

例如：

### SQLite 文件

它既可以提供：

- 文件路径端口
- `open(...)` 文件端口
- `sqlite3.connect(...)` 连接端口

### CSV 文件

它既可以提供：

- 文件路径端口
- `open(...)` 文件端口

上层可以自己选择：

- 用 `grep`
- 用 `csv`
- 用 `pandas`

### 本地目录

它可以提供：

- 路径端口
- `pathlib.Path`
- `os.walk` 友好端口

所以设计上不应写死“节点类型 -> 唯一端口”，而应表达为：

- “节点类型 -> 若干可用端口”

## 4.2 推荐端口集合

建议把端口定义成**原生访问入口**，而不是高阶业务方法。

第一版最值得支持的端口：

### 1. `path`

返回本地可访问路径。

适用：

- 本地文件
- 本地目录
- 本地 SQLite / DuckDB 文件

用途：

- 直接复用 `grep / rg / cat / sed / awk`

### 2. `open`

返回标准文件打开端口。

适用：

- 文本文件
- 二进制文件
- 本地文件类 source

对应的是：

- `open(path, ...)`

### 3. `db_connect`

返回数据库原生连接端口。

适用：

- SQLite
- PostgreSQL
- MySQL
- ClickHouse
- Snowflake
- 其他 SQL 数据源

对应的是：

- `sqlite3.connect(...)`
- `psycopg.connect(...)`
- `pymysql.connect(...)`
- 其他官方驱动

### 4. `stream`

返回对象流 / 远程内容流。

适用：

- S3 / OSS / GCS
- 大对象文件
- 远程文件 source

### 5. `native`

返回该 source 的官方客户端对象。

适用：

- 向量数据库
- NoSQL
- 云平台 SDK
- 编排系统客户端

这是一种兜底端口，但仍然是“原生端口”，不是业务抽象。

## 4.3 不建议内置为 src 端口的东西

以下不应作为 `src` 的一等职责：

- `grep`
- `query_sql`
- `read_rows`
- `schema`
- `describe_table`

原因：

- 它们都属于上层逻辑
- 都可以基于原生端口实现
- 放进 `src` 只会让数据层再次膨胀

---

## 五、不同类型实体的建议

## 5.1 `file:db`

例如：

- sqlite 文件
- duckdb 文件

建议暴露端口：

- `path`
- `open`
- `db_connect`

## 5.2 远程 SQL 数据源节点

例如：

- postgres source
- clickhouse source
- snowflake source

建议暴露端口：

- `db_connect`
- 可选 `native`

通常不暴露 `path`。

## 5.3 普通文本文件

例如：

- `.md`
- `.sql`
- `.yaml`
- `.py`
- `README`

建议暴露端口：

- `path`
- `open`

这样上层就能直接复用：

- `grep`
- `rg`
- `cat`
- 标准库文本读取

## 5.4 CSV / JSON / Parquet 等文件

建议暴露端口：

- `path`
- `open`

不要在 `src` 层直接加：

- `read_csv`
- `read_json`

因为这些都可以在上层根据文件类型自行决定使用：

- `csv`
- `json`
- `pandas`

## 5.5 目录 / bucket prefix

建议暴露端口：

- `path`（本地目录）
- `native`（对象存储前缀句柄）

## 5.6 其他图谱语义节点

例如：

- `table`
- `col`
- `fk`
- `rel`
- `knowledge`

默认不应直接提供 `src` 端口。

如果后面确实需要让某些节点可绑定 source，也应该通过显式规则开放，而不是默认都支持。

---

## 六、对现有代码的影响

## 6.1 extractor

当前 extractor 大量直接使用：

- `workspace.open_db(...)`
- `workspace.open_file(...)`
- `workspace.resolve_data_path(...)`
- `workspace.data_exists(...)`

后续应逐步迁成：

- 先用 cypher 找 source 节点
- 再通过 `workspace.src(...)` 取端口
- 再调用原生访问方式

例如 SQLite：

```python
binding = workspace.src('MATCH (n:file:db {name: $name}) RETURN n', {"name": db_name}).one()
conn = binding.port("db_connect")
```

文本文件：

```python
binding = workspace.src('MATCH (n:file {name: $name}) RETURN n', {"name": file_name}).one()
f = binding.port("open")
```

## 6.2 tool

`query` 这类 tool 后续也应改成：

- tool 层继续处理 `ref`
- 但进入 workspace 后只传 cypher
- 最终拿 `db_connect` 端口

## 6.3 grep

这条设计正是为了让将来的 `grep` 不需要重写。

文本文件类 source 只要暴露：

- `path`
- 或 `open`

上层就可以直接复用：

- 系统 `grep`
- `rg`
- 标准库文本处理

---

## 七、分阶段计划

## 阶段 1：定义 src 结果对象

目标：

- 引入 `workspace.src(cypher_query, params=None)`
- 返回统一的 `SourceBindingResult`

这一步先不删旧接口。

## 阶段 2：实现最小端口集

先只支持：

- `path`
- `open`
- `db_connect`

覆盖：

- 本地文件
- 本地目录
- SQLite / DuckDB

## 阶段 3：让 `query` tool 先切

这是最自然的第一个使用方。

## 阶段 4：迁 extractor

优先迁 DB 类 extractor，再迁文件类 extractor。

## 阶段 5：降级旧接口

将以下接口标为兼容层：

- `open_db`
- `open_file`
- `resolve_data_path`
- `data_exists`

## 阶段 6：最终收口

当 extractor / tool 全部切完后，`Workspace` 顶层只保留：

- `cypher`
- `src`

---

## 八、验收标准

重构完成后，应满足：

1. `storage / workspace` 层只有两类主能力：
   - 图谱查询：`cypher`
   - source 绑定：`src`

2. `src` 只认 cypher，不认 ref

3. `src` 返回的是可用端口集合，而不是高阶业务方法集合

4. 同一个 source 节点允许暴露多个端口

5. `grep`、数据库连接、文件读取都能直接复用系统/标准库/官方驱动，而不是再在 `src` 层重写一遍

---

## 一句话总结

这次重构的目标不是做一个新的“统一数据访问抽象库”，而是：

**让图中的 source 节点，通过 `src` 被绑定到现实世界里已经存在的原生访问端口。**

也就是说：

- 图谱访问：`cypher`
- 原始数据访问：`src`
- `src` 返回端口集合
- 上层自己决定拿哪个端口做 `query / grep / 读取 / 扫描`
