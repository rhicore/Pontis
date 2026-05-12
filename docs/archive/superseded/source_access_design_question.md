# Pontis 数据访问层设计问题整理

## 背景

Pontis 当前的核心目标是：

- 以图谱形式组织数据项目中的实体、关系、注释和经验知识
- 让 agent 主要通过图谱理解数据
- 让 tool 负责对外暴露可操作能力

目前图谱层已经基本收口到：

- `workspace.cypher(...)`：图谱查询 / 图谱写入

同时，系统里仍然存在一批“原始数据访问”需求：

- extractor 需要打开数据库 / 文件做抽取
- `query` 工具需要对数据库执行 SQL
- 将来可能有 `grep` / 文本搜索 / 文件遍历工具

这就引出了一个边界设计问题：

**图谱层是否还应该承载数据访问逻辑？如果不承载，如何避免 tool/extractor 重复做实体发现？**

---

## 当前现状

### 1. 图谱层已经做了实体发现

例如对一个 SQLite 数据库，图中已经发现了：

- 数据库文件节点
- table 节点
- column 节点
- fk / rel / overlap / disambig 等语义节点

也就是说，像下面这些定位信息，图谱里其实已经知道了：

- 某个 table 属于哪个数据库文件
- 某个 column 属于哪张表
- 某个实体的物理名字是什么

### 2. 当前原始数据访问还是 path-first

现在 `Workspace` 还暴露着一些历史接口：

- `open_db(...)`
- `open_file(...)`
- `resolve_data_path(...)`
- `data_exists(...)`

这些接口的问题是：

- 它们不是图谱接口
- 它们是 path-first，而不是 graph-first
- 未来接多种 source 时边界不清楚

### 3. 如果完全把数据访问逻辑挪到外部，会出现重复发现

如果 tool/extractor 不依赖图谱返回的定位信息，而是自己重新找：

- 这个 table 属于哪个 db
- 这个 column 属于哪张表
- 这个 source 对应哪个真实路径

那就等于：

- 图谱层发现了一遍
- tool/extractor 又发现了一遍

这明显不理想。

---

## 核心设计问题

当前真正的问题可以归纳成一句话：

> 在“知识图谱优先”的架构下，原始数据访问逻辑到底应该放在哪里，以及图谱层应该提供到什么程度？

更细一点，有几个子问题：

### 问题 1：是否需要在 `Workspace` / `storage` 层增加统一的数据访问入口？

候选方向：

- 保持 `workspace` 只有 `cypher`
- 或新增类似 `src(...)` / `source(...)` 的统一入口

### 问题 2：如果增加数据访问入口，它的输入语法应该是什么？

当前倾向：

- `tool` 层保留 `ref`
- `storage / workspace` 层只认 `cypher`

所以如果有 `src`，它更合理的形态应是：

- `workspace.src(cypher_query, params=None)`

而不是：

- `workspace.src(ref)`

### 问题 3：`src` 层应该提供“高阶访问能力”还是“原生端口绑定”？

两种思路：

#### 方案 A：高阶能力抽象

例如提供：

- `query_sql`
- `read_text`
- `grep`
- `read_rows`
- `schema`

问题：

- 可能会把数据层做得过重
- 会重复封装系统 / 标准库 / 官方驱动已经有的能力

#### 方案 B：原生端口绑定

`src` 只负责把图中的 source 节点绑定到现实世界里已有的原生端口，例如：

- SQLite 文件节点：
  - 文件路径端口
  - `open(...)`
  - `sqlite3.connect(...)`

- 文本文件节点：
  - 文件路径端口
  - `open(...)`

- 远程数据库节点：
  - 官方 driver connect 端口

这个方案更克制，也更贴近当前需求。

### 问题 4：一个实体是否可能对应多个端口？

答案显然是“会”。

例如一个 SQLite 文件节点：

- 既是文件
- 又是数据库

所以它可能同时支持：

- `path`
- `open`
- `db_connect`

因此设计上不能假设：

- 一个 source 节点只有一个固定端口

