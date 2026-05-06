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
- Enricher 用实体名后缀判断类型（enricher/__init__.py:48），和命名耦合
- Guardrail 直接访问 Store 私有 API（`_id_index`, `_adjacent`, `_ensure_index`）
- 19 处 `.replace(".col","").split(".")` 解析逻辑分散在 15+ 个 extractor 中

## 设计决策

| 决策 | 选择 | 原因 |
|---|---|---|
| 创建层级表达 | `--` 统一用于创建和查询 | 与查询语法一致，减少概念数 |
| 列实体发现 | 边遍历（Finder） | 类型信息从名称移到 `_labels`，名称不再编码层级 |
| 重构范围 | 全量（Store+Finder+Extractor+Enricher+Guardrail+Tools） | 一步到位，避免兼容层债务 |
| 跨项目边 | Store 存外部引用，Finder 解析 | Store 不感知跨项目 |
| 未挂载项目可见性 | 自动隐藏 | 类似 Linux 挂载语义 |

## 2. 目标架构

```
┌─────────────────────────────────────────────────┐
│  Tools (glob, grep, meta, create_entity...)     │  用户空间
├─────────────────────────────────────────────────┤
│  Finder                                         │  查询引擎（类似 libc）
│  - Cypher URN 解析（`--` 遍历, `:Tag` 过滤）   │
│  - 图遍历（`--` 分段，创建和查询统一）            │
│  - 层级标签匹配                                   │
│  - 跨 project 聚合                               │
├─────────────────────────────────────────────────┤
│  Store                                          │  存储原语（类似 VFS）
│  - 实体 CRUD（纯标识符，无类型后缀）               │
│  - 边 CRUD                                      │
│  - 内存索引（id/name/inode/adjacent）            │
│  - _labels 存储与层级匹配（唯一理解的元属性）       │
├─────────────────────────────────────────────────┤
│  Enricher                                       │  虚属性计算
│  - 按 label 注册属性计算模块                        │
│  - 在 get_meta() 时自动补充                       │
├─────────────────────────────────────────────────┤
│  Probe                                          │  虚实体探测
│  - 可插拔虚实体发现（FilesystemProbe 内置）      │
│  - Store 注册，Finder 透明查询                    │
├─────────────────────────────────────────────────┤
│  Config                                         │  配置层
│  - project → path 映射                           │
│  - default project                               │
│  - routing 规则（实体名模式 → project）            │
└─────────────────────────────────────────────────┘
```

以上所有组件均在 `storage/` 目录下：

```
storage/
├── __init__.py
├── config.py          # StoreConfig + ProjectEntry + RoutingRule
├── store.py           # Store（存储原语）
├── finder.py          # Finder（查询引擎）
├── workspace.py       # Workspace（顶层容器）
├── enricher.py        # Enricher（虚属性计算）
├── probe.py           # Probe 接口 + FilesystemProbe
└── modules/           # 按 tag 注册的属性计算模块
    ├── table_props.py
    ├── col_props.py
    ├── db_props.py
    ├── csv_props.py
    └── ...
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

| 旧命名 | 新命名 | _labels |
|---|---|---|
| `formula_1.db` | `formula_1.db` | `["file/db"]` |
| `drivers.table` | `drivers` | `["table"]` |
| `driverId.INT.col` | `driverId` | `["col/INT"]` |
| `no_concat.convention` | `no_concat.convention` | `["knowledge/convention"]` |

类型信息从实体名移到 `_labels` 元数据，标签用 `/` 层级组织。

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
    def create_node(self, ref, *, meta=None, edges=None, labels=None)
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
        """返回 [(entity_name, labels_list), ...]
        类似 getdents + d_type，用于 Finder 快速过滤"""
    def neighbors(self, ref) -> List[str]
        """返回邻接 entity_name 列表"""

    # Probe（可插拔虚实体探测）
    def register_probe(self, probe: Probe)
    def unregister_probe(self, probe: Probe)
    def probe_discover(self, pattern: str) -> List[str]
        """运行所有已注册 probe，返回虚实体名列表"""
    def probe_get_meta(self, name: str) -> Optional[dict]
        """从 probe 获取虚实体基础元数据"""

    # Cache
    def cache_path(self, *parts) -> str
    def cache_find(self, pattern) -> list

    # 属性
    @property
    def project_path(self) -> str
    @property
    def pontis_exists(self) -> bool
```

