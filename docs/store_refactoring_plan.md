# Store 重构计划

## 1. 问题

当前 `storage/store.py`（~1038 行）承担了三个不同层次的职责：

| 层次 | 当前代码 | 问题 |
|---|---|---|
| 存储原语 | `put_meta`, `get_meta`, `add_edges`, `delete_node` | Store 本职 |
| 查询引擎 | `_parse_glob`, `_global_match`, `_traverse_match`, `_find_files` | 混在 Store 里 |
| 路由决策 | `_GLOBAL_ROOT`, `_is_global`, `_get_global_store`, namespace 路由 | Store 不该管 |

导致：
- 新增实体类型（如知识实体）需要改 Store 内部路由逻辑
- `_namespaces` 同时承载「结构类型」（file, knowledge）和「作用域」（global, formula_1），语义混乱
- 实体名编码了类型后缀（`drivers.table`, `driverId.INT.col`），命名和分类耦合
- Store 方法签名不断膨胀（`namespaces`, `_visiting`, 跨 store fallback）

## 2. 目标架构

```
┌─────────────────────────────────────────────────┐
│  Tools (glob, grep, meta, create_entity...)     │  用户空间
├─────────────────────────────────────────────────┤
│  Finder                                         │  查询引擎（类似 libc）
│  - URN 解析（Project://[Type]:/Path/Pattern）   │
│  - 图遍历（:: 分段）                              │
│  - Type_Constraint 过滤                          │
│  - 跨 project 聚合                               │
├─────────────────────────────────────────────────┤
│  Store                                          │  存储原语（类似 VFS）
│  - 实体 CRUD（纯标识符，无类型后缀）               │
│  - 边 CRUD                                      │
│  - 内存索引（id/name/inode/adjacent）            │
│  - _tags 存储与层级匹配（唯一理解的元属性）       │
│  - Probe 注册（虚节点探测）                       │
├─────────────────────────────────────────────────┤
│  Config                                         │  配置层
│  - project → path 映射                           │
│  - default project                               │
│  - routing 规则（实体名模式 → project）            │
└─────────────────────────────────────────────────┘
```

## 3. URN 寻址协议

采用 Cypher 风格语法。每个段格式：

```
[Project://]Pattern[:Tag1[:Tag2[|Tag3]]]
```

三个部分均可省略：
- **Project://**：逻辑隔离标识（如 `formula_1`, `global`），省略时搜索所有已挂载 Store
- **Pattern**：名称 glob 模式（`*.db`, `**/*.csv`），必填
- **:Tag**：标签过滤，`:` 分隔 AND，`|` 分隔 OR，省略时不做标签过滤

### 3.1 单段查询（无括号）

```
b.py                                  → 纯名称匹配
b.py:file                             → 名称 + 标签
project_a://b.py                      → 项目 + 名称
project_a://b.py:file                 → 完整三段
*:Table                               → 任意名称，必须有 Table 标签
*:Table:Large                         → AND：同时有 Table 和 Large
*:Table|View                          → OR：有 Table 或 View
driver*:col/INT                       → 名称 glob + 层级标签
```

### 3.2 多段遍历（必须加括号）

段之间用 `--` 连接，每段必须加 `()`：

```
(formula_1.db)--(drivers)                                → 两段遍历
(formula_1.db)--(*:Table)--(driverId:col/INT)            → 三段遍历
(project_a://formula_1.db:file/db)--(drivers:Table)--(*:col/INT:primary_key)
(global://*:knowledge/convention)--(*:Table)              → 跨 Store 遍历
```

括号内每段独立指定 Project 和标签，省略的继承上下文。

### 3.3 层级标签匹配

标签支持 `/` 分层的层级结构。匹配规则：**查询标签匹配存储标签的任意路径段**。

存储标签 `file/db` 的匹配情况：

| 查询标签 | 是否匹配 | 原因 |
|---|---|---|
| `file` | 匹配 | 首段/前缀 |
| `db` | 匹配 | 末段 |
| `file/db` | 匹配 | 完全匹配 |

存储标签 `col/INT/primary_key` 的匹配情况：

