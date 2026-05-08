# Store 层架构文档

## 1. 整体架构

```
storage/
├── __init__.py              # 导出 Store
├── store.py                 # Store 基类 — 纯图数据库（ABC）
├── cypher.py                # Cypher 解析器 + 执行器
├── workspace.py             # 顶层容器：多项目路由 + 跨项目引用
├── config.py                # 项目配置 + 路由规则
├── enricher.py              # 虚属性按需计算引擎（FS 专属）
├── labels.py                # 扁平标签工具
└── stores/
    ├── __init__.py           # Store 工厂 + 注册表
    ├── fs.py                 # FSStore — 文件系统实现
    ├── sqlite_backend.py     # SQLite 持久化后端
    └── modules/
        └── fs/               # FS 虚属性计算函数
            ├── __init__.py   # PROP_REGISTRY 注册表
            ├── file.py       # file_size, modified_at
            ├── sqlite.py     # table_count, row_count, column_count ...
            └── directory.py  # child_count, file_count, subdir_count
```

外部调用者（extractor、tool、agent）只通过两个入口与 Store 层交互：

- `store.cypher(query, params)` — 所有图读写操作
- `store.open_db / open_file / data_exists` — 数据文件访问

---

## 2. 核心设计原则

### 2.1 Store 基类 = 纯图数据库

基类不包含任何领域逻辑。它是一个纯粹的属性图数据库：

- 节点有属性（dict），边是无向对
- 只有 `labels` 是特殊属性（Cypher 语法级匹配）
- `project` 不存储在节点属性中，由 Cypher 引擎实时附加（从 `store._project_name` 读取）
- `name` 是普通属性，不是标识符

所有 FS 专属逻辑（名称索引、inode 检测、虚属性计算、文件系统扫描）都在 FSStore 中。

### 2.2 FSStore = 图数据库的客户端

FSStore 使用基类提供的图操作来管理自己的实体发现、去重、身份解析。它覆写基类的默认实现以获得 O(1) 名称解析和虚属性补充。

### 2.3 SQLite 持久化

使用 SQLite（WAL 模式）替代 YAML 文件。单个 `.pontis/store.db` 存储：

- `nodes` 表：`id TEXT PK`, `props TEXT (JSON)`
- `edges` 表：`a_id TEXT`, `b_id TEXT`, `PK(a_id, b_id)`
- `_meta` 表：`key TEXT PK`, `value TEXT` — 全局元数据（版本号）
- `cross_edges` 表：跨项目引用指针（含 stale 标记）

### 2.4 多进程并发控制

使用版本号 + 过期重建策略：

- SQLite `_meta` 表存储全局版本号
- 每次写操作递增版本号（`_mark_dirty` → `_bump_version`）
- `_ensure_index()` 检查版本号，过期则从 SQLite 全量重建
- 多个 agent 进程共享同一数据库时自动同步

### 2.5 跨项目关联

跨项目边存储为实体 ID 指针 `(to_project, to_entity_id)`，不参与常规 Cypher 遍历。
Workspace 层提供显式解析方法，含容错处理（target_missing / project_unavailable / stale 标记）。

---

## 3. Store 基类接口规范

### 3.1 抽象方法：子类必须实现

#### 持久化 CRUD

所有图数据的持久化由子类决定存储介质（SQLite、远程 DB、内存等）。

| 方法 | 签名 | 说明 |
|---|---|---|
| `_scan_entities` | `() → List[Tuple[str, dict]]` | 全量扫描所有实体，返回 `[(ent_id, raw_props)]` |
| `_read_entity_meta` | `(ent_id: str) → Optional[dict]` | 读取单个实体的完整属性 |
| `_write_entity_meta` | `(ent_id: str, data: dict)` | 写入/更新单个实体 |
| `_delete_entity_storage` | `(ent_id: str)` | 删除单个实体的存储 |
| `_read_edges_storage` | `() → List[dict]` | 读取所有边，返回 `[{nodes: [id, id]}, ...]` |
| `_write_edges_storage` | `(edges: List[dict])` | 全量写入所有边 |

#### 数据访问（可选）

为 extractor 提供数据文件访问能力。对于无文件概念的存储类型（如 dbt、Kafka），子类可以不实现这些方法，调用时会抛出 `NotImplementedError`。

| 方法 | 签名 | 说明 |
|---|---|---|
| `resolve_data_path` | `(rel_path: str) → str` | 相对路径 → 绝对路径（或 URI） |
| `open_db` | `(rel_path: str) → ContextManager` | 打开数据库连接 |
| `open_file` | `(rel_path: str, mode, **kwargs) → ContextManager` | 打开文件 |
| `data_exists` | `(rel_path: str) → bool` | 检查数据是否存在 |

#### 版本控制

多进程并发场景下，SQLite 的版本号由子类委托给具体后端。

| 方法 | 签名 | 说明 |
|---|---|---|
| `_read_version` | `() → int` | 从持久化读取当前版本号 |
| `_bump_version` | `() → int` | 递增版本号，返回新值 |

#### 跨项目边持久化

| 方法 | 签名 | 说明 |
|---|---|---|
| `_persist_cross_edge` | `(from_id, to_project, to_entity_id)` | 持久化单条跨项目边 |
| `_delete_cross_edge` | `(from_id, to_project, to_entity_id)` | 删除单条 |
| `_delete_cross_edges_for` | `(from_id)` | 删除节点的所有跨项目边 |
| `_update_cross_edge_stale` | `(from_id, to_project, to_entity_id, *, stale)` | 更新 stale 标记 |
| `_load_cross_edges` | `() → Dict[str, List[dict]]` | 从存储加载所有跨项目边 |

### 3.2 可覆写方法：子类按需覆写

#### 索引维护

| 方法 | 基类默认 | 说明 |
|---|---|---|
| `_register_node(eid, props)` | `self._id_index[eid] = props` | 注册节点。子类可额外维护 `_name_index` 等 |
| `_unregister_node(eid)` | `self._id_index.pop(eid)` | 反注册。子类可清理额外索引 |

