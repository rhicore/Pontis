# Storage 边界收紧重构计划

## 目标

将 `storage` 收紧为**纯图谱层**。

最终边界：

- `storage / workspace` 对外只负责图谱查询与图谱持久化语义
- 外界所有图读写统一走 `workspace.cypher(...)`
- `Store` 的 `_xxx` 私有方法不再被 `tool / agent / explorer / extractor` 直接调用
- `open_db / open_file / resolve_data_path / data_exists` 这类数据源访问接口从 `storage` 外部接口中移除
- 原始数据库 / 文件访问由 `extractor` 或 `tool` 自己处理

## 当前问题

当前代码库里，`storage` 并没有真正成为唯一图接口层，主要问题有三类：

1. 外层大量直接使用 `Store` 私有方法
   - `workspace._get_store(...)`
   - `store._get_meta(...)`
   - `store._set_meta(...)`
   - `store._create_node(...)`
   - `store._add_edges(...)`
   - `store._delete_node(...)`
   - `store._adjacent`

2. `storage` 混入了数据源访问职责
   - `open_db(...)`
   - `open_file(...)`
   - `resolve_data_path(...)`
   - `data_exists(...)`

3. 外层接口不统一
   - 有的图操作走 `cypher`
   - 有的图操作绕过 `cypher` 直接写 `Store`
   - 导致系统边界不稳定，后续难以替换实现

## 重构原则

### 1. 图操作统一走 Cypher

以下行为都应统一走 `workspace.cypher(...)`：

- 查节点
- 查邻居
- 更新属性
- 创建节点
- 创建边
- 删除节点

### 2. Storage 不负责原始数据源访问

`storage` 不再承担：

- 打开 sqlite
- 打开文本文件
- 路径解析
- 文件存在性检查

这些属于数据源访问逻辑，不属于统一图谱接口。

### 3. Store 退回内部实现

`Store` 及其子类只作为 `Workspace.cypher(...)` 的底层实现细节存在。

外层代码不应该再依赖：

- `_get_store`
- `_resolve_to_id`
- `_get_meta`
- `_set_meta`
- `_create_node`
- `_add_edges`
- `_delete_node`
- `_adjacent`

### 4. 先迁移，后删除

不直接删底层实现。

顺序应为：

1. 先把外部调用迁走
2. 再把旧接口标记为内部-only
3. 最后删除不再使用的对外代理

## 目标接口形态

### 保留在 `Workspace` 的接口

- `cypher(query, params=None, project=None)`
- `project_path`
- `active_projects`
- 必要的 project/config 路由能力

### 从 `Workspace` 外部接口移除

- `open_db(...)`
- `open_file(...)`
- `resolve_data_path(...)`
- `data_exists(...)`

### 不再允许外层直接使用

- `workspace._get_store(...)`
- `store._xxx`

## 迁移策略

### 阶段 1：Tool 层统一到 Cypher

优先迁移这些模块：

- `tool/create_entity/tool.py`
- `tool/update_meta/tool.py`
- `tool/delete/tool.py`
- `tool/meta/tool.py`
- `tool/utils/resolve.py`

目标：

- `create_entity` 不再调用 `_create_node / _add_edges`
- `update_meta` 不再调用 `_set_meta`
- `delete` 不再调用 `_delete_node`
- `meta` 不再直接读取 `_adjacent`

说明：

`resolve.py` 可以暂时保留，但职责只能是**把用户 ref 归一化为确定实体名**，不能直接做图写操作。

### 阶段 2：Guardrail 层去 Store 私有依赖

重点检查：

- `agent/guardrail/readme_check.py`
- 其他依赖 `_get_store` 或图私有结构的 guardrail

目标：

- 图相关判断走 `cypher`
- 文件系统约束若仍需存在，应迁到更明确的项目上下文层，而不是依赖 `Store` 私有结构

### 阶段 3：Explorer 层统一

检查 `explorer/` 下是否仍有：