| 查询标签 | 是否匹配 | 原因 |
|---|---|---|
| `col` | 匹配 | 首段 |
| `INT` | 匹配 | 中间段 |
| `primary_key` | 匹配 | 末段 |
| `col/INT` | 匹配 | 子路径前缀 |
| `col/INT/primary_key` | 匹配 | 完全匹配 |
| `INT/primary_key` | 匹配 | 子路径 |

实现：将存储标签按 `/` 拆分，查询标签与所有子路径（及单段）做匹配。

### 3.5 完整示例

```
# 单段
*.db                                    → 所有 .db 实体
*:file                                  → 所有 file 标签实体
*:file|knowledge                        → file 或 knowledge 标签
project_a://*:file:db                   → project_a 下同时有 file 和 db 标签
*:knowledge/convention                  → 层级标签匹配 knowledge/convention

# 多段遍历
(formula_1.db)--(*:Table)               → formula_1.db 的邻接表节点
(formula_1.db)--(*:Table)--(*:col)      → 表下的所有列
(formula_1.db)--(*:Table)--(*:col/INT)  → 表下的所有 INT 列
(project_a://formula_1.db)--(global://*:knowledge/convention)  → 跨 Store
```

### 3.6 关键变化：实体名去类型后缀

| 旧命名 | 新命名 | _tags |
|---|---|---|
| `formula_1.db` | `formula_1.db` | `["file/db"]` |
| `drivers.table` | `drivers` | `["table"]` |
| `driverId.INT.col` | `driverId` | `["col/INT"]` |
| `no_concat.convention` | `no_concat.convention` | `["knowledge/convention"]` |

类型信息从实体名移到 `_tags` 元数据，标签用 `/` 层级组织。

## 4. 组件职责

### 4.1 Config — `storage/config.py`

新建文件。纯数据结构 + 加载逻辑。

```python
@dataclass
class ProjectEntry:
    path: str                    # 物理存储路径
    default: bool = False        # 是否为默认写入目标
    groups: List[str] = field(default_factory=list)

@dataclass
class RoutingRule:
    pattern: str                 # 实体名匹配模式（如 "*.convention"）
    project: str                 # 目标 project 名

@dataclass
class StoreConfig:
    projects: Dict[str, ProjectEntry]
    routing: List[RoutingRule]

    def default_project(self) -> Optional[str]: ...
    def resolve_path(self, project: str) -> Optional[str]: ...
    def route_entity(self, entity_name: str) -> Optional[str]: ...
```

**配置文件格式** — `pontis.yml`：

```yaml
projects:
  formula_1:
    path: /data/bird/dev/formula_1
    default: true
    groups: [bird, motorsport]
  global:
    path: ~/
    groups: [bird]
  car_retailing:
    path: /data/bird/dev/car_retailing
    groups: [bird, retail]

routing:
  - pattern: "*.convention"
    project: global
  - pattern: "*.pattern"
    project: global
  - pattern: "*.lesson"
    project: global
  - pattern: "*.term"
    project: global
  - pattern: "*.example"
    project: global
```

**加载逻辑**：
1. `{project_path}/pontis.yml` — 项目级配置
2. `~/.pontis/config.yml` — 全局用户配置
3. 无配置时 fallback：`basename(project_path)` 作为 project 名，`default=true`，无 routing 规则

### 4.2 Store — `storage/store.py`（精简）

Store 退化为带边的 KV 存储 + 内存索引。不解析 URN，不路由，不理解 namespace 语义。

**保留的方法**：