#### 名称解析

| 方法 | 基类默认 | 说明 |
|---|---|---|
| `_name_to_id(name)` | O(n) 线性扫描 `_id_index` | 子类可覆写为 O(1) |
| `_name_to_ids(name)` | O(n) 线性扫描 | 返回所有同名实体 |
| `_resolve_to_id(ref)` | `_name_to_id` + `ent_` 前缀检查 | 子类可加启发式回退 |

#### 元数据读取

| 方法 | 基类默认 | 说明 |
|---|---|---|
| `_get_meta_internal(ref, ...)` | 加载 meta + 过滤内部字段 | 子类可加 label 分组、虚属性、fallback |
| `_enrich_meta(eid, meta)` | 返回原始 meta | 子类可调用 enricher 补充虚属性 |
| `internal_fields` | `{_id, _entity_name}` | 内部字段集合，不暴露给外部 |

#### 虚实体

| 方法 | 基类默认 | 说明 |
|---|---|---|
| `discover_virtual(pattern, label)` | `[]` | 子类覆写以扫描数据源发现虚实体 |
| `get_virtual_meta(key)` | `None` | 获取虚实体元数据 |
| `get_virtual_neighbors(key)` | `[]` | 获取虚实体邻接 |
| `_materialize_virtual(ref, vmeta)` | `raise NotImplementedError` | 虚→实转换策略 |

#### 钩子

| 方法 | 基类默认 | 说明 |
|---|---|---|
| `_on_before_persist(meta, ename)` | 空操作 | 写入前钩子（如记录 inode） |
| `_on_meta_read(eid)` | 空操作 | 读取后钩子（如记录 mtime） |
| `_mark_dirty()` | `_bump_version` + 更新 `_last_version` | 写操作后通知 |

### 3.3 基类直接提供的功能（子类免费获得）

实现抽象方法后，以下功能开箱即用：

#### 图操作

| 方法 | 说明 |
|---|---|
| `cypher(query, params)` | Cypher 查询统一入口 |
| `_create_node(ref, *, meta, edges, labels)` | 创建节点（自动处理虚→实、`--` 路径拆分、父节点连边） |
| `_delete_node(ref)` | 删除节点（自动清理边、跨项目边） |
| `_set_meta(ref, data)` | 合并更新属性 |
| `_put_meta(ref, data)` | 全量替换属性 |
| `_add_edges(edges)` | 添加边（自动物化端点、去重） |
| `_clear_edges()` | 清空所有边 |

#### 查询

| 方法 | 说明 |
|---|---|
| `_get_meta(ref, include_props)` | 读取属性（带缓存、防循环引用） |
| `_get_stored_meta(ref)` | 仅从缓存/持久化读，不触发虚属性 |
| `_list_all()` | 列出所有实体的 `(name, labels)` |
| `_neighbors(ref)` | 获取邻接实体名列表 |
| `_get_edges(node_ref)` | 获取边列表 |
| `_walk_metas()` | 遍历所有 meta（去重） |
| `resolve_ref(ref)` | 解析 `::` 路径引用 |

#### 跨项目引用

| 方法 | 说明 |
|---|---|
| `_add_cross_edge(from_id, to_project, to_entity_id)` | 添加跨项目引用 |
| `_remove_cross_edge(...)` | 删除单条 |
| `_remove_cross_edges(from_id)` | 删除节点的所有引用 |
| `_mark_cross_edge_stale(...)` | 标记为失效 |
| `get_cross_refs(ref)` | 获取引用列表 |

#### 并发控制

| 方法 | 说明 |
|---|---|
| `_ensure_index()` | 版本检查 + 按需重建 |
| `_mark_dirty()` | 写后递增版本号 |

---

## 4. 数据模型

### 4.1 `_id_index: Dict[str, dict]`

主索引，存储每个节点的完整属性：

```python
{
    "ent_a1b2c3d4": {
        "_id": "ent_a1b2c3d4",
        "_entity_name": "event.db",
        "_labels": ["file", "db"],
        "path": "db/event.db",
        "row_count": 5,
    }
}
```

### 4.2 `_adjacent: Dict[str, set]`

邻接表，无向图：

```python
{
    "ent_a1b2c3d4": {"ent_e5f6g7h8", "ent_i9j0k1l2"},
}
```

### 4.3 `_cross_adjacent: Dict[str, List[dict]]`

跨项目引用索引：

```python
{
    "ent_a1b2c3d4": [
        {"to_project": "bird", "to_entity_id": "ent_e5f6g7h8", "stale": False}
    ]
}
```

### 4.4 Cypher 可见属性

| 属性 | 来源 | 说明 |
|---|---|---|
| `name` | `_id_index[eid]["_entity_name"]` | 实体名称 |
| `labels` | `_id_index[eid]["_labels"]` | 标签列表 |
| `project` | `store._project_name` | 实时附加，不存储 |

---

## 5. 多存储后端扩展性分析

### 5.1 当前接口对各类数据源的适用性

| 数据源类型 | 代表 | 持久化 CRUD | 数据访问 | 名称解析 | 虚实体 | 总评 |
|---|---|---|---|---|---|---|
| 本地文件系统 | ext4, NTFS | ✅ | ✅ | ✅ O(1) | ✅ 文件/目录扫描 | ✅ 完全适用 |
| 远程关系型 DB | MySQL, PostgreSQL, ClickHouse | ✅ | ⚠️ `open_db` 语义需调整 | ✅ | ⚠️ 虚实体 = 未索引的表/列 | ✅ 可适配 |
| 远程 NoSQL | MongoDB, Redis, Cassandra | ✅ | ⚠️ `open_file` 不适用 | ✅ | ⚠️ 虚实体 = 未索引的集合 | ✅ 可适配 |
| 云对象存储 | S3, OSS, GCS | ✅ | ⚠️ 需改为流式访问 | ✅ | ✅ prefix 扫描 | ✅ 可适配 |
| 湖仓表格式 | Iceberg, Hudi, Delta Lake | ✅ | ⚠️ `open_db` 不适用 | ✅ | ✅ 解析 metadata log | ✅ 可适配 |
| 向量数据库 | Milvus, Pinecone | ✅ | ❌ 不适用 | ✅ | ✅ 集合扫描 | ✅ 可适配 |
| 数据工程（dbt） | dbt core | ✅ | ❌ 不适用 | ✅ | ✅ 解析 YAML/SQL | ✅ 可适配 |
| 数据编排 | Airflow, Dagster | ✅ | ❌ 不适用 | ✅ | ✅ 解析 DAG 定义 | ✅ 可适配 |
| 消息总线 | Kafka, RabbitMQ | ✅ | ❌ 不适用 | ✅ | ✅ Topic 发现 | ✅ 可适配 |
| 数据目录 | DataHub, Amundsen | ✅ | ⚠️ API 访问 | ✅ | ✅ 注册表扫描 | ✅ 可适配 |

