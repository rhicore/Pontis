# Store 层 API 文档

`storage.Store` 是 Pontis 的统一知识图谱存储层。所有节点（文件、目录、表、列等）地位平等，使用同一套 API。

## 节点引用（ref）

所有接口通过单一 `ref` 字符串寻址：

| ref 格式 | 含义 | 示例 |
|----------|------|------|
| 路径 | 文件/目录节点 | `"db/event.db"` |
| 路径`::`实体名 | 实体节点（`::` 是边遍历操作符） | `"db/event.db::users.table"` |
| `ent_` 前缀 | ID 直接引用 | `"ent_a3f2c801"` |

`::` 是 Store 的核心操作符——表示沿边遍历。`"db/event.db::users.table"` 的语义是"从 `db/event.db` 节点出发，沿边找到 `users.table` 实体"。

## 构造

```python
from storage import Store

store = Store(project_path)  # project_path 是包含 .pontis/ 的项目根目录
```

| 属性 | 类型 | 说明 |
|------|------|------|
| `project_path` | `str` | 项目根目录绝对路径，物理文件访问的基路径 |
| `pontis_exists` | `bool` | `.pontis/` 目录是否存在 |

---

## 节点寻址

### `resolve_ref(ref) -> (path, entity_name)`

将 ref 字符串解析为内部二元组。一般不需要直接调用。

```python
store.resolve_ref("db/event.db")               # → ("db/event.db", "")
store.resolve_ref("db/event.db::users.table")   # → ("db/event.db", "users.table")
store.resolve_ref("ent_a3f2c801")               # → ID 查表
```

---

## Meta 读取

### `get_meta(ref, *, enrich=False) -> dict | None`

读取节点 meta，自动剥离内部字段（`_id`、`_files`）。

- `enrich=True`: 补充虚属性（文件大小、行数等现场计算的字段，agent 工具用）

```python
# 文件节点
meta = store.get_meta("db/event.db")
# → {"path": "event.db", "table_count": 5, ...}

# 实体节点
col_meta = store.get_meta("db/event.db::users.id.INT.col")
# → {"cardinality": 42, "null_count": 0, ...}

# agent 模式
meta = store.get_meta("db/event.db", enrich=True)
```

### `meta_exists(ref) -> bool`

检查节点 meta 是否存在。

---

## Meta 写入

### `set_meta(ref, data)`

**合并写入**：只更新 `data` 中的字段，保留已有字段。自动维护 `_id` 和 `_files`。

```python
store.set_meta("db/event.db", {"detail": "事件数据库"})
store.set_meta("db/event.db::users.table", {"row_count": 100})
```

### `put_meta(ref, data)`

**全量写入**：替换整个 meta。自动维护 `_id` 和 `_files`。

```python
store.put_meta("db/event.db", {
    "path": "event.db",
    "modified_at": "2026-04-13T10:00:00",
})
```

---

## 节点操作

### `create_node(ref, *, meta=None, edges=None, files=None)`

创建节点。根据 ref 是否含 `::` 自动判断类型：

- **实体节点**（ref 含 `::`）：创建目录 + meta + 自动 `contains` 边 + 用户边
- **文件/目录节点**（ref 不含 `::`）：只写 meta

```python
# 创建文件节点
store.create_node("db/event.db", meta={"path": "event.db"})

# 创建实体节点（自动加 contains 边）
store.create_node("db/event.db::users.table",
    meta={"row_count": 100},
    edges=[{"from": "db/event.db::users.table",
            "type": "columns",
            "to": "db/event.db::users.id.INT.col"}])
```

### `node_exists(ref) -> bool`

检查节点是否存在。

---

## 节点发现

### `find_nodes(pattern) -> list[str]`

按 pattern 查找节点，返回 ref 字符串列表。**`::` 是边遍历操作符**，支持多跳。