```python
class Store:
    def __init__(self, project_path: str):
        """纯物理存储绑定。不传 project 名——那是 config 的事。"""

    # 索引
    def _ensure_index(self)
    def _build_index(self)
    def _register_node(self, ent_id, entity_name, inode=None)
    def _unregister_node(self, ent_id)

    # 实体 CRUD
    def create_node(self, ref, *, meta=None, edges=None, tags=None)
    def put_meta(self, ref, data)
    def set_meta(self, ref, data)
    def get_meta(self, ref, include_props=None) -> Optional[dict]
    def node_exists(self, ref) -> bool
    def delete_node(self, ref) -> str

    # 边 CRUD
    def add_edges(self, edges)
    def get_edges(self, node_ref=None) -> List[dict]
    def clear_edges(self)

    # 原语查询（供 Finder 调用）
    def list_all(self) -> List[Tuple[str, List[str]]]
        """返回 [(entity_name, tags_list), ...]
        类似 getdents + d_type，用于 Finder 快速过滤"""
    def neighbors(self, ref) -> List[str]
        """返回邻接 entity_name 列表"""

    # Probe（虚节点）
    def register_probe(self, probe_fn)
    def _probe_nodes(self) -> List[str]

    # 文件系统扫描（内置 Probe）
    def scan_files(self, pattern) -> List[str]

    # Cache
    def cache_path(self, *parts) -> str
    def cache_find(self, pattern) -> list

    # 属性
    @property
    def project_path(self) -> str
    @property
    def pontis_exists(self) -> bool
```

**删除的方法/属性**：

| 删除 | 原因 |
|---|---|
| `_GLOBAL_ROOT`, `_is_global`, `_get_global_store()` | 路由由 Config + Finder 管理 |
| `_apply_namespaces()` | 改为 `_apply_tags()` |
| `_parse_glob()` | 移到 Finder |
| `_global_match()`, `_traverse_match()` | 移到 Finder |
| `find_nodes()` | 移到 Finder |
| `_find_files()` | 改为 `scan_files()` Probe |
| `walk_metas()`, `find_connected()` | 移到 Finder |
| `resolve_ref()` | 拆分：`::` 遍历移到 Finder，简单名查找保留 |

**`create_node` 签名**：

```python
def create_node(self, ref, *, meta=None, edges=None, tags=None):
```

`tags` 是 `List[str]`，支持层级（如 `["col/INT", "primary_key"]`）。Store 不解释含义，原样存入 `_tags` 字段。

### 4.3 Finder — `storage/finder.py`（新建）

从 Store 提取的查询引擎。类似 libc 的 `glob(3)`。

```python
class Finder:
    def __init__(self, config: StoreConfig):
        self._config = config
        self._stores: Dict[str, Store] = {}

    # Store 管理
    def get_store(self, project: str) -> Store
    def get_default_store(self) -> Store
    def all_stores(self) -> List[Store]

    # 核心查询
    def find(self, urn: str) -> List[str]
        """Cypher 风格 URN 查询，调度到合适的 Store(s)"""

    def find_in_project(self, project: str, urn: str) -> List[str]

    # 高级查询
    def walk_all(self) -> Iterator[Tuple[str, str, dict]]
        """遍历所有 project，yield (project, entity_name, meta)"""

    # 路由
    def route_create(self, entity_name: str) -> Store
        """根据 config routing 规则返回目标 Store"""

    # 内部
    def _parse_urn(self, urn) -> URNParsed
    def _segment_match(self, store, segment) -> List[str]
    def _traverse_match(self, store, segments) -> List[str]
```

**URN 解析结果**：

```python
@dataclass
class Segment:
    project: Optional[str] = None    # Project:// 段（每段独立）
    pattern: str = "*"               # 名称 glob 模式
    tags_and: List[str] = []         # : 分隔的 AND 标签
    tags_or: List[str] = []          # | 分隔的 OR 标签

@dataclass
class URNParsed:
    segments: List[Segment]          # 单段或 -- 连接的多段
```

**解析规则**：

```
b.py:file
  ↓
segments = [Segment(project=None, pattern="b.py", tags_and=["file"])]

project_a://*:Table:Large
  ↓
segments = [Segment(project="project_a", pattern="*", tags_and=["Table", "Large"])]

(project_a://formula_1.db:file/db)--(*:Table)--(driverId:col/INT)
  ↓
segments = [
    Segment(project="project_a", pattern="formula_1.db", tags_and=["file/db"]),
    Segment(project=None, pattern="*", tags_and=["Table"]),
    Segment(project=None, pattern="driverId", tags_and=["col/INT"]),
]

(project_a://formula_1.db)--(global://*:knowledge/convention)
  ↓
segments = [
    Segment(project="project_a", pattern="formula_1.db"),
    Segment(project="global", pattern="*", tags_and=["knowledge/convention"]),
]
```