### 5.2 各类数据源的图建模分析

以下分析每种数据源的虚实体发现、虚属性提取和最终图结构。统一使用图论表示：
- `节点 -- 节点` 表示无向边
- `[labels]` 表示节点的标签
- `属性: 值` 表示虚属性

---

#### 一、物理数据库类型

##### 1.1 关系型数据库（MySQL / PostgreSQL / Oracle / SQL Server / SQLite）

**虚实体发现**：`discover_virtual` → 执行 `INFORMATION_SCHEMA` 查询

```
数据源扫描                     虚实体
──────────                     ──────
SHOW DATABASES           →    [database]      database_name
SHOW TABLES FROM db      →    [table]         table_name
SHOW COLUMNS FROM t      →    [column, TYPE]  col_name:TYPE
SHOW INDEXES FROM t      →    [index]         idx_name
SHOW VIEWS               →    [view]          view_name
```

**虚属性（按需计算，不持久化）**：

| 实体标签 | 虚属性 | 来源 |
|---|---|---|
| `table` | `row_count`, `column_count`, `index_count`, `size_mb` | `COUNT(*)`, `INFORMATION_SCHEMA` |
| `column` | `data_type`, `nullable`, `default_value`, `cardinality` | `INFORMATION_SCHEMA.COLUMNS` |
| `index` | `index_type`, `unique`, `columns` | `SHOW INDEX` |
| `view` | `definition` | `SHOW CREATE VIEW` |

**图结构**：

```
[database] ── [schema] ── [table]
                           ├── [column]
                           ├── [column] ── [index]
                           └── [view]
[table] ── [table]           (外键关系)
[view] ── [table]            (视图依赖)
```

**对应的 Cypher 查询**：

```cypher
-- 查找所有有外键关联的表对
MATCH (a:table)--(b:table) RETURN a, b

-- 查找 user 表的所有字符串类型列
MATCH (t:table {name: "user"})--(c:column:string) RETURN c

-- 查找没有索引的表
MATCH (t:table) WHERE NOT (t)--(:index) RETURN t
```

---

##### 1.2 在线分析型（ClickHouse / Snowflake / BigQuery / Redshift）

**虚实体发现**：在 RDBMS 基础上增加分析引擎特有实体

```
数据源扫描                                虚实体
──────────                                ──────
SHOW CLUSTERS                        →   [cluster]
SHOW DATABASES                       →   [database]
SHOW TABLES                          →   [table]
SHOW COLUMNS                         →   [column]
SHOW PARTITIONS FROM t               →   [partition]
SHOW MATERIALIZED VIEWS              →   [materialized_view]
```

**虚属性**：

| 实体标签 | 虚属性 | 来源 |
|---|---|---|
| `cluster` | `shard_count`, `replica_count`, `zookeeper` | 系统 表 |
| `table` | `engine`, `partition_count`, `total_rows`, `compressed_bytes` | `system.parts` |
| `partition` | `partition_key`, `row_count`, `size_mb`, `min_time`, `max_time` | `system.parts` |
| `materialized_view` | `target_table`, `definition`, `refresh_state` | `system.views` |
| `column` | `compression_codec`, `default_compression_ratio`, `sort_order` | `system.columns` |

**图结构**：

```
[cluster] ── [database] ── [table]
                              ├── [column]
                              ├── [partition] ── [partition]
                              └── [materialized_view] ── [table]  (目标表)
[table] ── [table]              (Join 关系 / 分布式表)
```

**特色查询**：

```cypher
-- 查找最大分区（数据倾斜检测）
MATCH (t:table)--(p:partition) WHERE p.size_mb > 10000 RETURN t, p

-- 查找依赖某表的所有物化视图
MATCH (t:table)--(mv:materialized_view) RETURN mv
```

---

##### 1.3 文档型 NoSQL（MongoDB / CouchDB）

**虚实体发现**：`discover_virtual` → 执行 MongoDB 命令

```
数据源扫描                              虚实体
──────────                              ──────
listDatabases                      →   [database]
listCollections                    →   [collection]
getIndexes                         →   [index]
schema inference (抽样)             →   [field, TYPE]
```

**虚属性**：

| 实体标签 | 虚属性 | 来源 |
|---|---|---|
| `collection` | `document_count`, `avg_doc_size`, `total_size`, `sharded` | `collStats` |
| `index` | `index_type`, `keys`, `unique`, `size` | `listIndexes` |
| `field` | `data_type`, `frequency`, `null_ratio` | 文档抽样分析 |

**图结构**：

```
[database] ── [collection]
                 ├── [field]
                 ├── [field:embedded] ── [field]   (嵌套文档)
                 └── [index] ── [field]             (索引覆盖字段)
[collection] ── [collection]           ($lookup / 引用关系)
```

**特色查询**：

```cypher
-- 查找被索引覆盖最少的 collection
MATCH (c:collection)--(f:field) WHERE NOT (f)--(:index) RETURN c, count(f)

-- 查找跨 collection 引用
MATCH (c1:collection)--(c2:collection) RETURN c1, c2
```

---