- `_get_store`
- 物理路径依赖
- 直接操作 `Store` 的行为

目标：

- 图谱探索统一走 tool/cypher
- README 生成、reflection 等都不再碰 Store 私有接口

### 阶段 4：Extractor 分层收口

`extractor` 分两类处理：

1. 图写回逻辑
   - 统一走 `workspace.cypher(...)`

2. 原始数据读取逻辑
   - 不再走 `workspace.open_db/open_file`
   - 改为 extractor 自己的 IO / SQLite helper

建议后续新增：

- `extractor/modules/utils/sqlite.py`
- `extractor/modules/utils/files.py`

把数据源读取 helper 放在 extractor 自己的工具层，而不是 storage。

### 阶段 5：删除 Storage 的非图代理接口

当外部调用迁移完成后，删除：

- `Workspace.open_db`
- `Workspace.open_file`
- `Workspace.resolve_data_path`
- `Workspace.data_exists`

并考虑同步删除 `Store` 基类中的对应抽象方法。

## Cypher 能力要求

为了支撑这次收口，Cypher 层必须能稳定覆盖以下操作：

### 读节点

```cypher
MATCH (n {name: $name}) RETURN n
```

### 读邻居

```cypher
MATCH (n {name: $name})--(m) RETURN m
MATCH (n {name: $name})--(m:col) RETURN m
```

### 更新属性

```cypher
MATCH (n {name: $name}) SET n += $props
```

### 创建节点

```cypher
CREATE (n:knowledge:pattern {name: $name, ...})
```

### 创建边

```cypher
MATCH (a {name: $a}), (b {name: $b}) CREATE (a)--(b)
```

### 删除节点

```cypher
MATCH (n {name: $name}) DELETE n
```

如果某类外层需求无法舒服地用 Cypher 表达，应优先增强 Cypher，而不是重新开放 `Store` 私有接口。

## 风险与注意事项

### 1. `meta` 工具最容易回退到私有接口

因为它不只是读节点，还要做邻接分组、格式化展示。

这里需要明确：

- 展示逻辑可以保留在 tool 层
- 但图数据获取必须来自 cypher 查询结果

### 2. `create_entity` 容易为了方便重新绕回 `_create_node`

这次要避免再做“为了稳先临时走私有写接口”的回退。

如果 Cypher 的 `CREATE` 不够稳定，应该修 Cypher，不应该继续绕过它。

### 3. Extractor 迁移会带来较大工作量

因为当前很多 extractor 已经混用了：

- `workspace.cypher(...)`
- `workspace.open_db(...)`
- 少量 `store._add_edges(...)`

这部分要分清：

- 图写回迁到 cypher
- 原始数据访问迁到 extractor 自己的 helper

### 4. 不要把 source-specific 逻辑重新塞回 Workspace

删掉 `open_db/open_file` 后，不要再以别的名字把同样能力挂回 `Workspace`。

否则只是换名，不是收边界。

## 验收标准

满足以下条件时，可以认为这轮重构完成：

1. `tool / agent / explorer / extractor` 不再直接调用 `workspace._get_store(...)`
2. 外层不再直接调用任何 `store._xxx`
3. 图读写全部统一走 `workspace.cypher(...)`
4. 原始数据读取不再依赖 `storage` 对外代理
5. `Workspace` 对外接口只保留图谱与项目上下文相关能力

## 建议执行顺序

1. Tool 层迁移到纯 cypher
2. Guardrail 去 Store 私有依赖
3. Explorer 去 Store 私有依赖
4. Extractor 把图写回与原始数据访问拆开
5. 删除 `Workspace` 的非图代理接口
6. 最后清理 `Store` 基类中不再需要的对外抽象

## 附：迁移检查命令

可用下面的搜索结果作为迁移清单：

```bash
rg "workspace\\._get_store|store\\._[a-z]" tool agent explorer extractor
```

以及：

```bash
rg "open_db\\(|open_file\\(|resolve_data_path\\(|data_exists\\(" tool agent explorer extractor
```