**层级标签匹配**：

```python
def _tag_matches(self, entity_tags: List[str], query_tag: str) -> bool:
    """查询标签匹配实体标签的任意路径段或子路径。

    存储标签 "col/INT/primary_key" 可被以下查询匹配：
    - "col" (首段), "INT" (中段), "primary_key" (末段)
    - "col/INT" (子路径), "col/INT/primary_key" (完全匹配)
    - "INT/primary_key" (子路径)
    """
    for stored_tag in entity_tags:
        # 生成所有子路径
        segments = stored_tag.split("/")
        for i in range(len(segments)):
            for j in range(i + 1, len(segments) + 1):
                subpath = "/".join(segments[i:j])
                if subpath == query_tag:
                    return True
    return False
```

**`find()` 实现流程**：

```
输入: "*:knowledge/convention"
  ↓
解析 URN: segments=[Segment(project=None, pattern="*", tags_and=["knowledge/convention"])]
  ↓
单段查询，搜索范围: all_stores()（project 未指定）
  ↓
对每个 store:
  store.list_all() → 过滤 _tags 层级匹配 "knowledge/convention" + fnmatch "*"
  ↓
合并去重，返回
```

```
输入: "(formula_1.db)--(*:Table)--(*:col)"
  ↓
解析 URN: segments=[
    Segment(project=None, pattern="formula_1.db"),
    Segment(project=None, pattern="*", tags_and=["Table"]),
    Segment(project=None, pattern="*", tags_and=["col"]),
]
  ↓
多段遍历，project 均未指定 → default_store()
  ↓
_traverse_match():
  第一段：匹配 "formula_1.db"
  第二段：neighbors + fnmatch "*" + tag 匹配 "Table"
  第三段：neighbors + fnmatch "*" + tag 层级匹配 "col"
  ↓
返回匹配的 entity_name 列表
```

```
输入: "(project_a://formula_1.db)--(global://*:knowledge/convention)"
  ↓
跨 Store 遍历：
  第一段在 project_a store 找到 formula_1.db
  第二段在 global store 搜索 tag 匹配 knowledge/convention
  合并结果
```

**标签过滤逻辑（AND + OR）**：

```python
def _match_tags(self, entity_tags: List[str],
                tags_and: List[str], tags_or: List[str]) -> bool:
    """AND: 所有 tags_and 必须匹配（层级）。OR: 至少一个 tags_or 必须匹配。"""
    if tags_and and not all(self._tag_matches(entity_tags, t) for t in tags_and):
        return False
    if tags_or and not any(self._tag_matches(entity_tags, t) for t in tags_or):
        return False
    return True
```

### 4.4 Workspace — `storage/workspace.py`（新建）

顶层容器。由 Agent/Benchmark 脚本创建。

```python
class Workspace:
    def __init__(self, config_path: str = None, project_path: str = None):
        self._config = load_config(config_path, project_path)
        self._finder = Finder(self._config)

    @property
    def finder(self) -> Finder: ...
    @property
    def config(self) -> StoreConfig: ...

    def get_store(self, project: str = None) -> Store:
        """获取指定 project 的 Store，默认返回 default store"""

    def create_entity(self, ref, *, meta=None, edges=None,
                      tags=None, project=None) -> str:
        """统一创建入口。路由逻辑：
        1. 显式 project 参数 → 直接用
        2. config routing 规则匹配 → 自动路由
        3. 兜底 → default project"""
        target = project or self._config.route_entity(ref) or self._config.default_project()
        store = self.get_store(target)
        store.create_node(ref, meta=meta, edges=edges, tags=tags)
```

## 5. 实体命名变更

### 5.1 去除类型后缀

当前 extractor 产生的实体名编码了类型信息：

```python
# db_basic.py 当前
col_entity_name = f"{safe_name}.{safe_col}.{col_type}.col"  # drivers.driverId.INT.col
store.create_node(f"{rel_path}::{col_entity_name}", ...)
```

重构后：

```python
# db_basic.py 重构后
col_entity_name = safe_col  # driverId
store.create_node(f"{rel_path}::{safe_name}::{col_entity_name}",
                  meta={"col_type": "INT", ...},
                  tags=[f"col/{col_type}"],  # "col/INT"
                  edges=[{"a": safe_name, "b": col_entity_name}])
```

