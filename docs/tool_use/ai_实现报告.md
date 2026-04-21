# Pontis Tool Use 实现报告

> 更新时间: 2026-04-21

---

## 目录结构

```
tool_use/
├── __init__.py
├── glob/                 # ✅ 已实现
│   ├── __init__.py
│   ├── prompt.py
│   └── tool.py
├── grep/                 # ✅ 已实现
│   ├── __init__.py
│   ├── prompt.py
│   └── tool.py
├── read/                 # ✅ 已实现
│   ├── __init__.py
│   ├── prompt.py
│   └── tool.py
├── meta/                 # ✅ 已实现
│   ├── __init__.py
│   ├── prompt.py
│   └── tool.py
├── lookup/               # ✅ 已实现 (含 LSH 索引加速)
│   ├── __init__.py
│   ├── prompt.py
│   └── tool.py
├── search/               # ✅ 已实现 (BM25)
│   ├── __init__.py
│   ├── prompt.py
│   └── tool.py
├── bash/                 # ✅ 已实现
│   ├── __init__.py
│   ├── prompt.py
│   └── tool.py
├── create_entity/        # ✅ 已实现
│   ├── __init__.py
│   ├── prompt.py
│   └── tool.py
├── update_meta/          # ✅ 已实现
│   ├── __init__.py
│   ├── prompt.py
│   └── tool.py
├── add_edge/             # ✅ 已实现 (无向边 + required_by)
│   ├── __init__.py
│   ├── prompt.py
│   └── tool.py
├── delete/               # ✅ 已实现 (级联删除)
│   ├── __init__.py
│   ├── prompt.py
│   └── tool.py
├── sub_agent/            # ✅ 已实现 (子智能体)
│   ├── __init__.py
│   ├── prompt.py
│   └── tool.py
└── utils/
    ├── __init__.py
    ├── path_parser.py    # path::entity 解析器
    ├── config.py         # 分页/类型配置
    └── formatters.py     # 格式化工具
```

---

## 工具注册

### Readonly 模式 (7 个工具)

`build_readonly_registry()` — 数据探索，不修改图谱。

| 工具 | 说明 |
|------|------|
| **glob** | 按模式查找节点（`::` 支持无向遍历） |
| **grep** | 正则搜索文件内容（ripgrep + Python fallback） |
| **read** | 读取文件/实体内容（文本、图片、PDF、SQLite 表、JSON） |
| **meta** | 查看节点元数据 + 虚属性（按实体类型分组的邻接节点） |
| **lookup** | 值检索（DB 列/JSON 值，三层查询：元数据 → LSH → SQL） |
| **search** | BM25 语义搜索（CJK bigram 分词，检索 brief/detail 字段） |
| **bash** | Shell 命令执行（subprocess，timeout 控制） |

### Writer 模式 (12 个工具)

`build_writer_registry()` = readonly 全部工具 + 写入工具 + 子智能体。

| 工具 | 说明 |
|------|------|
| **create_entity** | 创建实体节点（自动添加归属边） |
| **update_meta** | 更新节点元数据（brief/detail 等） |
| **add_edge** | 添加无向边（`{a, b, required_by}`） |
| **delete** | 删除节点（按 `required_by` 级联删除） |
| **agent** | 创建子智能体执行任务（writer 能力，无动态项目概览） |

---

## 各工具详细说明

### 1. glob ✅

**文件**: `tool_use/glob/tool.py`

```python
glob_command(store, path_pattern, offset=0, limit=None) -> str
```

- `path_pattern` 支持 `::` 无向遍历：`*.db::*.table`、`*.table::*.db`
- 先匹配节点，再按 pattern 过滤
- 分页支持（offset/limit）

### 2. grep ✅

**文件**: `tool_use/grep/tool.py`

```python
grep_command(store, pattern, path="", output_mode="files_with_matches",
             glob=None, ignore_case=False, head_limit=250, offset=0) -> str
```

- ripgrep subprocess + Python re fallback
- 三种输出模式：content / files_with_matches / count
- 自动排除 `.git`、`.pontis`

### 3. read ✅

**文件**: `tool_use/read/tool.py`

```python
read_command(store, file_path, offset=None, limit=None) -> str
```

- `path::entity` 语法：`data.db::users.table`
- 支持：文本（cat-n）、SQLite 表、JSON、图片、PDF、Notebook
- 实体读取：`.table`（SQL 查询）、`.col`（列数据）

### 4. meta ✅

**文件**: `tool_use/meta/tool.py`

```python
meta_command(store, path, all=False, property=None) -> str
```

- 查看存储属性 + 虚属性
- 虚属性按邻接节点实体类型自动分组：`{table: [...], col: [...], view: [...]}`

### 5. lookup ✅

**文件**: `tool_use/lookup/tool.py`

```python
lookup_command(store, file_pattern, type, predicate,
               output_mode="distinct_count", offset=0, limit=None) -> str
```

