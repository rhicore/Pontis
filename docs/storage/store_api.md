# Store 层 API 文档

`storage.Store` 是 Pontis 的统一知识图谱存储层。所有节点（文件、目录、表、列等）地位平等，使用同一套 API。

## 核心设计

### Store 是图谱，不是文件系统

Store 只管理图谱中的节点和边。磁盘上的文件通过 **inode** 桥接到图谱节点：

```
物理文件系统                    知识图谱
┌──────────────┐              ┌──────────────────────┐
│ db/event.db  │── inode ──── │ ent_b94a3d7c 节点     │
│              │              │   path: db/event.db   │ ← 虚属性
│              │              │   table_count: 1      │ ← 存储
│              │              │   file_size: 20480    │ ← 虚属性
└──────────────┘              └──────────────────────┘
```

- **`_inode`**：文件/目录节点与物理文件的唯一桥梁，`create_node` 时自动记录
- **`path`**：虚属性，方便阅读但不用于身份识别
- **未索引的文件**：inode 在磁盘存在但不在图谱中，`get_meta` 可检测到

### 虚属性

虚属性是从文件系统现场计算的属性（文件大小、子文件数等），不持久化。`get_meta` **始终**计算并返回虚属性。

| 场景 | `get_meta` 返回值 |
|------|--------|
| 图谱有节点 | 存储 meta + 虚属性（虚属性不覆盖已存储字段） |
| 图谱无节点，但磁盘存在 | 纯虚属性 |
| 都不存在或实体无节点 | `None` |

虚属性定义在 `storage/virtual_props.py`，按文件类型注册。

---

## 节点引用（ref）

所有接口通过单一 `ref` 字符串寻址。三种格式：

| ref 格式 | 含义 | 示例 |
|----------|------|------|
| 路径 | 文件/目录节点 | `"db/event.db"` |
| 路径`::`实体名 | 实体节点 | `"db/event.db::users.table"` |
| `ent_` 前缀 | ID 直接引用 | `"ent_a3f2c801"` |

`::` 是边遍历操作符，用于 `find_nodes` 的多跳检索。

---

## Ref 解析逻辑

### 区分文件路径与实体名

**`/` 是文件路径的标志**。实体名（如 `users.table`、`event.id.INT.col`）不含 `/`。

| 输入 | 含 `/`? | 判定 |
|------|---------|------|
| `"db/event.db"` | 是 | 文件路径 |
| `"users.table"` | 否 | 可能为实体名或根目录文件 |
| `"db/event.db::users.table"` | `::` 左侧含 `/` | 文件下的实体 |
| `"ent_a3f2c801"` | 否 | ID 直接引用 |

### `_find_id` 查找流程

```
输入 ref → resolve_ref → (path, entity_name)
                         ↓
                    _find_id(path, entity_name)
                         ↓
            1. name_index 精确匹配 → 命中则返回
            2. 实体名模糊匹配（无 / 且唯一）→ 返回
               多匹配 → None（需完整 ref 消歧）
            3. stat(path) → inode → _inode_index → 命中则返回
               inode 不在图谱 → 文件存在但未索引
               stat 失败 → 文件不存在
```

### 解析示例

```
get_meta("db/event.db")
  → resolve_ref → ("db/event.db", "")
  → _find_id:
    1. name_index[("db/event.db", "")] → 命中 ✓

get_meta("users.table")
  → resolve_ref → ("users.table", "")
  → _find_id:
    1. name_index[("users.table", "")] → 未命中
    2. 实体名模糊匹配 → 找到唯一 ent_id ✓

get_meta("db/event.db::users.table")
  → resolve_ref → ("db/event.db", "users.table")
  → _find_id:
    1. name_index[("db/event.db", "users.table")] → 命中 ✓

get_meta("ent_a3f2c801")
  → _id_index["ent_a3f2c801"] → "db/event.db::users.table" ✓
```

### Inode 追踪

文件移动后，通过 inode 仍可定位：

```
get_meta("archive/event.db")     # 新路径
  → name_index 未命中
  → stat → inode=78786998（不变）
  → _inode_index → 同一个 ent_id ✓

get_meta("db/event.db")          # 旧路径，文件已移走
  → name_index 命中（旧路径仍在索引中） ✓
```