**影响范围**：

| 文件 | 当前实体名 | 重构后 | _tags |
|---|---|---|---|
| `db_basic.py` | `drivers.table` | `drivers` | `["table"]` |
| `db_basic.py` | `driverId.INT.col` | `driverId` | `["col/INT"]` |
| `db_basic.py` | `race.view` | `race` | `["view"]` |
| `csv_basic.py` | `name.TEXT.col` | `name` | `["col/TEXT"]` |
| `db_table_relations.py` | `fk_drivers.rel` | `fk_drivers` | `["rel"]` |
| `ai_db_column_rel.py` | `*.rel` | 去后缀 | `["rel"]` |
| `db_column_overlap.py` | `*.overlap` | 去后缀 | `["overlap"]` |

**注意**：同一父节点下的子实体名必须唯一（无类型后缀后，同名冲突由 `_name_index` 检测）。

### 5.2 知识实体保持后缀

`.convention`, `.pattern`, `.term`, `.lesson`, `.example` 这些后缀是**实体名本身的一部分**（如 `AVG_NEEDS_GROUP_BY.convention`），不是类型标记。保持不变。

同时 `_tags` 中存 `["knowledge/convention"]`，层级标签同时标记大类和具体类型。

### 5.3 遍历与歧义

当前 `::` 遍历依赖类型后缀消歧（`formula_1.db::drivers.table` 不会匹配到 `drivers.view`）。去除后缀后，同名消歧改用 Cypher 风格标签过滤：

```
formula_1.db--drivers            → 无歧义（drivers 唯一）
formula_1.db--drivers--driverId  → 无歧义（driverId 唯一）
formula_1.db--*:Table            → 只匹配 tag 含 Table 的邻接节点
formula_1.db--*:Table--*:col     → 只匹配 tag 匹配 col 的邻接节点
```

Finder 在遍历时，对每个邻接节点检查 `_tags` 做过滤（层级匹配）。

## 6. 调用方更新

### 6.1 Extractor 模块

**db_basic.py** — 最大的改动：

```python
# 当前
store.create_node(rel_path, meta=meta, namespaces=["file"])
store.create_node(f"{rel_path}::{safe_name}.table", meta={...})
col_entity_name = f"{safe_name}.{safe_col}.{col_type}.col"
store.create_node(f"{rel_path}::{col_entity_name}", meta={...})

# 重构后
store.create_node(rel_path, meta=meta, tags=["file/db"])
store.create_node(f"{rel_path}--{safe_name}",
                  meta={...}, tags=["table"])
store.create_node(f"{rel_path}--{safe_name}--{safe_col}",
                  meta={"col_type": col_type, ...},
                  tags=[f"col/{col_type}"],  # "col/INT"
                  edges=[{"a": safe_name, "b": safe_col}])
```

注意 `--` 结构变深：`db--table--col` 三层（当前是 `db::table.col` 两层，因为类型后缀把 col 附在 table 名下）。

**其他 extractor**：

| 文件 | 改动 |
|---|---|
| `text_basic.py` | `namespaces=["file"]` → `tags=["file/text"]` |
| `serialized_basic.py` | `namespaces=["file"]` → `tags=["file/json"]` 等 |
| `csv_basic.py` | `namespaces=["file"]` → `tags=["file/csv"]`；col 去后缀 |
| `db_table_relations.py` | `.rel` 后缀从名中移除，`tags=["rel"]` |
| `ai_db_column_rel.py` | 同上 |
| `db_column_overlap.py` | `.overlap` 后缀移除，`tags=["overlap"]` |
| `db_column_sketch_overlap.py` | 同理 |
| `json_pattern.py` | 确认是否有类型后缀需要去除 |

### 6.2 Tool 层

**`tool_use/create_entity/tool.py`**：

