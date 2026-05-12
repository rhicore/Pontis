# Pontis Source Binding 重构计划

## 目标

这份文档只讨论一件事：

**如何把原始数据访问统一成一条清晰的 `source binding` 路径，同时不破坏当前 `storage = 图谱层` 的边界。**

这里的核心前提是：

1. `tool` 层可以继续使用 `ref`
2. `storage / workspace` 层只认 `cypher`
3. `workspace.cypher(...)` 继续作为唯一图谱查询入口
4. 原始数据访问不再散落在 `open_db / open_file / resolve_data_path / data_exists` 这些历史口子里

---

## 一、当前问题

当前 `Workspace` 还暴露了这些接口：

- `open_db(...)`
- `open_file(...)`
- `resolve_data_path(...)`
- `data_exists(...)`

这些接口的问题不是“不能用”，而是：

1. **它们是 source-specific 访问能力，不是图谱能力**
2. **它们挂在 `Workspace` 顶层，会污染 graph API**
3. **它们是 path-first 设计，不是 graph-first 设计**
4. **将来接入多种数据库源时，扩展点不清楚**

同时，完全删掉这些能力也不合理，因为：

- extractor 需要访问原始数据库和文件
- `query` 这类 tool 需要访问数据库
- 后续 `grep` / 大规模 source 扫描也需要统一入口

所以问题不是“要不要保留能力”，而是“这组能力应该如何重组”。

---

## 二、设计原则

### 2.1 图谱层和数据源层分开

`Workspace` 的职责拆成两类：

1. **图谱能力**
   - `workspace.cypher(...)`

2. **数据源绑定能力**
   - `workspace.source(cypher_query, params=None)`

不再保留顶层 path-first 的零散方法作为长期公共 API。

### 2.2 storage 层只认 Cypher

这条边界必须保持硬：

- `tool` 层可以用 `ref`
- `storage / workspace` 层不认 `ref`
- `workspace.source(...)` 的输入也必须是 `cypher`

也就是说：

- `workspace.source("financial.sqlite")` 不应该存在
- `workspace.source('MATCH (n:file {name: "financial.sqlite"}) RETURN n')` 才是目标形态

### 2.3 source binding 允许 0 / 1 / 多个命中

因为 `cypher` 本来就可能返回 0、1 或多个节点，所以不需要拆成：

- `source()`
- `sources()`

一个统一入口就够：

```python
workspace.source(cypher_query, params=None)
```

它返回统一的绑定结果对象，而不是有时返回单个 handle、有时返回列表。

### 2.4 不是所有节点都能绑定为 source

可绑定 source 的节点一般包括：

- `file:db`
- `file:csv`
- `file:json`
- `file:text`
- 未来的远程数据库 source 节点

通常不能直接绑定的节点包括：

- `table`
- `col`
- `fk`
- `rel`
- `knowledge`

如果 `cypher` 返回了不可绑定节点，应返回明确错误。

---

## 三、目标接口

## 3.1 Workspace

新增公共接口：

```python
workspace.source(cypher_query: str, params: dict | None = None)
```

用途：

- 执行一段 cypher
- 从结果行中提取可绑定的 source 节点
- 返回统一的 `SourceResult`

保留：

- `workspace.cypher(...)`

逐步降级为内部兼容：

- `open_db(...)`
- `open_file(...)`
- `resolve_data_path(...)`
- `data_exists(...)`

## 3.2 SourceResult

`workspace.source(...)` 不直接返回单个 handle，而是返回统一结果对象，例如：

```python
result = workspace.source('MATCH (n:file:db) RETURN n')
```

它应支持：

- `result.count()`
- `result.one()`：要求唯一，否则报错
- `result.first()`
- `result.all()`
- 可迭代

这样：

- `query` 场景用 `.one()`
- `grep` 场景直接迭代

## 3.3 SourceHandle

单个绑定后的 source 应返回统一协议，例如：

```python
src.kind()
src.node()
src.project()
src.exists()
```

按类型开放具体能力：

- DB 类 source：
  - `query(sql)`
  - `open_db()`
  - `list_tables()`
  - `describe_table(name)`

- 文本 / 文件类 source：
  - `read_text()`
  - `open_file(...)`
  - `path()`

不支持的方法应明确报错，而不是沉默失败。

---

## 四、内部实现建议

## 4.1 不把实现塞回 Store 基类顶层 API

不建议继续在 `Store` 基类顶层扩展更多：

- `open_db`
- `open_file`

这会继续把 `Store` 变成“图谱 + source IO 混合体”。

更合适的做法是：