##### 1.4 键值型 NoSQL（Redis / DynamoDB / Memcached）

**虚实体发现**：键空间模式推断

```
数据源扫描                              虚实体
──────────                              ──────
SCAN keyspace pattern              →   [keyspace]
key pattern grouping               →   [key_pattern]
TTL 分析                           →   [ttl_group]
```

**虚属性**：

| 实体标签 | 虚属性 | 来源 |
|---|---|---|
| `keyspace` | `key_count`, `memory_usage`, `hit_rate`, `miss_rate` | `INFO` |
| `key_pattern` | `sample_keys`, `avg_ttl`, `avg_size`, `type` | `SCAN` + 抽样 |
| `ttl_group` | `min_ttl`, `max_ttl`, `expired_ratio` | `TTL` 批量查询 |

**图结构**：

```
[keyspace] ── [key_pattern] ── [ttl_group]
[key_pattern] ── [key_pattern]     (命名空间层级: user:* → user:profile:*)
```

**特色查询**：

```cypher
-- 查找内存占用最大的键空间
MATCH (ks:keyspace) RETURN ks ORDER BY ks.memory_usage DESC LIMIT 10

-- 查找即将过期的键模式
MATCH (kp:key_pattern)--(tg:ttl_group) WHERE tg.min_ttl < 300 RETURN kp
```

---

##### 1.5 宽表型 NoSQL（Cassandra / HBase）

**虚实体发现**：

```
数据源扫描                              虚实体
──────────                              ──────
DESCRIBE KEYSPACES                 →   [keyspace]
DESCRIBE TABLES                    →   [table]
DESCRIBE COLUMNS                   →   [column]
partition key 分析                  →   [partition_key]
clustering column 分析              →   [clustering_col]
```

**虚属性**：

| 实体标签 | 虚属性 | 来源 |
|---|---|---|
| `keyspace` | `replication_strategy`, `replication_factor`, `durable_writes` | `system_schema.keyspaces` |
| `table` | `compaction_strategy`, `compression`, `sstable_count`, `partition_count` | `system.size_estimates` |
| `partition_key` | `data_type`, `is_composite` | schema |
| `clustering_col` | `data_type`, `sort_order` | schema |

**图结构**：

```
[keyspace] ── [table]
                ├── [partition_key]
                ├── [clustering_col]
                └── [column]
[table] ── [table]                    (物化视图 / 二级索引关系)
```

---

##### 1.6 时序型（InfluxDB / TimescaleDB）

**虚实体发现**：

```
数据源扫描                              虚实体
──────────                              ──────
SHOW MEASUREMENTS                  →   [measurement]
SHOW TAG KEYS                      →   [tag_key]
SHOW FIELD KEYS                    →   [field_key]
SHOW RETENTION POLICIES            →   [retention_policy]
SHOW CONTINUOUS QUERIES            →   [continuous_query]
```

**虚属性**：

| 实体标签 | 虚属性 | 来源 |
|---|---|---|
| `measurement` | `point_count`, `series_count`, `time_range_start`, `time_range_end` | 统计查询 |
| `tag_key` | `cardinality`, `top_values` | `SHOW TAG VALUES CARDINALITY` |
| `field_key` | `data_type` | `SHOW FIELD KEYS` |
| `retention_policy` | `duration`, `replica_count`, `shard_duration` | `SHOW RETENTION POLICIES` |

**图结构**：

```
[retention_policy] ── [measurement]
                        ├── [tag_key] ── [tag_value_sample]
                        └── [field_key]
[continuous_query] ── [measurement]    (查询目标)
```

---

##### 1.7 图数据库（Neo4j / NebulaGraph / TigerGraph）

**虚实体发现**：

```
数据源扫描                              虚实体
──────────                              ──────
SHOW LABELS                        →   [node_label]
SHOW RELATIONSHIP TYPES            →   [rel_type]
SHOW PROPERTIES                    →   [property_schema]
```

**虚属性**：

| 实体标签 | 虚属性 | 来源 |
|---|---|---|
| `node_label` | `node_count`, `property_keys` | `CALL db.labels()` |
| `rel_type` | `relationship_count`, `source_labels`, `target_labels` | `CALL db.relationshipTypes()` |
| `property_schema` | `data_type`, `mandatory`, `index_exists` | APOC / schema introspection |

**图结构**：

```
[node_label] ── [rel_type] ── [node_label]     (元图)
[node_label] ── [property_schema]               (标签的属性)
```

这是一个**图的元图（meta-graph）**——节点是原图的标签，边是原图的关系类型。用于理解数据模型而非存储原始数据。

---

##### 1.8 向量数据库（Milvus / Pinecone / Qdrant / Chroma）

**虚实体发现**：

```
数据源扫描                              虚实体
──────────                              ──────
list_collections()                 →   [collection]
describe_collection()              →   [field] (向量字段 + 标量字段)
list_indexes()                     →   [index]
list_partitions()                  →   [partition]
```

**虚属性**：

| 实体标签 | 虚属性 | 来源 |
|---|---|---|
| `collection` | `vector_count`, `dimension`, `metric_type` (L2/COSINE/IP) | `describe_collection` |
| `field` | `data_type`, `is_primary`, `is_vector`, `dimension` | schema |
| `index` | `index_type` (HNSW/IVF/FLAT), `metric_type`, `params` | `describe_index` |
| `partition` | `vector_count`, `segment_count` | `get_partition_stats` |

**图结构**：

```
[collection] ── [field]          (schema 字段)
[collection] ── [index] ── [field]  (索引覆盖的字段)
[collection] ── [partition]
```

**特色查询**：

```cypher
-- 查找没有向量索引的 collection
MATCH (c:collection) WHERE NOT (c)--(:index) RETURN c

-- 查找高维向量字段
MATCH (f:field:vector) WHERE f.dimension > 768 RETURN f
```

---

#### 二、文件与对象存储类型

##### 2.1 本地文件系统（已实现 — FSStore）

**虚实体**：`[file, EXT]`（按扩展名推断标签）、`[dir]`