---

## `get_meta` 完整行为

```
get_meta(ref)
  ↓
resolve_ref → (path, entity_name)
_find_id → ent_id
  ↓
  ├─ ent_id 存在 ──→ 读取存储 meta → 剥离内部字段 → 补充虚属性 → 返回
  │
  ├─ ent_id=None, entity_name 非空 ──→ 实体必须存在于图谱 → None
  │
  └─ ent_id=None, entity_name 为空 ──→ enrich_meta({}, path)
       ├─ 磁盘存在 → 返回纯虚属性
       └─ 磁盘不存在 → None
```

---

## 构造

```python
from storage import Store

store = Store(project_path)  # 包含 .pontis/ 的项目根目录
```

| 属性 | 类型 | 说明 |
|------|------|------|
| `project_path` | `str` | 项目根目录绝对路径 |
| `pontis_exists` | `bool` | `.pontis/` 目录是否存在 |

---

## Meta 读取

### `get_meta(ref) -> dict | None`

读取 meta + 虚属性。详见上方完整行为。

```python
# 有节点的文件：存储 + 虚属性
store.get_meta("db/event.db")
# → {"path": "db/event.db", "table_count": 1, "file_size": 20480, ...}

# 无节点的目录：纯虚属性
store.get_meta("db/")
# → {"child_count": 2, "file_count": 1, "subdir_count": 1}

# 不存在：None
store.get_meta("nope.txt")  # → None
```

### `meta_exists(ref) -> bool`

检查图谱中是否有此节点（不涉及虚属性，不涉及磁盘）。

---

## Meta 写入

### `set_meta(ref, data)`

**合并写入**：只更新 `data` 中的字段，保留已有字段。自动维护内部字段。

```python
store.set_meta("db/event.db", {"detail": "事件数据库"})
store.set_meta("db/event.db::users.table", {"row_count": 100})
```

### `put_meta(ref, data)`

**全量写入**：替换整个 meta。自动维护内部字段。

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

- **实体节点**（ref 含 `::`）：写 meta + 自动 `contains` 边 + 用户边
- **文件/目录节点**（ref 不含 `::`）：写 meta + 自动 stat 记录 `_inode`

```python
# 文件节点（自动 stat → _inode）
store.create_node("db/event.db", meta={"path": "event.db"})

# 实体节点（自动加 contains 边 + columns 边）
store.create_node("db/event.db::users.table",
    meta={"row_count": 100},
    edges=[{"from": "db/event.db::users.table",
            "type": "columns",
            "to": "db/event.db::users.id.INT.col"}])
```

### `node_exists(ref) -> bool`

检查节点是否在图谱中。

### `walk_metas() -> iterator[(ref, meta)]`

遍历图谱中所有节点，yield `(ref, meta)`。meta 含虚属性。

---

## 节点发现

### `find_nodes(pattern) -> list[str]`

按 pattern 查找图谱中的节点，返回 ref 列表。`::` 是边遍历操作符，支持多跳。

#### 检索匹配规则

| 段类型 | 匹配目标 |
|--------|----------|
| 含 `/` 的 pattern | 仅文件/目录节点（实体名不含 `/`） |
| 不含 `/` 的 pattern | 文件名 + 实体名 |
| `*` | 所有关联节点 |

遍历是**双向**的（同时沿出边和入边），每跳自动去重。

#### 示例

```python
# 单段：匹配文件
store.find_nodes("*.db")
# → ["db/event.db"]

# 单段：匹配实体（跨文件）
store.find_nodes("*.table")
# → ["db/event.db::users.table"]

# 正向：文件 → 实体
store.find_nodes("*.db::*.table")
# → ["db/event.db::users.table"]

# 反向：实体 → 文件（沿入边）
store.find_nodes("*.table::*.db")
# → ["db/event.db"]

# 多跳：文件 → 表 → 列
store.find_nodes("*.db::*.table::*.*.*.col")
# → ["db/event.db::event.id.INT.col", ...]

# 反向多跳：列 → 表 → 文件
store.find_nodes("*.*.*.col::*.table::*.db")
# → ["db/event.db"]
```

