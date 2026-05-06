# Store 子类化架构设计

## 1. 问题

当前 storage 层的文件访问逻辑硬编码在 Probe、Enricher、Modules 中。切换后端（本地 FS → S3/对象存储）需要改动大量文件。

目标：**每种数据源类型一个 Store 子类，切换后端只需换 Store，其余代码不变。**

---

## 2. 核心思路

文件**来源**（本地 FS / S3 / 云 DB）和文件**格式**（SQLite / CSV / JSON）是两个正交维度：

```
              SQLite          CSV           JSON          Text
本地 FS       db_extract()   csv_extract() json_extract() text_extract()
S3            db_extract()   csv_extract() json_extract() text_extract()
云存储        db_extract()   csv_extract() json_extract() text_extract()
```

- **来源不同，格式相同**：本地 SQLite 和 S3 上的 SQLite，只有"怎么拿到文件"不同，`PRAGMA table_info`、列统计、FK 检测这些处理逻辑 100% 相同
- **格式不同，来源相同**：同一台机器上的 .db 和 .csv，文件获取方式相同，解析逻辑完全不同

所以：**按来源拆 Store 子类，格式相关的提取逻辑由 Extractor 自行处理。**

---

## 3. 重构范围

**本次重构只涉及 storage 层**，Extractor 不动。

```
重构范围                              不动
────────────────────────────        ────────────────────
storage/store.py                    extractor/modules/*
storage/probe.py                    agent/*
storage/enricher.py                 scripts/*
storage/modules/*                   (外部使用者)
storage/finder.py
storage/workspace.py
storage/config.py
```

---

## 4. 架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Finder / Agent / Prompt                   │
│                 （只依赖 Store 的查询接口）                    │
└──────────────────────────┬──────────────────────────────────┘
                           │  读取
                  ┌────────▼────────┐
                  │   Store (基类)   │
                  │                 │
                  │  ┌───────────┐  │
                  │  │ .pontis   │  │  持久化实体（已提取）
                  │  │ 持久化层  │  │
                  │  └───────────┘  │
                  │                 │
                  │  ┌───────────┐  │
                  │  │ 虚实体层  │  │  文件、目录、表、列的动态发现
                  │  └───────────┘  │
                  │                 │
                  │  ┌───────────┐  │
                  │  │ 虚属性层  │  │  动态计算的属性（file_size 等）
                  │  └───────────┘  │
                  │                 │
                  │  统一查询接口    │  get_meta / find_nodes / neighbors
                  └────────┬────────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
       ┌──────▼──────┐ ┌───▼────┐ ┌────▼──────┐
       │ LocalStore  │ │S3Store │ │CloudDB    │
       │             │ │        │ │Store      │
       │ .pontis 读写│ │.pontis │ │.pontis    │
       │ 虚实体发现  │ │读写    │ │读写       │
       │ 虚属性计算  │ │虚实体  │ │虚实体发现 │
       │             │ │虚属性  │ │虚属性计算 │
       └─────────────┘ └────────┘ └───────────┘
