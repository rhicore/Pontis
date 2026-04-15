# Store 层 API 文档

`storage.Store` 是 Pontis 的统一知识图谱存储层，同时是 extractor 和 agent 与项目文件系统的唯一接口。

## 核心设计

### Store 的边界

Store 负责所有**文件系统元数据**操作，但不读文件内容：

```
Store 可以                 Store 不可以
───────────────           ───────────────
os.stat() (inode)         open() 读文件内容
os.walk() 遍历文件         sqlite3.connect() 读数据库
glob 扫描文件系统           json.load() 解析文件
计算虚属性                  解析任何文件格式
```

这使得 extractor 模块不需要直接访问文件系统——通过 Store API 发现文件，拿到路径后自己读内容。

### 节点分类

```
节点 = 显式节点 ∪ 虚节点

显式节点：extractor 处理过的，有 ent_id，存储在 .pontis/nodes/ 下
  ├── 文件节点（如 db/event.db）：含 _inode，通过 inode 桥接物理文件
  └── 实体节点（如 db/event.db::event.table）：含 _entity_name，通过边连接文件节点

虚节点：磁盘上存在但未被 extractor 处理过的文件
  └── 无 ent_id，get_meta() 返回纯虚属性，find_nodes() 可检索到
```

### Inode 桥接

文件/目录节点通过 Linux inode 标识，连接物理文件系统与知识图谱：

```
物理文件系统                    知识图谱
┌──────────────┐              ┌──────────────────────┐
│ db/event.db  │── inode ──── │ ent_b94a3d7c 节点     │
│              │              │   path: db/event.db   │ ← 虚属性
│              │              │   table_count: 1      │ ← 存储
│              │              │   file_size: 20480    │ ← 虚属性
└──────────────┘              └──────────────────────┘
```

- **`_inode`**：文件/目录节点与物理文件的唯一桥梁，`create_node` 时优先用 `meta["path"]` stat
- **`path`**：虚属性，方便阅读但不用于身份识别
- **虚节点**：磁盘上存在但无图谱节点，`get_meta` 返回纯虚属性

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
# 显式节点：存储 + 虚属性
store.get_meta("db/event.db")
# → {"path": "db/event.db", "table_count": 1, "file_size": 20480, ...}

# 虚节点：纯虚属性（磁盘存在但未处理）
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
- **文件/目录节点**（ref 不含 `::`）：写 meta + 自动 stat 记录 `_inode`（优先用 `meta["path"]`）

```python
# 文件节点（自动 stat → _inode，优先用 meta["path"] 定位物理文件）
store.create_node("db/event.db", meta={"path": "db/event.db"})

# 实体节点（自动加 contains 边 + columns 边）
store.create_node("db/event.db::users.table",
    meta={"row_count": 100},
    edges=[{"from": "db/event.db::users.table",
            "type": "columns",
            "to": "db/event.db::users.id.INT.col"}])
```

### `node_exists(ref) -> bool`

检查节点是否在图谱中（显式节点返回 True，虚节点返回 False）。

### `walk_metas() -> iterator[(ref, meta)]`

遍历图谱中所有显式节点，yield `(ref, meta)`。meta 含虚属性。不含虚节点。

---

## 节点发现

### `find_nodes(pattern) -> list[str]`

按 pattern 查找节点，返回 ref 列表。合并两层搜索结果。

#### 双层搜索

```
find_nodes(pattern)
  ↓
  ├── Layer 1: 图谱搜索（fnmatch，递归匹配所有节点名）
  │     扫描 _id_index 中所有显式节点（文件节点 + 实体节点）
  │     fnmatch 的 * 匹配任意字符（含 /），因此 *.db 匹配任意深度
  │     覆盖：已索引的文件 + 所有逻辑实体（.table, .col 等）
  │
  └── Layer 2: 文件系统搜索（glob 语义）
        使用 glob.glob 扫描物理文件系统
        遵循标准 glob 语义：*.db = 仅根目录，**/*.db = 递归
        覆盖：未处理过的文件（虚节点）
```

**合并策略**：Layer 1 结果优先，Layer 2 补充去重。

| Pattern | Layer 1（图谱） | Layer 2（文件系统） | 结果 |
|---------|----------------|-------------------|------|
| `*.table` | 所有 .table 实体 ✓ | 根目录无 .table 文件 | 实体列表 |
| `*.db` | 所有已索引 .db 文件（任意深度） | 根目录 .db 文件 | 合并 |
| `**/*.db` | 所有已索引 .db 文件 | 所有 .db 文件（递归） | 合并去重 |
| `db/*.db` | db/ 下的已索引 .db | db/ 下的 .db 文件 | 合并去重 |

#### `::` 边遍历

`::` 是边遍历操作符，支持多跳、双向。多段 pattern 逐段遍历：

- 第 1 段：由双层搜索产生起始节点集
- 第 2+ 段：沿边（双向）遍历，每跳自动去重
- 虚节点没有边，在遍历中自然被跳过

#### 检索匹配规则

| 段类型 | 匹配目标 |
|--------|----------|
| 含 `/` 的 pattern | 仅文件/目录节点（实体名不含 `/`） |
| 不含 `/` 的 pattern | 文件名 + 实体名（Layer 1） |
| `*` | 所有关联节点 |

#### 示例

```python
# 单段：搜索所有 .table（Layer 1 找到实体，Layer 2 无 .table 文件）
store.find_nodes("*.table")
# → ["db/event.db::users.table"]

# 单段：搜索 .db 文件（Layer 1 + Layer 2）
store.find_nodes("**/*.db")
# → ["db/event.db"]

# 正向：文件 → 实体（沿出边）
store.find_nodes("**/*.db::*.table")
# → ["db/event.db::users.table"]

# 反向：实体 → 文件（沿入边）
store.find_nodes("*.table::*.db")
# → ["db/event.db"]

# 多跳：文件 → 表 → 列
store.find_nodes("**/*.db::*.table::*.*.*.col")
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

## 可视化缓存

```bash
python -m utils.scripts.build_cache ./my_project
# 生成 .pontis/_cache/ 树状目录（仅供浏览，不影响图谱）
```

---

## Extractor 访问模式

Extractor 模块通过 Store API 完成所有文件发现和元数据操作：

```
Extractor 模块          Store API                    文件系统
───────────────        ──────────                   ─────────
文件发现                find_nodes("**/*.db")         glob 扫描
                       ↓ 返回 ref                    + 图谱搜索
                                              
文件节点创建            create_node(path, meta)       stat → _inode

物理文件访问            store.project_path            open/sqlite3
                       + meta["path"]                (读内容)

元数据读写              get_meta / set_meta           .pontis/nodes/

实体节点创建            create_node(ref::entity)      边存储
```

**Extractor 不直接使用 `os.walk()`、`glob.glob()`——全部通过 Store。**

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