**Probe 接口** — `storage/probe.py`（与 Enricher 同级）：

```python
from abc import ABC, abstractmethod

class Probe(ABC):
    """可插拔虚实体探测脚本。"""
    @abstractmethod
    def discover(self, store, pattern: str) -> List[str]:
        """发现匹配 pattern 的虚实体，返回 entity_name 列表"""

    @abstractmethod
    def get_meta(self, store, name: str) -> Optional[dict]:
        """为虚实体提供基础元数据（不含 _labels，不含虚属性）"""

class FilesystemProbe(Probe):
    """内置 Probe：扫描项目目录下的物理文件。"""
    def discover(self, store, pattern): ...
    def get_meta(self, store, name): ...
```

**虚实体的属性全是虚的**：Probe 提供 `get_meta()` 返回基础信息（path, size 等），enricher 在 `get_meta()` 时补充虚属性（file_size, modified_at 等）。真实体也可以有虚属性。两者独立：

| | 实体 | 属性 |
|---|---|---|
| **实** | .pontis/nodes/ 下有 _meta.yml | meta.yml 中存储的字段 |
| **虚** | Probe 发现，不持久化 | Probe.get_meta() + Enricher 补充 |

**删除的方法/属性**：

| 删除 | 原因 |
|---|---|
| `_GLOBAL_ROOT`, `_is_global`, `_get_global_store()` | 路由由 Config + Finder 管理 |
| `_apply_namespaces()` | 改为 `_apply_labels()` |
| `_parse_glob()` | 移到 Finder |
| `_global_match()`, `_traverse_match()` | 移到 Finder |
| `find_nodes()` | 移到 Finder |
| `_find_files()` | 改为 `scan_files()` Probe |
| `walk_metas()`, `find_connected()` | 移到 Finder |
| `resolve_ref()` | 拆分：`::` 遍历移到 Finder，简单名查找保留 |

**`create_node` 签名**：

```python
def create_node(self, ref, *, meta=None, edges=None, labels=None):
```

`tags` 是 `List[str]`，支持层级（如 `["col/INT", "primary_key"]`）。Store 不解释含义，原样存入 `_labels` 字段。

**`create_node` 的 `--` 语义**：

创建和查询统一使用 `--`。`create_node` 收到含 `--` 的 ref 时：

```python
# 创建：ref 含 -- → 自动建父子边
store.create_node("formula_1.db--drivers",
                  labels=["table"],
                  meta={...})
# 等效于：创建实体 drivers，自动建边 formula_1.db → drivers

store.create_node("formula_1.db--drivers--driverId",
                  labels=["col/INT"],
                  meta={...})
# 两段 --：创建 driverId，自动建边 drivers → driverId
```

Store 内部拆分 `--` 段，最后一段是新实体名，前面的段通过 `node_exists` 定位父节点，自动建边。

**层级标签约定**：

```
文件实体：   file/db, file/csv, file/json, file/text
数据库实体： table, view, col/INT, col/TEXT, col/REAL, col/FLOAT
关系实体：   rel, overlap, disambig
知识实体：   knowledge/convention, knowledge/pattern, knowledge/lesson, knowledge/term, knowledge/example
```

不带点号（`file/db` 而非 `file/.db`），与 `col/INT` 保持一致。

**Store 实例 = 单项目**：每个 Store 绑定一个物理路径、一个项目。`project://` 的解析和多 Store 调度由 Finder 处理，Store 不感知其他项目。

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
    def find(self, urn: str) -> List[Tuple[str, List[str]]]
        """Cypher 风格 URN 查询，返回 [(entity_name, labels), ...]"""

    def find_in_project(self, project: str, urn: str) -> List[Tuple[str, List[str]]]

    # 高级查询
    def walk_all(self) -> Iterator[Tuple[str, str, dict]]
        """遍历所有 project，yield (project, entity_name, meta)"""

    # 路由
    def route_create(self, entity_name: str) -> Store
        """根据 config routing 规则返回目标 Store"""

    # 可见性
    def is_mounted(self, project: str) -> bool
        """检查 project 是否在已挂载列表中"""