```

### Store 的职责

Store 是图谱的**唯一入口**，负责：

1. **持久化层**：.pontis 目录的读写（实体 YAML、边 YAML）
2. **虚实体层**：动态发现未持久化的实体（文件、目录、表、列等）
3. **虚属性层**：为实体动态计算属性（file_size、child_count 等）
4. **统一查询**：对上层（Finder / Agent）提供统一接口，调用方不区分持久化/虚实体/虚属性

### Extractor 的定位

Extractor 是 Store 外部的数据生产者，和 Store 松耦合：

1. Extractor 自己读文件、自己计算
2. 通过 `create_node / set_meta / add_edges` 把结果写入 Store
3. 写完就结束，后续所有读取由 Store 负责

Extractor 不是本次重构的对象。Store 子类化后，Extractor 调用的写入接口不变。

---

## 5. Store 子类的职责边界

每个 Store 子类封装一种后端的**全部**访问逻辑：

| 职责 | 说明 | LocalStore | S3Store | CloudDBStore |
|---|---|---|---|---|
| .pontis 读写 | 持久化实体和边 | 本地 YAML 文件 | S3 对象或本地缓存 | 本地 YAML 或独立库 |
| 虚实体发现 | 动态发现未持久化实体 | `os.walk` 扫文件/目录 | `list_objects_v2` | `information_schema` |
| 虚属性计算 | 补充 meta 中缺失的字段 | `os.stat` 等 | `head_object` | catalog 查询 |
| 虚邻接 | 目录→子项等关系 | `os.listdir` | CommonPrefixes | schema→table→col |
| 统一查询 | get_meta / find_nodes | 基类实现 | 基类实现 | 基类实现 |

**关键**：虚实体和虚属性的计算完全在 Store 内部完成。Finder / Agent 调用 `get_meta("formula_1.db")` 时，Store 内部决定是从 .pontis 读、还是动态计算、还是两者合并。调用方无感知。

---

## 6. Store 基类

```python
class Store(ABC):
    """图谱存储基类。

    子类封装特定后端的全部访问逻辑：
    - .pontis 持久化数据的读写
    - 虚实体的发现
    - 虚属性的计算
    - 虚邻接的提供

    基类提供统一的查询接口，供 Finder / Agent 使用。
    """

    # ==================== 子类实现 ====================

    @property
    @abstractmethod
    def project_path(self) -> str: ...

    @abstractmethod
    def _read_persisted_meta(self, ent_id: str) -> Optional[dict]:
        """从 .pontis 读取持久化的 meta。"""

    @abstractmethod
    def _write_persisted_meta(self, ent_id: str, meta: dict):
        """将 meta 写入 .pontis。"""

    @abstractmethod
    def _discover_virtual(self, pattern: str, label: str = None) -> List[NodeInfo]:
        """发现虚实体（未持久化的文件、目录、表、列等）。"""

    @abstractmethod
    def _get_virtual_meta(self, key: str) -> Optional[dict]:
        """获取虚实体的元数据。"""

    @abstractmethod
    def _get_virtual_neighbors(self, key: str) -> List[NodeInfo]:
        """获取虚实体的邻接节点。"""

    @abstractmethod
    def _enrich_meta(self, ent_id: str, meta: dict) -> dict:
        """为实体补充虚属性（file_size、child_count 等）。

        返回需要补充的字段 dict，不修改 meta 本身。
        """

    # ==================== 统一查询接口（基类实现） ====================

    def get_meta(self, ref, **kwargs) -> Optional[dict]:
        """统一查询：持久化 meta + 虚属性补充。"""
        ent_id = self._resolve_to_id(ref)
        if ent_id is not None:
            result = self._read_persisted_meta(ent_id)
            extra = self._enrich_meta(ent_id, result)
            result.update(extra)
            return result
        # 未持久化 → 尝试虚实体
        return self._get_virtual_meta(ref)

    def find_nodes(self, pattern, **kwargs) -> list:
        """统一发现：持久化实体 + 虚实体。"""
        persisted = self._find_in_index(pattern, **kwargs)
        virtual = self._discover_virtual(pattern, **kwargs)
        return self._merge_results(persisted, virtual)

    def neighbors(self, ref) -> list:
        """统一邻接：持久化边 + 虚邻接。"""

    # ==================== 写入接口（不变） ====================

    def create_node(self, ref, *, meta=None, edges=None, labels=None) -> str: ...
    def set_meta(self, ref, meta: dict, *, merge=True): ...
    def add_edges(self, edges: List[dict]): ...
