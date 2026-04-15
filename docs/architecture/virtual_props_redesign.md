# 虚属性架构重构：问题分析

## 1. 现状

### 当前位置与调用关系

```
storage/
  store.py                    ← Store 主类
  virtual_props.py            ← enrich_meta() 入口，被 Store.get_meta() 调用
  virtual_props_extract/
    __init__.py               ← 注册表 PROP_REGISTRY
    common.py                 ← file_size, modified_at（os.path.getsize/getmtime）
    directory.py              ← child_count, file_count, subdir_count（os.listdir）
    database.py               ← table_count, view_count, index_count（sqlite3.connect）
    table.py                  ← row_count, column_count（sqlite3.connect）
    textfile.py               ← file_size, modified_at（复用 common）
```

调用链：

```
Store.get_meta(ref)
  → enrich_meta(meta, project_path, file_rel_path, entity_path)
    → 根据后缀查 PROP_REGISTRY → 调用具体函数(project_path, file_rel_path, entity_path)
```

每个虚属性函数签名：`(project_path: str, file_rel_path: str, entity_path: str) -> value`

### 当前实现的特点

- **直接使用 OS API**：`os.path.getsize()`、`os.listdir()`、`sqlite3.connect()`
- **不经过 Store**：无法使用 `find_connected()` 等图谱查询
- **位于 storage/ 内部**：与 Store 耦合，不是独立模块
- **按文件后缀注册**：新增类型只需加模块 + 注册

---

## 2. 期望目标

虚属性应与 extractor 同级，成为**独立的模块化子系统**：

```
Pontis/
  storage/          ← Store 层（知识图谱 + 文件系统元数据）
  extractor/        ← 提取器（文件内容分析 → 创建节点/边）
  enricher/         ← 虚属性（理想位置，但目前不存在）
    modules/
      db_props.py
      dir_props.py
      ...
```

**开发者体验**：了解 Store API 后，可以像写 extractor 模块一样写虚属性模块。

**需求拆解**：

| # | 需求 | 说明 |
|---|------|------|
| A | 模块化 | 新增/删除虚属性模块不影响其他代码 |
| B | 可插拔 | 与 extractor 类似的注册/发现机制 |
| C | 使用 Store API | 用 `store.find_connected()` 等图谱查询代替直接 OS 调用 |
| D | 与 extractor 同级 | 逻辑上独立于 storage 层 |

---

## 3. 核心矛盾

### 3.1 递归调用问题

如果虚属性函数通过 `store.get_meta()` 获取数据：

```
store.get_meta("db/event.db")
  → enrich_meta(meta, store, ref)
    → store.find_connected("db/event.db", "columns")   ← OK，不触发 get_meta
    → store.get_meta("db/event.db::users.table")       ← 递归！
```

`get_meta()` 内部调用 `enrich_meta()`，如果 enrichment 又调用 `get_meta()`，就会无限递归。

**可行的解法**：
- enrich_meta 只使用 `find_connected()`、`get_edges()` 等不触发 enrichment 的 API
- Store 内部提供一个不经过 enrichment 的 `_get_raw_meta()` 供虚属性使用
- 分层：先算文件系统级虚属性（不依赖图谱），再算图谱级虚属性（不调用 get_meta）

### 3.2 两类虚属性的差异

当前虚属性实际分两类，它们的**数据来源**完全不同：

| 类型 | 示例 | 当前数据源 | 理想数据源 |
|------|------|-----------|-----------|
| **文件系统级** | file_size, modified_at, child_count | `os.stat()`, `os.listdir()` | 仍需 OS API（Store 不暴露 stat） |
| **图谱级** | table_count（从图的结构算） | `sqlite3.connect()` 直接查数据库 | `store.find_connected()` 查边 |

**文件系统级虚属性**无法纯粹通过 Store API 实现，因为 Store 的设计原则是「不读文件内容」，而 `os.stat()` 属于文件系统元数据操作（Store 内部已经用了 `os.stat()` 来获取 inode）。

**图谱级虚属性**（如从边结构推导的属性）可以完全通过 Store API 实现。

### 3.3 职责边界问题

Store 层当前已经在做两件事：
1. **知识图谱管理**（节点、边、索引）
2. **文件系统元数据**（stat、glob、walk）