```python
# 当前
if _is_knowledge_entity(ref):
    namespaces = ["knowledge", "global"]
store.create_node(ref, meta=meta, edges=edges, namespaces=namespaces)

# 重构后
if _is_knowledge_entity(ref):
    suffix = ref.rsplit(".", 1)[-1]  # "convention"
    tags = [f"knowledge/{suffix}"]   # ["knowledge/convention"]
workspace.create_entity(ref, meta=meta, edges=edges, tags=tags)
# 路由自动完成（.convention → global routing rule）
```

**`tool_use/glob/tool.py`**、**`tool_use/grep/tool.py`**：

`store.find_nodes(pattern)` → `workspace.finder.find(pattern)`

### 6.3 Agent 层

**`agent/agent.py`**：

```python
# 当前
self.store = Store(project_path, project=project_name)

# 重构后
self.workspace = Workspace(project_path=project_path)
self.store = self.workspace.get_store()
```

Agent 暴露 `self.workspace` 给 tools。

### 6.4 脚本层

所有 `Store(path)` → `Workspace(project_path=path).get_store()` 或直接用 Workspace。

## 7. 数据迁移

### 7.1 实体重命名

最复杂的部分。存量实体的 `_entity_name` 包含类型后缀，需要去掉：

```python
# 迁移脚本
for meta_file in glob("**/.pontis/nodes/*/_meta.yml"):
    data = yaml.safe_load(open(meta_file))
    old_name = data.get("_entity_name", "")

    # 推断类型并去除后缀
    new_name, type_tags = strip_type_suffix(old_name)

    data["_entity_name"] = new_name
    old_ns = data.pop("_namespaces", [])
    # scope 标签丢弃（由物理位置推导），保留 type 标签
    type_tags += [t for t in old_ns if t not in KNOWN_PROJECT_NAMES and t != "global"]
    data["_tags"] = type_tags

    yaml.dump(data, open(meta_file, 'w'), ...)
```

### 7.2 边重命名

`_edges.yml` 中的边引用 entity_name，需要同步更新。

### 7.3 全局 Store

`~/.pontis/` 下实体保持原位。config 中 `global` project 指向 `~/`。

## 8. 实施顺序

| 阶段 | 内容 | 预计改动 |
|---|---|---|
| **Phase 1** | 新建 `storage/config.py` | ~80 行新文件 |
| **Phase 2** | 新建 `storage/finder.py`（Cypher URN 解析 + 查询） | ~300 行新文件 |
| **Phase 3** | 新建 `storage/workspace.py` | ~60 行新文件 |
| **Phase 4** | 精简 `storage/store.py`（删查询/路由，改签名） | 删 ~350 行 |
| **Phase 5** | 更新 extractor（去类型后缀 + tags） | 8 个文件 |
| **Phase 6** | 更新 tool 调用方 | 4 个 tool 文件 |
| **Phase 7** | 更新 agent 层 | agent.py |
| **Phase 8** | 数据迁移脚本 | 1 个脚本 |
| **Phase 9** | 测试验证 | 全量回归 |

## 9. 验证方案

1. **功能回归**：对一个 BIRD 数据库跑全量 extractor pipeline，对比实体数量
2. **Cypher URN 查询**：
   - `finder.find("*.db")` → 所有 .db 文件
   - `finder.find("*:file")` → 所有 file 标签实体
   - `finder.find("formula_1.db--*:Table")` → 表节点
   - `finder.find("formula_1.db--*:Table--*:col")` → 列节点
   - `finder.find("formula_1.db--*:Table--*:col/INT")` → INT 列
   - `finder.find("*:file|knowledge")` → file 或 knowledge 标签
   - `finder.find("*:knowledge:convention")` → 知识类 convention 实体（跨 Store）
3. **层级标签匹配**：
   - 查 `col` → 匹配 `col/INT`, `col/TEXT`, `col/REAL`
   - 查 `INT` → 匹配 `col/INT`
   - 查 `file` → 匹配 `file/db`, `file/csv`, `file/json`
4. **路由验证**：
   - `workspace.create_entity("AVG_NEEDS_GROUP_BY.convention", tags=["knowledge/convention"])` → global store
   - `workspace.create_entity("formula_1.db", tags=["file/db"])` → 项目 store
5. **AI 零感知**：create_entity tool 不传 project，路由自动完成
6. **无配置兼容**：无 pontis.yml 时 fallback 到当前行为