### 问题 5：如果不做 `src`，是不是把数据访问逻辑放到 tool 层更合适？

当前一个很强的反向观点是：

- Pontis 的核心目标是知识图谱，不是通用数据访问框架
- 原始数据访问逻辑完全可以放到：
  - tool 层
  - extractor 的 helper 层

图谱层只需要返回足够的定位信息（locator），例如：

- source_ref
- physical_name
- table_name
- path
- object_kind

然后 tool/extractor 根据这些 locator 去调用原生端口。

这个思路的优点是：

- graph 层保持轻
- 不用在 `Workspace` 再加一个重的 source runtime
- 避免 over-engineering

---

## 当前我最纠结的点

我现在最纠结的，不是技术上能不能做，而是**边界到底该落在哪里**：

### 纠结点 A

如果把数据访问逻辑完全放到外部：

- tool/extractor 会不会重复做实体发现？
- 图谱已经知道 table / column / source 的定位，为什么外层还要自己再找一遍？

### 纠结点 B

如果把这层逻辑塞回 `Workspace` / `storage`：

- 会不会让 `storage` 从“知识图谱层”膨胀成“图谱 + 数据访问平台”？
- 会不会引入新的过度抽象？

### 纠结点 C

如果设计 `src`：

- 它究竟应该只是一个很薄的“原生端口绑定层”
- 还是应该成为一套统一的 source access framework？

目前更倾向于前者，但还没有最后定。

---

## 候选方案总结

## 方案 1：不做 `src`，数据访问逻辑放在 tool / extractor 层

### 做法

- `workspace` 继续只保留 `cypher`
- 图谱层负责返回实体的 locator 信息
- tool / extractor 根据 locator 自己调用：
  - `sqlite3.connect`
  - `open`
  - `csv/json`
  - 远程 driver

### 优点

- 边界干净
- storage 不膨胀
- 更符合“知识图谱优先”定位

### 问题

- 需要先设计 locator 协议
- tool/extractor 仍然要分别消费 locator
- 可能会在多个地方分散一些原生访问逻辑

## 方案 2：做一个很薄的 `workspace.src(cypher)`，只负责端口绑定

### 做法

- `workspace.src(...)` 只吃 cypher
- 返回 source 节点对应的端口集合
- 不内置 `grep/query/schema` 等高阶逻辑
- 只返回原生端口，例如：
  - `path`
  - `open`
  - `db_connect`
  - `stream`
  - `native`

### 优点

- 避免 tool/extractor 重复做绑定逻辑
- 保留 graph-first 的体验
- 仍然比较克制

### 问题

- 还是会把 `Workspace` 往 source 平台方向推一步
- 需要决定 `src` 和 locator 的关系

## 方案 3：做一个更完整的统一 source 访问框架

### 做法

- `workspace.src(...)` 返回统一 handle
- handle 内建：
  - `query`
  - `read_text`
  - `grep`
  - `schema`
  - `read_rows`

### 优点

- 上层体验最统一

### 问题

- 明显过重
- 很容易过度抽象
- 不符合当前“尽量简化数据层”的目标

当前基本可以判定：

**方案 3 不合适。**

---

## 当前比较想听别人意见的关键问题

1. 对于一个“知识图谱优先”的系统，原始数据访问层是否应该存在于 `Workspace/storage` 层？
2. 如果存在，这层应不应该只做“原生端口绑定”，而不做高阶数据访问抽象？
3. 如果不存在，图谱层应该至少返回什么 locator 信息，才能避免 tool/extractor 重复发现实体？
4. 对于一个 source 节点支持多个端口（例如 SQLite 同时是文件和数据库）这种情况，最好在哪里表达？
5. 从长期演进看，哪种边界最不容易把系统做得过于拥挤？

---

## 当前个人倾向

我现在的倾向是：

- 不做重的 source runtime
- 如果做 `src`，也只做很薄的端口绑定
- 或者干脆不做 `src`，直接让图谱返回 locator，tool/extractor 自己调用原生端口

但我还没有完全确定这条边界该落在哪。