虚属性增加的是第三个维度：
3. **从文件内容/结构推导的属性**（sqlite3 查询、文件统计）

如果把虚属性移到外部（与 extractor 同级），Store 需要提供一个**干净的接口**让外部模块能获取文件系统元数据，同时避免递归。

---

## 4. 可能的方案

### 方案 A：虚属性作为 Store 的外部插件

```
enricher/
  __init__.py           ← enrich_meta(store, ref, meta) 入口
  registry.py           ← 按后缀/类型注册
  modules/
    db_props.py         ← store.find_connected() 查表数量
    dir_props.py        ← os.listdir()（仍需直接 OS 调用）
    file_props.py       ← os.stat()（仍需直接 OS 调用）
```

- Store.get_meta() 改为调用 `enricher.enrich_meta(store, ref, meta)`
- 文件系统级属性仍直接用 OS API
- 图谱级属性通过 Store API
- **问题**：递归调用需显式规避；文件系统级属性绕过了 Store 层

### 方案 B：Store 暴露文件系统元数据 API

在 Store 中新增公共方法：

```python
class Store:
    def stat_file(self, ref) -> os.stat_result | None:
        """获取文件/目录的 stat 信息（不触发虚属性计算）"""
        path, _ = self.resolve_ref(ref)
        full = os.path.join(self._project_path, path)
        try:
            return os.stat(full)
        except OSError:
            return None

    def list_dir(self, ref) -> list[str]:
        """列出目录内容（不触发虚属性计算）"""
        ...
```

然后虚属性模块通过这些 API 获取数据，不直接调用 OS。

- **优点**：统一了数据获取路径，虚属性完全通过 Store API 工作
- **缺点**：Store 增加了新接口，但这些接口本质上是 OS API 的透传
- **问题**：数据库级虚属性（如 row_count）仍需 `sqlite3.connect()`，Store 无法封装

### 方案 C：分层架构 — Store 内置文件系统属性 + 外部内容属性

```
Store.get_meta() 内置计算：
  - file_size（os.stat）
  - modified_at（os.stat）
  - child_count（os.listdir）
  等纯文件系统元数据属性

外部 enricher 模块（与 extractor 同级）：
  - table_count（通过 store.find_connected() 或 sqlite3 查询）
  - row_count（sqlite3 查询）
  等需要读文件内容/数据库的属性
```

- **优点**：边界清晰——文件系统元数据 = Store 内部，内容相关 = 外部
- **缺点**：虚属性被拆成两半，修改某个文件类型的属性需要改两个地方

### 方案 D：虚属性保持现状，只是移出 storage/

只做物理位置移动，不改架构：

```
enricher/                ← 从 storage/virtual_props* 移出
  __init__.py
  registry.py
  modules/
    ...
```

`enrich_meta()` 签名不变，仍接收 `(meta, project_path, file_rel_path, entity_path)`。

Store.get_meta() 内部调用改为 `from enricher import enrich_meta`。

- **优点**：改动最小，结构更清晰
- **缺点**：虚属性仍直接用 OS API，不经过 Store
- **问题**：没有解决「开发者需要了解两套 API」的问题

---

## 5. 需要讨论的问题

1. **虚属性是否应该完全通过 Store API 获取数据？**
   - 如果是：Store 需要暴露 `stat_file()`、`list_dir()` 等接口
   - 如果否：直接用 OS API 是否可接受？这与 extractor 直接读文件的模式一致

2. **数据库类虚属性（需要 sqlite3.connect）怎么处理？**
   - 这些属性本质上需要读文件内容，与 Store「不读文件」的原则冲突
   - 但它们又不是 extractor（不创建节点/边，只是计算属性）

3. **是否可以接受虚属性被拆成「Store 内置 + 外部模块」两部分？**
   - 文件系统元数据（size, mtime）放在 Store 内部
   - 内容相关属性放在外部 enricher

4. **enricher 模块是否需要像 extractor 一样有 pipeline 机制？**
   - extractor 有注册表和执行顺序
   - 虚属性目前是按后缀匹配，没有执行顺序依赖

5. **递归调用的规避策略？**
   - 显式约定（虚属性不调用 get_meta）？
   - Store 提供内部 `_get_raw_meta()` 接口？
   - 分层计算（先文件系统级，再图谱级）？