```

---

## 7. Store 子类实现

### 7.1 LocalStore

```python
class LocalStore(Store):
    """本地文件系统 Store。"""

    def __init__(self, project_path: str):
        self._project_path = os.path.abspath(project_path)
        self._probe = FilesystemProbe()

    # --- 持久化 ---
    def _read_persisted_meta(self, ent_id):
        path = self._meta_path(ent_id)
        if os.path.exists(path):
            with open(path) as f:
                return yaml.safe_load(f)
        return None

    def _write_persisted_meta(self, ent_id, meta):
        path = self._meta_path(ent_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(meta, f)

    # --- 虚实体 ---
    def _discover_virtual(self, pattern, label=None):
        results = []
        if label is None or label == "dir":
            results.extend(self._probe.discover_dirs(self, pattern))
        if label is None or label not in ("dir",):
            results.extend(self._probe.discover_files(self, pattern))
        return results

    def _get_virtual_meta(self, key):
        return self._probe.get_meta(self, key)

    def _get_virtual_neighbors(self, key):
        return self._probe.get_adjacent(self, key)

    # --- 虚属性 ---
    def _enrich_meta(self, ent_id, meta):
        path = meta.get("path", "")
        full = os.path.join(self._project_path, path)
        result = {}
        if os.path.isfile(full):
            stat = os.stat(full)
            if "file_size" not in meta:
                result["file_size"] = stat.st_size
            if "modified_at" not in meta:
                result["modified_at"] = stat.st_mtime
        return result
```

### 7.2 S3Store

```python
class S3Store(Store):
    """S3 对象存储 Store。"""

    def __init__(self, bucket: str, prefix: str = ""):
        import boto3
        self._s3 = boto3.client("s3")
        self._bucket = bucket
        self._prefix = prefix

    # --- 持久化 ---
    def _read_persisted_meta(self, ent_id):
        key = f".pontis/entities/{ent_id}/meta.yml"
        resp = self._s3.get_object(Bucket=self._bucket, Key=self._prefix + key)
        return yaml.safe_load(resp["Body"])

    def _write_persisted_meta(self, ent_id, meta):
        key = f".pontis/entities/{ent_id}/meta.yml"
        self._s3.put_object(
            Bucket=self._bucket,
            Key=self._prefix + key,
            Body=yaml.dump(meta))

    # --- 虚实体 ---
    def _discover_virtual(self, pattern, label=None):
        resp = self._s3.list_objects_v2(
            Bucket=self._bucket, Prefix=self._prefix, Delimiter="/")
        results = []
        for p in resp.get("CommonPrefixes", []):
            prefix = p["Prefix"].rstrip("/")
            bare = os.path.basename(prefix)
            if fnmatch(bare, pattern):
                results.append((prefix, bare, ["dir"]))
        for obj in resp.get("Contents", []):
            key = obj["Key"]
            bare = os.path.basename(key)
            if fnmatch(bare, pattern):
                results.append((key, bare, []))
        return results

    def _get_virtual_meta(self, key):
        if key.endswith("/"):
            resp = self._s3.list_objects_v2(
                Bucket=self._bucket, Prefix=key, Delimiter="/")
            return {"_labels": ["dir"],
                    "child_count": len(resp.get("Contents", []))}
        obj = self._s3.head_object(Bucket=self._bucket, Key=key)
        return {"file_size": obj["ContentLength"],
                "modified_at": obj["LastModified"].isoformat()}

    def _get_virtual_neighbors(self, key):
        resp = self._s3.list_objects_v2(
            Bucket=self._bucket, Prefix=key, Delimiter="/")
        children = []
        for p in resp.get("CommonPrefixes", []):
            prefix = p["Prefix"].rstrip("/")
            children.append((prefix, os.path.basename(prefix), ["dir"]))
        for obj in resp.get("Contents", []):
            bare = os.path.basename(obj["Key"])
            children.append((obj["Key"], bare, []))
        return children

    # --- 虚属性 ---
    def _enrich_meta(self, ent_id, meta):
        path = meta.get("path", "")
        obj = self._s3.head_object(Bucket=self._bucket, Key=self._prefix + path)
        result = {}
        if "file_size" not in meta:
            result["file_size"] = obj["ContentLength"]
        if "modified_at" not in meta:
            result["modified_at"] = obj["LastModified"].isoformat()
        return result
```

### 7.3 CloudDBStore

```python
class CloudDBStore(Store):
    """云端数据库 Store（PostgreSQL）。

    没有文件系统。表和列直接从 catalog 发现。
    """

    def __init__(self, connection_string: str):
        import psycopg2
        self._conn = psycopg2.connect(connection_string)

    # --- 持久化（知识实体等仍用 .pontis 存储） ---
    def _read_persisted_meta(self, ent_id): ...

    # --- 虚实体（直接查 catalog） ---
    def _discover_virtual(self, pattern, label=None):
        cur = self._conn.cursor()
        results = []
        if label is None or label == "table":
            cur.execute("SELECT table_name FROM information_schema.tables")
            for (name,) in cur:
                if fnmatch(name, pattern):
                    results.append((name, name, ["table"]))
        if label is None or label == "col":
            cur.execute(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns")
            for (table, col, dtype) in cur:
                if fnmatch(col, pattern):
                    results.append((col, col, [f"col/{dtype}"]))
        return results

    def _get_virtual_meta(self, key): ...
    def _get_virtual_neighbors(self, key): ...
    def _enrich_meta(self, ent_id, meta): ...
```

---

## 8. 虚属性共享逻辑

不同 Store 子类的虚属性计算可能有共用逻辑。比如表列统计信息（cardinality、null_count、row_count 等），LocalStore（SQLite）和 CloudDBStore（PostgreSQL）的计算方式类似，只是 SQL 方言不同。

把这类可复用的计算逻辑提取到 `storage/props/` 中，作为 Store 子类的内部工具：

```
storage/props/
├── file_props.py      # file_size, modified_at（各后端差异大，不太复用）
├── dir_props.py       # child_count, file_count, subdir_count
├── table_props.py     # row_count, column_count, primary_key
├── column_props.py    # cardinality, null_count, min/max
└── column_topk.py     # topk 统计
```

这些不是独立模块，而是 Store 子类 `_enrich_meta` 内部调用的工具函数：

```python
# storage/props/table_props.py
def compute_table_stats(conn, table_name: str) -> dict:
    """通用的表统计计算，接收 connection 对象。"""
    cur = conn.execute(f"SELECT COUNT(*) FROM {table_name}")
    row_count = cur.fetchone()[0]
    ...

# LocalStore 内部
def _enrich_meta(self, ent_id, meta):
    if "table" in meta.get("_labels", []):
        conn = sqlite3.connect(os.path.join(self._project_path, meta["path"]))
        extra = compute_table_stats(conn, meta["name"])
        return extra
```

```python
# CloudDBStore 内部
def _enrich_meta(self, ent_id, meta):
    if "table" in meta.get("_labels", []):
        extra = compute_table_stats(self._conn, meta["name"])
        return extra
```

同一个 `compute_table_stats`，LocalStore 传 SQLite 连接，CloudDBStore 传 PostgreSQL 连接。

---

## 9. 迁移路径

### Phase 1：目录虚节点（已完成）

- `storage/probe.py`：FilesystemProbe 发现目录 + 虚邻接
- `storage/finder.py`：`__vdir__` 虚实体遍历
- `storage/store.py`：重名支持、`create_node` 返回 ID
- Extractor：裸名 + `meta["path"]`

### Phase 2：Store 接口抽象

- 定义 Store 基类的抽象方法
- 现有 Store 改名为 LocalStore，实现抽象方法
- Probe 的发现逻辑 → LocalStore._discover_virtual / _get_virtual_meta / _get_virtual_neighbors
- Enricher 的属性计算 → LocalStore._enrich_meta
- Finder / Workspace / Agent 通过基类接口访问 Store

### Phase 3：虚属性共享逻辑提取

- 从 `storage/modules/` 和 `storage/enricher.py` 提取可复用的计算函数到 `storage/props/`
- 各 Store 子类在 `_enrich_meta` 中调用共享的 props 函数

### Phase 4：新后端（未来）

- 实现 `S3Store`
- 实现 `CloudDBStore`
- 通过配置选择 Store 类型

---

## 10. 配置示例

```python
# 本地项目（默认）
store = LocalStore(project_path="/path/to/project")

# S3 项目
store = S3Store(bucket="my-data", prefix="raw/")

# 云 DB 项目
store = CloudDBStore(connection_string="postgresql://host/db")

# 配置文件方式（.pontis/config.yml）
# storage:
#   type: local | s3 | cloud_db
#   bucket: my-data          # S3
#   prefix: raw/             # S3
#   connection_string: ...   # CloudDB
```

---

## 11. 待讨论

### 11.1 持久化位置

当前 `.pontis/` 目录存放在项目根目录下。S3/CloudDB 没有本地目录，持久化数据放哪？

- S3：放同一个 bucket 的 `.pontis/` prefix 下
- CloudDB：放本地临时目录，或独立的元数据库

### 11.2 虚属性的计算时机

当前虚属性在 `get_meta` 时实时计算。如果 `os.stat` / S3 `head_object` 调用频繁，是否有性能问题？是否需要缓存？

### 11.3 LocalStore 和当前架构的关系

当前架构中 Probe / Enricher / Store 是三个独立组件。迁移到 LocalStore 时：
- Probe 的发现逻辑 → LocalStore._discover_virtual / _get_virtual_meta / _get_virtual_neighbors
- Enricher 的属性计算 → LocalStore._enrich_meta
- Store 的 CRUD / 查询 → 基类实现（大部分不变）

迁移后 `storage/probe.py` 和 `storage/enricher.py` 成为 LocalStore 的内部实现细节，不再作为独立组件暴露。