```python
# 匹配文件节点
store.find_nodes("*.db")
# → ["db/event.db"]

# 匹配实体节点（跨文件搜索）
store.find_nodes("*.table")
# → ["db/event.db::users.table"]

# 遍历边：DB → 表
store.find_nodes("*.db::*.table")
# → ["db/event.db::users.table"]

# 遍历边：指定 DB → 表
store.find_nodes("db/event.db::*.table")
# → ["db/event.db::users.table"]

# 多跳遍历：DB → 表 → 列
store.find_nodes("*.db::*.table::*.*.*.col")
# → ["db/event.db::users.id.INT.col", ...]

# 通配：某文件下所有相连实体
store.find_nodes("db/event.db::*")
# → ["db/event.db::users.table", "db/event.db::users.id.INT.col", ...]
```

### `find_connected(ref, edge_type=None, pattern="*") -> list[str]`

从指定节点出发，沿边查找相连节点。

```python
# 所有相连节点
store.find_connected("db/event.db")
# → ["db/event.db::users.table", "db/event.db::users.id.INT.col", ...]

# 按边类型过滤
store.find_connected("db/event.db::users.table", edge_type="columns")
# → ["db/event.db::users.id.INT.col", ...]

# 按名称模式过滤
store.find_connected("db/event.db", pattern="*.table")
# → ["db/event.db::users.table"]
```

### `walk_metas(*, enrich=False) -> iterator[(ref, meta)]`

遍历所有节点 meta，yield `(ref, meta)`。

---

## 边操作

边的输入输出均使用 ref 格式，内部自动转换为 ID 存储。

### `get_edges(from_ref=None, edge_type=None, to_ref=None) -> list[dict]`

查询边。参数可选，支持任意组合过滤。

```python
# 某节点的所有出边
store.get_edges(from_ref="db/event.db::users.table")

# 某类型的所有边
store.get_edges(edge_type="columns")

# 组合查询
store.get_edges(from_ref="db/event.db::users.table", edge_type="columns")
# → [{"from": "db/event.db::users.table",
#      "type": "columns",
#      "to": "db/event.db::users.id.INT.col"}, ...]
```

### `add_edges(edges)`

添加边，自动去重。

```python
store.add_edges([
    {"from": "db/event.db::users.table", "type": "columns",
     "to": "db/event.db::users.id.INT.col"},
])
```

### `clear_edges()`

清空所有边。

---

## 物理文件访问

Store 不提供物理文件访问接口。通过 `store.project_path` 操作：

```python
import os

# 从 meta 获取源文件绝对路径
meta = store.get_meta("db/event.db")
abs_path = os.path.join(store.project_path, meta["path"])

# 列目录
entries = os.listdir(os.path.join(store.project_path, "data"))
```

---

## 典型用法

### Extractor 模块

```python
from storage import Store

store = Store(project_path)

# 遍历所有 DB 列实体
for ref in store.find_nodes("*.db::*.*.*.col"):
    path, entity_name = ref.split("::", 1)
    meta = store.get_meta(ref)
    # 处理...
    store.set_meta(ref, {"processed": True})

# 创建实体
store.create_node("db/event.db::users.table",
    meta={"row_count": 100},
    edges=[{"from": "db/event.db::users.table",
            "type": "columns",
            "to": "db/event.db::users.id.INT.col"}])
```

### Agent 工具

```python
# 查询节点的列
columns = store.find_connected("db/event.db::users.table", edge_type="columns")

# 带 enrichment 读取（补充虚属性）
meta = store.get_meta("db/event.db", enrich=True)

# 查询边
edges = store.get_edges(edge_type="foreign_keys")
```

---

## 内部机制（不暴露给外部）

| 机制 | 说明 |
|------|------|
| **`_id`** | 自动生成的节点 ID（`ent_{8hex}`），`get_meta()` 返回时自动过滤 |
| **`_files`** | 文件关联列表，`create_node()` 自动填充，`get_meta()` 返回时自动过滤 |
| **虚属性** | `get_meta(enrich=True)` 时按需计算补充（文件大小、行数等），不持久化 |
| **边索引** | 内存维护 `_outgoing`（出边）和 `_incoming`（入边），支持 `::` 遍历 |
| **边存储** | `_edges.yml` 内部用 ID 引用，`get_edges()` 返回时自动转为 ref 格式 |
| **物理存储** | `.pontis/` 目录结构是内部实现细节，API 不暴露 |
