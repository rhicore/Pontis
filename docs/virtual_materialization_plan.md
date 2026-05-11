## Virtual Materialization Plan

### Goal

把虚实体 / 虚属性的物化逻辑中心化到 `Workspace`，并采用一条简单规则：

- **虚实体 / 虚属性优先级永远高于已经物化的内容**

当前阶段不引入复杂 provenance、冲突分级、人工优先等策略。

---

### Current Problems

1. `FSStore` 里还残留部分旧的虚实体物化分支，职责不够集中。
2. `Workspace` 已经开始做模块调度和 merged read view，但还没有统一的中心物化器。
3. 模块子图和主图之间的匹配与物化闭包逻辑还没有收敛到一条主线。

---

### Target Design

#### 1. 模块职责

每个模块只负责三件事：

- 提供子图
- 提供 `src`
- 提供一个**结构匹配 cypher**

这个匹配 cypher 的返回结果必须是最小结构闭包，而不是只返回单个目标节点。

例如 `fk` 模块返回：

- `fk`
- 两端 `col`
- 两端 `table`

#### 2. Workspace 职责

`Workspace` 负责：

- 执行模块匹配 cypher
- 根据返回结果决定：
  - `0` 个命中：创建整套闭包
  - `1` 个命中：复用并补齐闭包
  - `>1` 个命中：不自动物化，返回歧义
- 统一写入主图

#### 3. 元数据冲突策略

当前阶段采用最简单规则：

- **新的虚实体 / 虚属性值始终覆盖主图已有值**

也就是：

- 虚节点重新出现时
- 模块重新算出的属性重新写入时
- 统一以虚侧结果为准

暂不区分：

- 人工值 / 自动值
- 动态属性 / 静态属性
- 事实属性 / 解释属性

---

### Implementation Steps

#### Phase 1: 中心化 materialize 入口

在 `Workspace` 增加中心物化入口，例如：

- `materialize_node(...)`
- 或内部 `_materialize_match(...)`

职责：

- 执行模块匹配 cypher
- 收集返回节点
- resolve / create / write-back
- 统一建边

#### Phase 2: 模块匹配接口收敛

把模块接口从：

- `match_node(...)`

逐步收敛成：

- `match_cypher(...)`

要求模块返回结构闭包，而不是 Python 级别的规则判断。

#### Phase 3: 写入 merge 规则统一

在中心 materializer 里实现一条简单 merge 规则：

- 对同一个持久化节点：
  - 如果虚侧有该属性，就覆盖主图旧值
  - `_labels` 取并集

#### Phase 4: 清理旧 FS 虚索引遗留

继续移除 `FSStore` 里剩余的旧虚实体物化路径，例如：

- `_virtual_ids` 相关分支
- `_auto_materialize(...)` 的旧耦合逻辑

目标是让：

- 虚节点只存在于模块子图
- 物化只由 `Workspace` 中心入口触发

#### Phase 5: 让读写主线完全一致

最终主线应为：

- 读：`Workspace.cypher(...)` on merged view
- 物化：`Workspace` 中心处理
- 写：统一落主图 store

---

### Immediate Next Task

下一步先做：

1. 设计并接入 `Workspace` 的中心物化入口
2. 先让 FS 模块走通这条入口
3. 采用“虚属性覆盖主图旧值”的简单策略