**虚属性**：`file_size`, `modified_at`, `row_count`, `column_count`, `table_count`, `child_count`

**图结构**：

```
[dir] ── [dir] ── [file:db] ── [table] ── [column]
                    ├── [file:csv]
                    ├── [file:json]
                    └── [file:yaml]
```

（已完整实现，此处不展开）

---

##### 2.2 分布式文件系统（HDFS / Ceph / GlusterFS）

**虚实体发现**：

```
数据源扫描                              虚实体
──────────                              ──────
NameNode 目录列表                   →   [directory]
文件列表                            →   [file]
Block 位置                         →   [block]
DataNode 注册表                     →   [datanode]
NameNode 状态                       →   [namenode]
```

**虚属性**：

| 实体标签 | 虚属性 | 来源 |
|---|---|---|
| `directory` | `file_count`, `total_size`, `quota`, `space_quota` | `hdfs dfsadmin` |
| `file` | `size`, `replication_factor`, `block_size`, `owner`, `permission` | `getFileStatus` |
| `block` | `size`, `datanode_locations`, `corrupt` | Block 位置报告 |
| `datanode` | `capacity`, `used`, `remaining`, `last_update` | `JMX` |
| `namenode` | `total_capacity`, `total_used`, `total_blocks`, `files_under_construction` | `JMX` |

**图结构**：

```
[namenode] ── [datanode] ── [block] ── [file] ── [directory]
[directory] ── [directory]
[datanode] ── [block]              (副本分布)
```

**特色查询**：

```cypher
-- 查找副本不足的 block
MATCH (b:block) WHERE b.replication_factor < 3 RETURN b

-- 查找数据倾斜（某 DataNode 存储过多 block）
MATCH (dn:datanode)--(b:block) RETURN dn, count(b) AS block_count ORDER BY block_count DESC
```

---

##### 2.3 云对象存储（S3 / OSS / GCS）

**虚实体发现**：

```
数据源扫描                              虚实体
──────────                              ──────
listBuckets()                      →   [bucket]
listObjects() (prefix 分组)         →   [prefix]
listObjects() (单个对象)             →   [object]
listObjectVersions()               →   [version]
```

**虚属性**：

| 实体标签 | 虚属性 | 来源 |
|---|---|---|
| `bucket` | `object_count`, `total_size`, `versioning`, `encryption`, `region` | `headBucket` + `listObjects` |
| `prefix` | `object_count`, `total_size`, `depth` | `listObjects` 聚合 |
| `object` | `size`, `etag`, `storage_class` (STANDARD/IA/GLACIER), `last_modified`, `content_type` | `headObject` |
| `version` | `version_id`, `is_latest`, `size` | `listObjectVersions` |

**图结构**：

```
[bucket] ── [prefix] ── [prefix] ── [object] ── [version]
```

注意：S3 是扁平命名空间，`prefix` 是从 object key 拆分出来的虚拟目录层级。

**特色查询**：

```cypher
-- 查找 GLACIER 存储类的大文件
MATCH (o:object) WHERE o.storage_class = "GLACIER" AND o.size > 1073741824 RETURN o

-- 查找空 prefix（可能有误的目录结构）
MATCH (p:prefix) WHERE NOT (p)--(:object) RETURN p
```

---

#### 三、湖仓一体与开放表格式

##### 3.1 Apache Iceberg / Delta Lake / Apache Hudi

**虚实体发现**：需要解析元数据日志而非文件系统

```
数据源扫描                              虚实体
──────────                              ──────
Catalog.listNamespaces()           →   [namespace]
Catalog.listTables()               →   [table]
metadata.json / _delta_log         →   [snapshot]
manifest list                      →   [manifest]
manifest 读 data files             →   [data_file]
partition spec                     →   [partition_field]
schema                            →   [column]
```

**虚属性**：

| 实体标签 | 虚属性 | 来源 |
|---|---|---|
| `namespace` | `table_count`, `location` | Catalog API |
| `table` | `format_version`, `snapshot_count`, `total_records`, `total_size`, `partition_spec` | metadata |
| `snapshot` | `timestamp`, `operation` (append/overwrite/replace), `added_files`, `deleted_files` | snapshot log |
| `manifest` | `file_count`, `added_files`, `existing_files`, `partition_values` | manifest 文件 |
| `data_file` | `file_format` (Parquet/ORC/Avro), `record_count`, `size`, `column_sizes` | manifest entry |
| `column` | `data_type`, `nullable`, `default_value` | schema |
| `partition_field` | `source_column`, `transform` (identity/bucket/truncate) | partition spec |

**图结构**：

```
[namespace] ── [table] ── [snapshot] ── [manifest] ── [data_file]
                 ├── [column]
                 └── [partition_field]
[snapshot] ── [snapshot]              (快照链/时间旅行)
[table] ── [table]                    (SQL JOIN 依赖 / 血缘)
```

**特色查询**：

```cypher
-- 查找最近 24 小时的快照
MATCH (t:table)--(s:snapshot) WHERE s.timestamp > "2026-05-07" RETURN t, s

-- 查找大文件（可能导致查询性能差）
MATCH (mf:manifest)--(df:data_file) WHERE df.size > 1073741824 RETURN df
```

---

#### 四、数据工程与逻辑抽象项目

##### 4.1 dbt（Data Build Tool）

**虚实体发现**：解析项目代码而非数据源

```
数据源扫描                              虚实体
──────────                              ──────
dbt_project.yml                    →   [project]
models/*.sql                       →   [model]
macros/*.sql                       →   [macro]
tests/*.sql / *.yml                →   [test]
sources.yml                        →   [source]
exposures.yml                      →   [exposure]
schema.yml (columns)               →   [column_ref]
```

**虚属性**：