### `find_connected(ref, edge_type=None, pattern="*") -> list[str]`

从指定节点出发，沿边（双向）查找相连节点。

```python
# 出边：表的列
store.find_connected("db/event.db::users.table", edge_type="columns")
# → ["db/event.db::users.id.INT.col", ...]

# 入边：表属于哪个文件
store.find_connected("db/event.db::users.table", edge_type="contains")
# → ["db/event.db"]
```

---

## 边操作

边的输入输出均使用 ref 格式，内部自动转换为 ID 存储。

### `get_edges(from_ref=None, edge_type=None, to_ref=None) -> list[dict]`

查询边。参数可选，支持任意组合过滤。

```python
store.get_edges(from_ref="db/event.db::users.table", edge_type="columns")
# → [{"from": "db/event.db::users.table",
#      "type": "columns",
#      "to": "db/event.db::users.id.INT.col"}, ...]
```

### `add_edges(edges)`

添加边，自动去重。

### `clear_edges()`

清空所有边。

---

## 物理文件访问

Store 通过 `store.project_path` 连接物理文件系统：

```python
import os
meta = store.get_meta("db/event.db")
abs_path = os.path.join(store.project_path, meta["path"])
```

---

## 可视化缓存

```bash
python -m utils.scripts.build_cache ./my_project
# 生成 .pontis/_cache/ 树状目录（仅供浏览，不影响图谱）
```

---

## 典型用法

### Extractor 模块

```python
from storage import Store
store = Store(project_path)

for ref in store.find_nodes("*.db::*.*.*.col"):
    path, entity_name = ref.split("::", 1)
    meta = store.get_meta(ref)
    store.set_meta(ref, {"processed": True})
```

### Agent 工具

```python
# 查询节点
columns = store.find_connected("db/event.db::users.table", edge_type="columns")

# 反向查找
tables = store.find_nodes("*.*.*.col::*.table")

# 读取 meta（始终含虚属性）
meta = store.get_meta("db/event.db")
print(meta["file_size"])  # 虚属性
```

---

## 内部机制

### 存储

| 机制 | 说明 |
|------|------|
| **存储格式** | `.pontis/nodes/{ent_id}/_meta.yml`，扁平化按节点 ID 组织 |
| **`_id`** | 节点 ID（`ent_{8hex}`），`get_meta()` 自动过滤 |
| **`_inode`** | 文件/目录的 Linux inode，物理文件与图谱的桥梁，`get_meta()` 自动过滤 |
| **`_entity_name`** | 实体名称，内部索引用，`get_meta()` 自动过滤 |
| **`_files`** | 实体的关联文件路径列表，`get_meta()` 自动过滤 |
| **`path`** | 文件/目录的相对路径，**虚属性**，存储但不用于身份识别 |

### 索引

| 索引 | 说明 |
|------|------|
| **`_id_index`** | `ent_id → ref`，ID 直接引用 |
| **`_inode_index`** | `inode → ent_id`，文件路径 → stat → inode → 节点 |
| **`_name_index`** | `(path, entity_name) → ent_id`，精确 + 模糊匹配 |
| **`_outgoing`** | 出边索引，`node_id → [{type, to}]` |
| **`_incoming`** | 入边索引，`node_id → [{type, from}]`，反向遍历用 |

### 虚属性

| 机制 | 说明 |
|------|------|
| **计算时机** | `get_meta()` 始终计算，不持久化 |
| **有节点** | 补充到存储 meta 中，不覆盖已有字段 |
| **无节点** | 文件/目录在磁盘存在时返回纯虚属性 |
| **定义位置** | `storage/virtual_props.py` + `virtual_props_extract/` |

### 其他

| 机制 | 说明 |
|------|------|
| **边存储** | `_edges.yml` 内部用 ID 引用，`get_edges()` 自动转为 ref 格式 |
| **去重** | `find_nodes` 每跳自动去重，避免多源汇聚产生重复 |
| **可视化缓存** | `build_cache` 将扁平存储转换为 `_cache/` 树状目录，仅供浏览 |