```

**Tool 显示配置**：

`tool_use/config.py` 中的 `INFO_TYPE_CONFIG` 重构为按标签段索引，`info_fn` 返回 `dict` 而非 `str`：

```python
INFO_TYPE_CONFIG = {
    # 文件段
    "file":     InfoTypeConfig(info_fn=lambda m: {"size": _v(m, "file_size")}),
    "db":       InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m,'table_count')} tables, {_v(m,'view_count')} views"}),
    "csv":      InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m,'row_count')} rows, {_v(m,'column_count')} cols"}),
    "json":     InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m,'structure_type')}, {_v(m,'line_count')} lines"}),
    "md":       InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m,'line_count')} lines"}),
    # 结构段
    "table":    InfoTypeConfig(info_fn=lambda m: {
                    "stats": f"{_v(m,'row_count')} rows, {_v(m,'column_count')} cols",
                    "links": ", ".join(filter(None, [_c(m,'fk'), _c(m,'rel')])),
                }),
    "col":      InfoTypeConfig(info_fn=lambda m: {
                    "links": ", ".join(filter(None, [_c(m,'rel'), _c(m,'fk')])) or "-",
                }),
    # 关系段
    "fk":       InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    "rel":      InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    "overlap":  InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    "disambig": InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    # 知识段（knowledge 本身不配，只有具体类型配）
    "pattern":     InfoTypeConfig(info_fn=lambda m: {"pattern": _v(m, "pattern")}),
    "convention":  InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    "term":        InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    "lesson":      InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    "example":     InfoTypeConfig(info_fn=lambda m: {"brief": _v(m, "brief")}),
    # 其他
    "chunk":    InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m, 'char_count')} chars"}),
    "directory": InfoTypeConfig(info_fn=lambda m: {"stats": f"{_v(m, 'file_count')} files, {_v(m, 'subdir_count')} dirs"}),
}
```

**叠加逻辑**：每个标签按 `/` 拆分为独立段，每段在 config 中查找匹配。有匹配则调用 `info_fn(meta)` 得到 dict，无匹配则跳过（如 `knowledge`、`INT` 等纯分类段）。所有段的 dict 按 key 合并（后者覆盖前者），天然去重——`brief` 无论几个段贡献只出现一次。

```python
def resolve_info(entity_labels, meta):
    merged = {}
    for label in entity_labels:
        for segment in label.split("/"):
            if segment in INFO_TYPE_CONFIG:
                merged.update(INFO_TYPE_CONFIG[segment].info_fn(meta))
    return " | ".join(v for v in merged.values() if v and v != "-") or "-"
```

**渲染格式**：四列等价，`\t` 分隔。格式：`project:// \t name \t :label1/label2 \t info`。

```
formula_1://	formula_1	:file/db	1.2 MB | 5 tables, 2 views | F1 官方数据库
formula_1://	drivers	:table	857 rows, 5 cols | 1 fk, 2 rels | 车手信息表
formula_1://	driverId	:col/INT	2 rels, 1 fk | 车手唯一标识
formula_1://	driverRef	:col/STR	1 rel | 车手代号
formula_1://	driverId→results	:fk	关联到 results.driverId
global://	date_format	:knowledge/pattern	\d{4}-\d{2}-\d{2} | 标准日期格式
global://	naming	:knowledge/convention	表名使用蛇形命名法
```

多个独立标签用空格分隔：`:label1 :label2/label3`。

**`META_TYPE_CONFIG` 同理**：key 从后缀改为标签段，多标签取 `default_keys` 并集、`folded_keys` 并集。

**`meta` 命令的两种模式**：

`meta` 接受可选的邻居标签参数，按标签沿边筛选相邻实体：