| 实体标签 | 虚属性 | 来源 |
|---|---|---|
| `project` | `dbt_version`, `profile`, `model_count` | `dbt_project.yml` |
| `model` | `materialization` (table/view/incremental/ephemeral), `schema`, `database`, `description`, `column_count` | SQL + YAML 解析 |
| `source` | `database`, `schema`, `table_name`, `loaded_at_field`, `freshness` | `sources.yml` |
| `test` | `test_type` (unique/not_null/relationships/accepted_values), `severity` | YAML 解析 |
| `exposure` | `type` (dashboard/ml/notes), `owner`, `description` | `exposures.yml` |
| `macro` | `argument_count`, `used_by_models` | SQL 解析 |

**图结构**：

```
[project] ── [model] ── [model]           (ref() 依赖，DAG 边)
              ├── [column_ref]
              ├── [test]
              └── [exposure]
[source] ── [model]                        (source() 引用)
[model] ── [macro]                         (macro 调用)
```

这是一个**有向无环图（DAG）**。当前 Store 的边是无向的，dbt 的 `depends_on` 方向信息可以存在节点属性中（如 `_upstream` / `_downstream` 列表）。

**特色查询**：

```cypher
-- 查找某个 model 的所有上游依赖（2 跳）
MATCH (m:model {name: "fct_orders"})-[*1..3]-(upstream) RETURN upstream

-- 查找没有 test 的 model
MATCH (m:model) WHERE NOT (m)--(:test) RETURN m

-- 查找过期的 source
MATCH (s:source) WHERE s.freshness = "stale" RETURN s
```

---

##### 4.2 数据编排（Airflow / Dagster / Prefect）

**虚实体发现**：

```
数据源扫描                              虚实体
──────────                              ──────
DAG 定义文件                         →   [dag]
Task 定义                           →   [task]
Sensor 定义                         →   [sensor]
Connection 配置                     →   [connection]
Variable 配置                       →   [variable]
Pool 配置                           →   [pool]
```

**虚属性**：

| 实体标签 | 虚属性 | 来源 |
|---|---|---|
| `dag` | `schedule_interval`, `task_count`, `last_run_status`, `avg_duration`, `owner` | Airflow DB / API |
| `task` | `task_type` (Bash/Python/Sensor), `retries`, `timeout`, `pool`, `upstream_task_ids` | DAG 解析 |
| `sensor` | `poke_interval`, `timeout`, `mode` (poke/reschedule) | DAG 解析 |
| `connection` | `conn_type`, `host`, `schema` | Airflow DB |
| `pool` | `slots`, `running_slots`, `queued_slots` | Airflow DB |

**图结构**：

```
[dag] ── [task] ── [task] ── [sensor]       (执行依赖链)
[task] ── [connection]                         (数据源连接)
[dag] ── [pool]                               (资源池)
[task] ── [variable]                           (配置引用)
[dag] ── [dag]                                (ExternalTaskSensor 跨 DAG)
```

**特色查询**：

```cypher
-- 查找关键路径（最长依赖链）
MATCH (d:dag {name: "etl_daily"})--(t:task)-[*1..5]-(t2:task) RETURN t, t2

-- 查找使用某 connection 的所有 task
MATCH (t:task)--(c:connection {name: "prod_mysql"}) RETURN t
```

---

##### 4.3 流处理与消息总线（Kafka / RabbitMQ / Flink）

**虚实体发现**：

```
数据源扫描                              虚实体
──────────                              ──────
listTopics()                       →   [topic]
describeTopics() partitions        →   [partition]
listConsumerGroups()               →   [consumer_group]
describeCluster() brokers          →   [broker]
Flink jobs                         →   [stream_job]
```

**虚属性**：

| 实体标签 | 虚属性 | 来源 |
|---|---|---|
| `broker` | `host`, `port`, `rack`, `partition_count`, `leader_count` | AdminClient |
| `topic` | `partition_count`, `replication_factor`, `message_rate`, `total_size`, `retention_ms`, `cleanup_policy` | AdminClient + JMX |
| `partition` | `leader_broker`, `replica_brokers`, `isr_count`, `offset_begin`, `offset_end` | Metadata API |
| `consumer_group` | `members`, `lag`, `consume_rate`, `assigned_partitions` | AdminClient |
| `stream_job` | `state` (running/failed/canceled), `parallelism`, `checkpoint_interval` | Flink REST API |

**图结构**：

```
[broker] ── [topic] ── [partition] ── [broker]     (leader/replica)
[consumer_group] ── [topic]                          (订阅关系)
[stream_job] ── [topic]                              (source/sink)
[topic] ── [stream_job] ── [topic]                   (处理管道)
```

**特色查询**：

```cypher
-- 查找消费延迟严重的 consumer group
MATCH (cg:consumer_group)--(t:topic) WHERE cg.lag > 1000000 RETURN cg, t

-- 查找 ISR 不足的 partition
MATCH (p:partition) WHERE p.isr_count < 3 RETURN p

-- 查找完整的数据流管道
MATCH (t1:topic)--(j:stream_job)--(t2:topic) RETURN t1, j, t2
```

---

##### 4.4 数据目录与治理（DataHub / Amundsen / Atlas / Glue Catalog）

**虚实体发现**：

```
数据源扫描                              虚实体
──────────                              ──────
搜索 dataPlatformInstances          →   [platform]
搜索 datasets                       →   [dataset]
搜索 schemas                        →   [schema_field]
搜索 glossaryTerms                  →   [glossary_term]
搜索 tags                           →   [tag]
搜索 corpUsers (owners)             →   [owner]
lineage edges                       →   (dataset ── dataset)
```

**虚属性**：

| 实体标签 | 虚属性 | 来源 |
|---|---|---|
| `platform` | `type` (mysql/kafka/dbt...), `instance_count`, `dataset_count` | DataHub API |
| `dataset` | `platform`, `namespace`, `description`, `ownership`, `freshness`, `usage_count` | DataHub API |
| `schema_field` | `data_type`, `nullable`, `description`, `field_path` | DataHub API |
| `glossary_term` | `definition`, `parent_term`, `related_terms`, `assigned_datasets` | DataHub API |
| `tag` | `description`, `assigned_count` | DataHub API |
| `owner` | `username`, `team`, `owned_dataset_count` | DataHub API |