- `Workspace.source(...)` 作为公共入口
- 内部通过 `SourceManager` / `SourceBinder` 分发
- 各类 `SourceHandle` 负责具体访问

## 4.2 推荐内部结构

可以新增一层，例如：

```text
storage/
  source_binding.py
  source_handles/
    base.py
    sqlite.py
    file.py
```

职责：

- `source_binding.py`
  - 解析 cypher 结果
  - 判断哪些节点可绑定
  - 构造 `SourceResult`

- `source_handles/base.py`
  - 定义统一接口

- `sqlite.py`
  - 处理 `file:db`

- `file.py`
  - 处理普通文件类节点

如果你后面不想把它继续放在 `storage/` 下，也可以单独提到：

```text
source/
```

但第一步放在 `storage/` 内部是最省改动的。

## 4.3 cypher 结果到 source handle 的绑定

`workspace.cypher(...)` 现在返回：

- `list[dict]`
- 每一行是 `var -> node dict`

所以 `workspace.source(...)` 的第一步很简单：

1. 执行 cypher
2. 遍历每一行
3. 提取其中的节点对象
4. 判断节点是否可绑定
5. 生成 handle

这里要定一个明确规则：

- 如果一行里有多个节点，默认取哪一个？

建议：

- **要求每一行最终只能绑定出一个 source 节点**
- 如果一行有多个可绑定节点，直接报错

这样语义最清楚，不引入隐式猜测。

---

## 五、对现有代码的影响

## 5.1 extractor

当前 extractor 大量使用：

- `workspace.open_db(...)`
- `workspace.open_file(...)`
- `workspace.resolve_data_path(...)`
- `workspace.data_exists(...)`

后续应逐步迁成：

- `workspace.source(cypher).one().open_db()`
- `workspace.source(cypher).one().read_text()`

这一步可能需要先给 extractor 层写一些 helper，避免每个模块手写一段 cypher。

## 5.2 query tool

当前 [tool/DB_query/tool.py](/nfsdat2/home/bcchenslm/Projects/Pontis/tool/DB_query/tool.py) 还是直接用：

- `workspace.resolve_data_path(file)`

后续应改成：

- tool 层先把 `ref` 转成 cypher
- 再调用 `workspace.source(cypher).one().query(sql)`

这样 `query` 也能自然扩展到非 sqlite source。

## 5.3 grep / 批量扫描工具

这是这套设计的关键受益点。

例如将来 `grep`：

```python
result = workspace.source('MATCH (n:file:text) RETURN n')
for src in result:
    ...
```

这样不需要再额外设计 `sources()`。

## 5.4 guardrail

guardrail 一般不需要直接走 `source()`。

它更适合继续：

- 用 `workspace.cypher(...)` 看图谱知识

只有在未来确实要检查原始 source 内容时，才应显式调用 source binding。

---

## 六、分阶段重构顺序

## 阶段 1：引入最小 `workspace.source(...)`

目标：

- 只支持 `cypher` 输入
- 只支持 `file:db` / 文本文件节点
- 返回 `SourceResult`

这一步不删旧接口，只新增。

## 阶段 2：让 `query` tool 先接新接口

原因：

- `query` 是最自然的 source binding 使用者
- 也是最容易验证抽象是否正确的地方

## 阶段 3：迁 extractor 模块

优先迁：

- `db_basic`
- `db_column_stats`
- `db_column_sample`
- `db_column_topk`
- `db_table_relations`
- `db_fk_validate`

然后再迁文件类 extractor。

## 阶段 4：把历史顶层口子降级

将：

- `open_db`
- `open_file`
- `resolve_data_path`
- `data_exists`

标记为兼容层，仅内部或少数旧模块使用。

## 阶段 5：最终删除或隐藏旧接口

当 extractor / tool 全部迁完后，再真正收口 `Workspace` 顶层 API。

---

## 七、验收标准

重构完成后，应满足：

1. `storage / workspace` 层只暴露两类核心能力：
   - `cypher`
   - `source`

2. `source` 只接受 cypher，不接受 ref

3. `tool` 层继续独占 ref 解析语义

4. `query` / extractor / 后续 grep 工具都能统一基于 `source` 工作

5. `open_db / open_file / resolve_data_path / data_exists` 不再作为主要公共接口使用

---

## 八、一句话总结

这次重构的核心不是“删掉 `open_db`”，而是：

**把原始数据访问从 path-first 的历史接口，重组为 graph-first 的 source binding。**

最终形态应是：

- 图谱访问：`workspace.cypher(...)`
- 数据源访问：`workspace.source(cypher_query, params=None)`

这样既保留你想要的平滑体验，又能把 `storage` 边界真正收紧。