```python
def meta(ref, neighbor_label=None):
    own_meta = store.get_meta(ref)
    if neighbor_label is None:
        # 模式1：显示自身 meta + related 分组
        return format_own_meta(own_meta) + format_related(ref)
    else:
        # 模式2：沿边找 label 匹配的邻居，按 glob 格式显示
        neighbors = store.neighbors(ref)
        matched = [n for n in neighbors
                   if finder._label_matches(store.get_labels(n), neighbor_label)]
        return format_neighbor_list(matched)
```

**模式1：`meta(driverId)`** — 无邻居筛选，显示自身 meta + `related:` 分组：

```
formula_1://	driverId	:col/INT

  cardinality      857
  null_count       0
  null_percentage  0.0%
  sample           [1, 2, 3, 4, 5]
  min_value        1
  max_value        857
  brief            车手唯一标识

related:
  db:
    formula_1	:file/db	1.2 MB | 5 tables, 2 views | F1 官方数据库
  table:
    drivers	:table	857 rows, 5 cols | 1 fk, 2 rels | 车手信息表
  fk:
    driverId→results	:fk	关联到 results.driverId
  rel:
    driverId∼results	:rel	值分布关联
```

**模式2：`meta(driverId, fk)`** — 只显示 fk 标签的邻居：

```
driverId→results	:fk	关联到 results.driverId
```

**邻居显示规则**：
- 同项目邻居省略 `project://` 前缀（与 URN 省略规则一致）
- 跨项目邻居显示 `project://` 前缀（如 `global://`）
- `related:` 的分组 key 取邻居主 label 的首段（`col/INT` → 分到 `col` 组）
- 所有邻居渲染复用 `INFO_TYPE_CONFIG` 的 `resolve_info()` 逻辑

    # 内部
    def _parse_urn(self, urn) -> URNParsed
    def _segment_match(self, store, segment) -> List[str]
    def _traverse_match(self, store, segments) -> List[str]
```

**跨项目边与可见性**：

Store 的 `_edges.yml` 支持两种边：

```yaml
edges:
  # 项目内边：ent_id 对
  - nodes: [ent_abc123, ent_def456]

  # 跨项目边：一个 ent_id + 外部引用
  - nodes: [ent_abc123]
    external: {project: "global", entity: "AVG_NEEDS_GROUP_BY.convention"}
```

**可见性规则**：Finder 遍历边时，遇到 `external` 引用会检查目标 project 是否已挂载（`is_mounted()`）。未挂载的 project 的边被自动隐藏——不会出现在查询结果中。

行为类比 Linux 挂载：`/mnt/usb` 没挂载时，路径存在但内容不可达。Finder 只返回当前上下文可达的实体，避免出现「找到了实体但读不到 meta」的情况。

**URN 解析结果**：

```python
@dataclass
class Segment:
    project: Optional[str] = None    # Project:// 段（每段独立）
    pattern: str = "*"               # 名称 glob 模式
    labels_and: List[str] = []         # : 分隔的 AND 标签
    labels_or: List[str] = []          # | 分隔的 OR 标签

@dataclass
class URNParsed:
    segments: List[Segment]          # 单段或 -- 连接的多段
```

**解析规则**：

```
b.py:file
  ↓
segments = [Segment(project=None, pattern="b.py", labels_and=["file"])]

project_a://*:Table:Large
  ↓
segments = [Segment(project="project_a", pattern="*", labels_and=["Table", "Large"])]