**图结构**：

```
[platform] ── [dataset] ── [schema_field]
[dataset] ── [owner]
[dataset] ── [tag]
[dataset] ── [glossary_term]
[glossary_term] ── [glossary_term]             (术语层级)
[dataset] ── [dataset]                          (血缘关系)
```

**特色查询**：

```cypher
-- 查找包含 PII 标签的数据集
MATCH (d:dataset)--(t:tag {name: "PII"}) RETURN d

-- 查找某术语下的所有数据集
MATCH (d:dataset)--(g:glossary_term {name: "revenue"}) RETURN d

-- 血缘追踪：某数据集的所有上游
MATCH (d:dataset {name: "fct_orders"})-[*1..5]-(upstream:dataset) RETURN upstream
```

---

#### 5.2.1 各类数据源的图结构对比

| 数据源 | 图的特征 | 典型深度 | 关键边类型 |
|---|---|---|---|
| RDBMS | 树形（db→schema→table→col） | 3-4 | 包含、FK、视图依赖 |
| OLAP | 树 + 横向（分区/物化视图） | 4-5 | 包含、分区、刷新 |
| 文档型 NoSQL | 树 + 嵌套 + 引用 | 3-5 | 包含、嵌套、$lookup |
| 键值型 | 扁平 + 命名空间层级 | 1-3 | 命名空间、TTL 分组 |
| 宽表型 | 树（keyspace→table→col） | 3 | 包含、分区键 |
| 时序型 | 双层树（policy→measurement→tag/field） | 2-3 | 保留策略、测量 |
| 图数据库 | 元图（标签→关系类型→标签） | 2 | 元关系 |
| 向量数据库 | 树（collection→field/index/partition） | 2-3 | schema、索引覆盖 |
| 本地文件系统 | 树（dir→file→table→col） | 3-5 | 包含、虚实体物化 |
| 分布式文件系统 | 树 + 分布拓扑 | 3-5 | 目录、block 分布、副本 |
| 云对象存储 | 扁平 + prefix 层级 | 2-4 | prefix、版本 |
| 湖仓表格式 | 链式（table→snapshot→manifest→file） | 4-6 | 快照链、文件清单 |
| dbt | DAG（有向无环图） | 2-6 | depends_on、source、macro |
| Airflow | DAG + 资源关联 | 2-4 | 任务依赖、连接、跨 DAG |
| Kafka | 拓扑（broker→topic→partition→consumer） | 2-4 | 分区分布、订阅、管道 |
| 数据目录 | 多维（dataset↔owner↔tag↔term） | 2-3 | 血缘、标签、术语 |

### 5.3 需要调整的点

#### 数据访问方法应从抽象降为可选

当前 `resolve_data_path`、`open_db`、`open_file`、`data_exists` 是 `@abstractmethod`，强制所有子类实现。但以下数据源没有"文件"或"本地数据库"的概念：

- dbt / Airflow — 纯代码项目，无数据文件
- Kafka / RabbitMQ — 流式数据，无文件
- Milvus / Pinecone — 通过 SDK 访问，无文件路径
- DataHub / Amundsen — 通过 HTTP API 访问

**建议**：将这 4 个方法从 `@abstractmethod` 改为普通方法，基类抛 `NotImplementedError` 并附明确错误信息。需要数据访问的 Store 覆写，不需要的不实现。

```python
# 当前（强制实现）
@abstractmethod
def open_db(self, rel_path: str):
    """上下文管理器：打开数据库连接。"""

# 建议（可选覆写）
def open_db(self, rel_path: str):
    raise NotImplementedError(
        f"{self.__class__.__name__} does not support open_db. "
        f"This data source has no file-based database access."
    )
```

#### `open_db` 语义应更通用

当前 `open_db` 在 FSStore 中返回 `sqlite3.Connection`。对于远程数据库（MySQL、ClickHouse），应返回对应驱动的连接对象。调用方（extractor）应通过连接对象的统一接口（`.cursor().execute()`）使用，不假设底层是 SQLite。

#### 虚实体发现机制已足够通用

`discover_virtual(pattern, label)` 和 `get_virtual_meta(key)` 的接口签名是通用的：

- **本地 FS**：扫描文件系统
- **远程 DB**：执行 `SHOW TABLES` / `listCollections()`
- **S3**：`list_objects_v2()`
- **dbt**：解析 `models/*.sql` 文件
- **Kafka**：`listTopics()`

子类只需实现"扫描"和"获取元数据"的逻辑，基类的 `_auto_materialize` 和 Cypher 引擎无需改动。

### 5.3 接口扩展建议

当前接口已能覆盖所有列出的数据源类型，核心图操作和 Cypher 引擎完全存储无关。但长期来看，以下扩展可能有用：

| 扩展 | 适用场景 | 优先级 |
|---|---|---|
| 数据访问方法降为可选 | dbt, Kafka, 向量 DB | 高 |
| `_open_connection()` 抽象方法 | 远程 DB（MySQL, ClickHouse） | 中 |
| 异步 `_scan_entities()` | 大规模远程数据源 | 低 |
| 边属性（有向边、权重） | dbt DAG、Kafka 拓扑 | 低 |

---

## 6. Cypher 引擎 (`storage/cypher.py`)

### 6.1 支持的语法

| 语句 | 示例 |
|---|---|
| 单节点查询 | `MATCH (n:table) RETURN n` |
| 属性匹配 | `MATCH (n {name: "loan"}) RETURN n` |
| WHERE 过滤 | `MATCH (n) WHERE n.name ENDS WITH 'id' RETURN n` |
| 关系遍历 | `MATCH (n:file:db)--(t:table) RETURN n, t` |
| 可变长度路径 | `MATCH (a:table)-[*1..3]-(b:col) RETURN a, b` |
| 创建节点 | `CREATE (n:Label {name: "x", path: "data/x.db"})` |
| 创建边 | `MATCH (a {name:"x"}),(b {name:"y"}) CREATE (a)--(b)` |
| 删除节点 | `MATCH (n {name: "x"}) DELETE n` |
| 设置属性 | `MATCH (n {name: "x"}) SET n.brief = "desc"` |
| 参数化 SET | `MATCH (n {name: $name}) SET n += $props` |
| 多节点查询 | `MATCH (a:file),(b:table) RETURN a, b`（笛卡尔积） |