- **三层查询策略**：
  1. 元数据预过滤（min/max/cardinality）
  2. LSH 索引查询（O(1) 等值，KLL 范围预估）
  3. SQL 兜底（带谓词下推）
- 支持 DB 列和 JSON 值检索
- Predicate：`=`, `!=`, `>`, `<`, `>=`, `<=`

### 6. search ✅

**文件**: `tool_use/search/tool.py`

```python
search_command(store, path_pattern, query, offset=0, limit=None) -> str
```

- BM25 搜索（k1=1.5, b=0.75）
- CJK bigram 分词 + ASCII word splitting
- 仅检索 `brief` 和 `detail` 字段
- `path_pattern` 范围限制

### 7. bash ✅

**文件**: `tool_use/bash/tool.py`

```python
bash_command(command, cwd, timeout_ms=120000) -> str
```

- subprocess pass-through
- stdout + stderr 合并输出
- 超时控制（默认 120s）

### 8. create_entity ✅

**文件**: `tool_use/create_entity/tool.py`

```python
create_entity_command(store, ref, meta=None, edges=None) -> str
```

- ref 必须含 `::`（`path::entity_name`）
- 自动生成 ent_id，创建 meta 文件
- 自动添加归属边（`file ↔ entity`，`required_by: [entity]`）
- 可选同时添加额外边

### 9. update_meta ✅

**文件**: `tool_use/update_meta/tool.py`

```python
update_meta_command(store, ref, fields) -> str
```

- 合并写入，只更新指定字段
- ref 支持：文件路径、`path::entity`、ent_id

### 10. add_edge ✅

**文件**: `tool_use/add_edge/tool.py`

```python
add_edge_command(store, edges) -> str
```

- 无向边：`{a: ref, b: ref, required_by: ["a"|"b"]}`
- 验证两端节点存在
- 自动去重
- `required_by`：指定哪个节点依赖此边（级联删除用）

### 11. delete ✅

**文件**: `tool_use/delete/tool.py`

```python
delete_command(store, ref) -> str
```

- 删除节点 meta + 所有连接边
- 按 `required_by` 级联：被删边的 `required_by` 中列出的节点也会被删除
- 递归级联直到无更多依赖
- 返回所有被删除的节点列表

### 12. sub_agent ✅

**文件**: `tool_use/sub_agent/tool.py`

```python
AgentExecutor(parent_registry)(store, arguments) -> str
```

- 创建独立 PontisAgent 实例执行任务
- 工具集 = 父工具集 - agent（防止递归）
- 模式：sub_agent（writer 能力 + 子智能体行为约束，无动态项目概览）
- 参数：task（必填）、max_rounds（默认 15）、description（日志标签）

---

## 边模型设计

### 无向边

边无方向，存储格式：

```yaml
edges:
  - nodes: [ent_a1b2, ent_c3d4]       # 两个端点
    required_by: [ent_c3d4]            # 依赖方（可选，列表）
    ent_a1b2: event.db                 # 可读性
    ent_c3d4: event.db::users.table    # 可读性
```

- `nodes`: 两个端点的 ent_id
- `required_by`: 依赖此边的节点列表。边被删除时，列表中的节点会被级联删除
- 额外的 ent_id → ref 映射提供可读性

### 级联删除规则

1. 删节点 → 删除该节点所有边
2. 删边 → 检查 `required_by`，其中列出的节点如果尚未删除 → 级联删除
3. 递归直到无更多级联

### 虚属性分组

`get_meta()` 自动按邻接节点的实体类型后缀生成分组：

```yaml
# meta event.db
table: [event.db::users.table, event.db::orders.table]
col: [event.db::users.id.INT.col, ...]
view: [event.db::user_order_join.view]
```

文件节点的后缀为 `file`。

---

## 实体类型

| 后缀 | 含义 | 来源 |
|------|------|------|
| `.table` | 表 | extractor/skeleton |
| `.col` | 列 | extractor/skeleton |
| `.view` | 关系视图（join/overlap） | agent |
| `.summary` | AI 摘要 | agent |
| `.fk` | 外键关系 | extractor/agent |
| `.rel` | 逻辑关系 | agent |

所有实体扁平挂在文件节点下，通过归属边连接。表列关系通过命名约定（`users.id.INT.col` 属于 `users` 表）。

---

## Prompt 分层架构

```
最终 prompt = 静态层(_base) + 模式层 + 动态层(project)
```

| 模式 | 静态层 | 模式层 | 动态层 |
|------|--------|--------|--------|
| readonly | _base | _readonly | _project |
| writer | _base | _writer | _project |
| sub_agent | _base | _writer + _sub_agent | 无 |

- `_base.py`: Pontis 概念、Ref 语法、实体类型、元数据字段、读取策略
- `_readonly.py`: 只读角色、实体命名规则、行为约束
- `_writer.py`: 写入角色、create_entity/update_meta/add_edge/delete 指令、写入原则
- `_sub_agent.py`: 子智能体行为约束（使用父上下文、专注执行）
- `_project.py`: 运行时项目路径 + 实体/边统计