(project_a://formula_1.db:file/db)--(*:Table)--(driverId:col/INT)
  ↓
segments = [
    Segment(project="project_a", pattern="formula_1.db", labels_and=["file/db"]),
    Segment(project=None, pattern="*", labels_and=["Table"]),
    Segment(project=None, pattern="driverId", labels_and=["col/INT"]),
]

(project_a://formula_1.db)--(global://*:knowledge/convention)
  ↓
segments = [
    Segment(project="project_a", pattern="formula_1.db"),
    Segment(project="global", pattern="*", labels_and=["knowledge/convention"]),
]
```

**层级标签匹配**：

```python
def _label_matches(self, entity_labels: List[str], query_tag: str) -> bool:
    """查询标签匹配实体标签的任意路径段或子路径。

    存储标签 "col/INT/primary_key" 可被以下查询匹配：
    - "col" (首段), "INT" (中段), "primary_key" (末段)
    - "col/INT" (子路径), "col/INT/primary_key" (完全匹配)
    - "INT/primary_key" (子路径)
    """
    for stored_label in entity_labels:
        # 生成所有子路径
        segments = stored_label.split("/")
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
解析 URN: segments=[Segment(project=None, pattern="*", labels_and=["knowledge/convention"])]
  ↓
单段查询，搜索范围: all_stores()（project 未指定）
  ↓
对每个 store:
  store.list_all() → 过滤 _labels 层级匹配 "knowledge/convention" + fnmatch "*"
  ↓
合并去重，返回
```

```
输入: "(formula_1.db)--(*:Table)--(*:col)"
  ↓
解析 URN: segments=[
    Segment(project=None, pattern="formula_1.db"),
    Segment(project=None, pattern="*", labels_and=["Table"]),
    Segment(project=None, pattern="*", labels_and=["col"]),
]
  ↓
多段遍历，project 均未指定 → default_store()
  ↓
_traverse_match():
  第一段：匹配 "formula_1.db"
  第二段：neighbors + fnmatch "*" + label 匹配 "Table"
  第三段：neighbors + fnmatch "*" + label 层级匹配 "col"
  ↓
返回匹配的 entity_name 列表
```

```
输入: "(project_a://formula_1.db)--(global://*:knowledge/convention)"
  ↓
跨 Store 遍历：
  第一段在 project_a store 找到 formula_1.db
  第二段在 global store 搜索 label 匹配 knowledge/convention
  合并结果
```

**标签过滤逻辑（AND + OR）**：

```python
def _match_labels(self, entity_labels: List[str],
                labels_and: List[str], labels_or: List[str]) -> bool:
    """AND: 所有 labels_and 必须匹配（层级）。OR: 至少一个 labels_or 必须匹配。"""
    if labels_and and not all(self._label_matches(entity_labels, t) for t in labels_and):
        return False
    if labels_or and not any(self._label_matches(entity_labels, t) for t in labels_or):
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
                      labels=None, project=None) -> str:
        """统一创建入口。路由逻辑：
        1. 显式 project 参数 → 直接用
        2. config routing 规则匹配 → 自动路由
        3. 兜底 → default project"""
        target = project or self._config.route_entity(ref) or self._config.default_project()
        store = self.get_store(target)
        store.create_node(ref, meta=meta, edges=edges, labels=tags)
```

### 4.5 Enricher — `storage/enricher.py`（从 `enricher/` 迁入）

Enricher 从项目根目录的 `enricher/` 迁入 `storage/` 层，与 Probe 同级。属性计算模块从 `enricher/modules/` 迁入 `storage/modules/`。

当前 Enricher 通过实体名后缀判断类型（`enricher/__init__.py:48`）：

```python
# 当前：看名称最后一个 .xxx
suffix = ("." + name.split(".")[-1].lower()) if "." in name else ""
if suffix in PROP_REGISTRY: ...
```

重构后改为读 `_labels`：

```python
# 重构后：读 _labels 字段
labels = meta.get("_labels", [])
if "table" in labels or any(t.startswith("table") for t in labels):
    _apply_group(result, TABLE_PROPS, ...)
elif "col" in labels or any(t.startswith("col/") for t in labels):
    _apply_group(result, COL_PROPS, ...)
elif "file" in labels or any(t.startswith("file/") for t in labels):
    suffix_map = {"file/db": DB_PROPS, "file/csv": CSV_PROPS, ...}
    for label in labels:
        if label in suffix_map:
            _apply_group(result, suffix_map[label], ...)
            break
```

`storage/modules/` 下的各模块按 tag 注册表重组，替代当前的 `.suffix` 匹配。

### 4.6 Guardrail 公共 API

当前 3 个 guardrail 文件直接访问 Store 私有属性：

| 文件 | 私有 API | 用途 |
|---|---|---|
| `agent/guardrail/sql_join_check.py:138-139` | `_ensure_index()`, `_id_index` | 遍历所有实体找 DB 文件 |
| `agent/guardrail/sql_utils.py:133-143` | `_ensure_index()`, `_id_index` | 遍历实体找表/列 |
| `agent/guardrail/sql_disambig_check.py:108-118` | `_ensure_index()`, `_id_index`, `_adjacent` | 遍历+邻接查询 |
| `tool_use/find_path/tool.py:18-23` | `_ensure_index()`, `_id_index` | 遍历所有实体 |

重构后全部改为使用 Finder：

```python
# 当前
store._ensure_index()
for eid, ref in store._id_index.items():
    if ref.endswith(".db"): ...

# 重构后
for ref in finder.find("*:file/db"):  # 层级标签匹配
    ...
```

Guardrail 接收 `workspace`（含 Finder）替代直接访问 Store 私有 API。

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
                  labels=[f"col/{col_type}"],  # "col/INT"
                  edges=[{"a": safe_name, "b": col_entity_name}])
```

**影响范围**：

| 文件 | 当前实体名 | 重构后 | _labels |
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

同时 `_labels` 中存 `["knowledge/convention"]`，层级标签同时标记大类和具体类型。

### 5.3 遍历与歧义

当前 `::` 遍历依赖类型后缀消歧（`formula_1.db::drivers.table` 不会匹配到 `drivers.view`）。去除后缀后，同名消歧改用 Cypher 风格标签过滤：

```
formula_1.db--drivers            → 无歧义（drivers 唯一）
formula_1.db--drivers--driverId  → 无歧义（driverId 唯一）
formula_1.db--*:Table            → 只匹配 label 含 Table 的邻接节点
formula_1.db--*:Table--*:col     → 只匹配 label 匹配 col 的邻接节点
```

Finder 在遍历时，对每个邻接节点检查 `_labels` 做过滤（层级匹配）。

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
store.create_node(rel_path, meta=meta, labels=["file/db"])
store.create_node(f"{rel_path}--{safe_name}",
                  meta={...}, labels=["table"])
store.create_node(f"{rel_path}--{safe_name}--{safe_col}",
                  meta={"col_type": col_type, ...},
                  labels=[f"col/{col_type}"],  # "col/INT"
                  edges=[{"a": safe_name, "b": safe_col}])
```

注意 `--` 结构变深：`db--table--col` 三层（当前是 `db::table.col` 两层，因为类型后缀把 col 附在 table 名下）。

**其他 extractor**：

| 文件 | 改动 |
|---|---|
| `text_basic.py` | `namespaces=["file"]` → `labels=["file/text"]` |
| `serialized_basic.py` | `namespaces=["file"]` → `labels=["file/json"]` 等 |
| `csv_basic.py` | `namespaces=["file"]` → `labels=["file/csv"]`；col 去后缀 |
| `db_table_relations.py` | `.rel` 后缀从名中移除，`labels=["rel"]` |
| `ai_db_column_rel.py` | 同上 |
| `db_column_overlap.py` | `.overlap` 后缀移除，`labels=["overlap"]` |
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
    labels = [f"knowledge/{suffix}"]   # ["knowledge/convention"]
workspace.create_entity(ref, meta=meta, edges=edges, labels=tags)
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

### 6.4 Prompt 层

**`agent/prompt/_base.py`** — URN 语法更新：

```python
# 当前
"""
pattern := segment ("::" segment)*
segment := (label":")? match_pattern

glob "formula_1.db::*.table::*.col"
glob "file:*.convention"
"""

# 重构后
"""
URN 格式：[Project://]Pattern[:Label1[:Label2[|Label3]]]
多段遍历：(Segment)--(Segment)--(...)

glob "formula_1.db"                           → 单段名称匹配
glob "formula_1.db--*:Table"                   → 两段遍历
glob "formula_1.db--*:Table--*:col"            → 三段遍历
glob "formula_1.db--*:Table--*:col/INT"        → 层级 label 过滤
glob "*:file"                                  → label 过滤
glob "*:file|knowledge"                        → OR 过滤
meta drivers                                   → 自身 meta + related 分组
meta drivers col                               → 只看 col label 的邻居
"""
```

**`agent/prompt/_namespace.py`** — label 体系替换 namespace：

```python
# 当前：tags + scope 双维度
# file / knowledge 标签，project / global 作用域

# 重构后：层级 label 统一
"""
实体通过 _labels 字段标记类型。Label 支持层级（/ 分隔）：
  file/db, file/csv, file/json    → 文件实体
  table, view                     → 结构实体
  col/INT, col/TEXT, col/REAL     → 列实体（含数据类型）
  fk, rel, overlap, disambig      → 关系实体
  knowledge/convention, knowledge/pattern, knowledge/term, knowledge/lesson, knowledge/example
                                  → 知识实体

创建实体时不需要指定 project，系统自动路由。
"""
```

**`agent/prompt/_entities.py`** — 去掉类型后缀命名规则：

```python
# 当前：实体名编码类型
# drivers.table, driverId.INT.col, fk_drivers.rel

# 重构后：实体名不含类型后缀，类型由 _labels 表达
# drivers（labels: ["table"]）
# driverId（labels: ["col/INT"]）
# fk_drivers（labels: ["rel"]）

# 知识实体例外：.convention 等后缀是名称本身的一部分，保留
# AVG_NEEDS_GROUP_BY.convention（labels: ["knowledge/convention"]）
```

**`agent/prompt/_sql.py`** — 查询语法更新：

```python
# 当前
# glob "formula_1.db::*.table::*.col"
# FK 名编码 JOIN 条件：orders.user_id__to__users.id.fk

# 重构后
# glob "(formula_1.db)--(*:Table)--(*:col)"
# FK 信息存在 meta.brief 中，不再编码在名称里
```

**`agent/prompt/_writer.py`** — 创建实体简化：

```python
# 当前：需指定 namespace
# create_entity(ref="rule.convention", namespaces=["knowledge", "global"])

# 重构后：只需 ref，路由和 label 自动推断
# create_entity(ref="rule.convention")
```

**`agent/prompt/_reflection.py`** — label 替代 namespace：

```python
# 当前：knowledge 实体打 namespace=["knowledge", "global"]
# 重构后：自动打 labels=["knowledge/convention"]，路由到 global store
```

**`agent/prompt/_project.py`** — 动态扫描改用 label 过滤：

```python
# 当前：扫描 _namespaces 字段
# 重构后：扫描 _labels 字段，用 label 匹配统计各类型数量
```

### 6.5 脚本层

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
    new_name, type_labels = strip_type_suffix(old_name)

    data["_entity_name"] = new_name
    old_ns = data.pop("_namespaces", [])
    # scope 标签丢弃（由物理位置推导），保留 type 标签
    type_labels += [t for t in old_ns if t not in KNOWN_PROJECT_NAMES and t != "global"]
    data["_labels"] = type_labels

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
| **Phase 5** | 更新 extractor（去类型后缀 + labels） | 8 个文件 |
| **Phase 6** | 更新 tool 调用方 | 4 个 tool 文件 |
| **Phase 7** | 更新 agent 层 | agent.py |
| **Phase 8** | 更新 prompt 层（_base, _namespace, _entities, _sql, _writer, _reflection, _project） | 7 个 prompt 文件 |
| **Phase 9** | 数据迁移脚本 | 1 个脚本 |
| **Phase 10** | 测试验证 | 全量回归 |

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
   - `workspace.create_entity("AVG_NEEDS_GROUP_BY.convention", labels=["knowledge/convention"])` → global store
   - `workspace.create_entity("formula_1.db", labels=["file/db"])` → 项目 store
5. **AI 零感知**：create_entity tool 不传 project，路由自动完成
6. **无配置兼容**：无 pontis.yml 时 fallback 到当前行为