### 6.2 结果格式

读查询返回 `List[dict]`：

```python
[{"n": {"name": "event.db", "labels": ["file", "db"], "project": "context"}}]
```

写操作返回摘要：

```python
[{"created": {"name": "foo", "labels": ["test"], "project": "..."}}]
[{"deleted": [{"name": "foo"}]}]
[{"updated": [{"name": "event.db", "set": ["brief"]}]}]
[{"created_edges": 1}]
```

---

## 7. Workspace (`storage/workspace.py`)

顶层容器，管理多个项目：

```
Workspace
  ├── _config: StoreConfig         # 配置（项目列表 + 路由规则）
  ├── _stores: {name → Store}      # 项目 → Store 实例映射
  │
  ├── get_store(project)           # 获取 Store
  ├── cypher(query, params, project) # 代理到 Store.cypher
  ├── create_entity(ref, ...)      # 统一创建入口（支持路由）
  ├── add_cross_ref(...)           # 跨项目引用
  ├── resolve_cross_refs(project)  # 解析跨项目引用（容错）
  └── purge_stale_refs(project)    # 清理失效引用
```

### 跨项目引用容错

| 失效场景 | 返回状态 |
|---|---|
| 目标实体存在 | `status: "ok"` + `to_entity` 属性 |
| 目标实体被删除 | `status: "target_missing"` + 标记 stale |
| 目标项目未注册 | `status: "project_unavailable"` |
| 解析异常 | `status: "error"` + `detail` |

---

## 8. 实体生命周期

```
                  ┌─────────────────────────────────────┐
                  │           文件系统上的文件              │
                  │  (event.db, data.csv, config.yaml)    │
                  └──────────────┬──────────────────────┘
                                 │ _build_index() → _add_virtual_to_index()
                                 ▼
                  ┌─────────────────────────────────────┐
                  │         虚实体 (_v_xxxx)              │
                  │  内存索引中，不在 store.db 中           │
                  │  labels: 由扩展名推断                  │
                  └──────┬──────────────────────┬───────┘
                        │                      │
              SET/CREATE 触发         _create_node 触发
              _auto_materialize       _materialize_virtual
                        │                      │
                        ▼                      ▼
                  ┌─────────────────────────────────────┐
                  │        持久化实体 (ent_xxxx)          │
                  │  store.db → nodes 表                  │
                  │  extractor 写入的 meta 属性           │
                  │  虚属性按需计算不持久化                 │
                  └──────────────┬──────────────────────┘
                                 │ DELETE 触发
                                 ▼
                  ┌─────────────────────────────────────┐
                  │            已删除                     │
                  │  store.db 中节点和边移除               │
                  │  跨项目引用清理                        │
                  └─────────────────────────────────────┘
```

---

## 9. 并发控制

```
SQLite (source of truth)          Agent A (内存)         Agent B (内存)
┌─────────────────────┐          ┌──────────────┐       ┌──────────────┐
│ nodes, edges        │          │ _id_index    │       │ _id_index    │
│ _meta: version = 5  │◄─写入──  │ version = 5  │       │ version = 5  │
└─────────────────────┘          └──────────────┘       └──────────────┘
         │                              │                       │
         │ 版本号变 6                    │ _ensure_index()       │ _ensure_index()
         │                              │ 5 ≠ 6 → 重建          │ 5 ≠ 6 → 重建
         ▼                              ▼                       ▼
```

- 写操作：先写 SQLite → `_mark_dirty()`（递增版本 + 更新本地 `_last_version`）
- 读操作：`_ensure_index()` 检查版本号，过期则全量重建
- 写写冲突：SQLite `INSERT OR REPLACE` 保证 last-write-wins

---

## 10. 扩展新存储后端

### 10.1 基本步骤（以 S3 为例）

1. 创建 `storage/stores/s3.py`，继承 `Store`
2. 实现持久化抽象方法（可用 SQLite 做本地缓存）
3. 覆写 `_name_to_id` 提供 O(1) 名称解析
4. 覆写 `discover_virtual` 扫描 S3 prefix
5. 注册后端：`register_backend("s3", S3Store)`

### 10.2 无文件数据源（以 dbt 为例）

1. `_scan_entities` → 解析 `dbt_project.yml` + `models/*.sql`
2. 不实现 `open_db` / `open_file`（调用时抛 NotImplementedError）
3. `discover_virtual` → 扫描 `models/` 目录发现未索引的 model
4. 边由 dbt 的 `ref()` / `depends_on` 自动构建

---

## 11. 关键设计决策

| 决策 | 原因 |
|---|---|
| 自定义 Cypher 引擎 | 统一虚实体 + 实实体的图谱查询，外部图数据库无法感知虚实体发现 |
| `_id_index: Dict[str, dict]` | 存完整属性，`_get_labels_by_id` 等无需磁盘访问 |
| 基类线性扫描，子类覆写为 O(1) | 基类保持纯净，性能优化由子类负责 |
| SQLite 替代 YAML | 解决 `_edges.yml` 全量重写的并发问题，WAL 模式读写不互斥 |
| `project` 不存储 | 同一实体在不同 Workspace 视角下可能显示不同的 `project` |
| 跨项目边不参与 Cypher 遍历 | 避免 Store 间的隐式耦合，显式解析更可控 |
| 实体 ID 指针（非查询语句） | 直接、无字符串解析，与 `_id_index` 查找一致 |
| stale 标记不自动删除 | 保留审计痕迹，显式清理 |
| 版本号 + 过期重建 | 解决多进程内存/磁盘脑裂，开销极低（单行 PK 查询） |
| 数据访问方法可降为可选 | 覆盖无文件概念的数据源（dbt, Kafka, 向量 DB） |
