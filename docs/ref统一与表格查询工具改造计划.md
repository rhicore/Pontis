# Pontis ref 统一与表格查询工具改造计划

## 背景

KDD public benchmark 暴露出一个核心问题：agent 不是单纯“不听提示词”，而是当前工具访问模型不够统一、不够直觉。

现在工具参数混用了 `ref`、`path`、`file`：

- `find/meta` 偏图谱语义，使用 `ref`。
- `read/jd` 更像文件路径工具，使用 `path`。
- `query` 使用 `file`，并且实际只稳定支持 SQLite DB。
- `bash` 可以直接绕过 storage 访问 OS path。

这导致 agent 形成了错误心智：`context/csv/x.csv` 是文件系统路径，可以用 bash/python 直接打开。于是它大量使用 bash 做 CSV/JSON/Markdown 的读取、筛选、聚合和正则抽取。

目标不是简单限制 bash，而是让 Pontis 工具覆盖 agent 真实需要做的动作，并统一成：

> agent 访问的是图谱实体，不是文件系统路径。所有实体访问都通过 `ref`。

## 总目标

1. 所有实体访问工具统一使用 `ref`。
2. `ref` 是图谱访问语法，不是绝对路径，也不是相对路径。
3. 工具内部先 resolve 图谱实体，再通过 storage handle 访问真实资源。
4. `query` 扩展为统一 SQL 查询入口，能查询 DB、CSV/TSV、JSON records。
5. `read/grep/jd` 变成直觉、稳定的文件/文本/JSON 探查工具。
6. 合并旧实体发现能力 为统一实体发现工具 `find`。
7. 保留旧参数和旧工具名作为兼容层，但工具说明和 agent schema 应逐步只暴露新接口。

## ref 语法规范

### 基本形式

`ref` 是图谱实体定位语法，可长得像路径，但语义上不是 OS path。

示例：

```text
context/knowledge.md:file
context/doc/Laboratory.md:file:md:text
context/csv/trans.csv:file:csv:text
context/csv/trans.csv/account_id:col:INT
context/json/drivers.json:file:json:text
context/db/results.db:file:db
context/db/results.db/results:table
context/db/results.db/results/position:col:INT
```

带 project 前缀：

```text
task_250::context/knowledge.md:file
task_420::context/db/cards.db/cards/hasContentWarning:col:INT
```

### 设计原则

- `ref` 先在图谱中 resolve，到唯一实体后才访问资源。
- `context/csv/trans.csv` 只是 path-style ref 的显示形式，不代表工具直接 `open()` 该路径。
- 所有工具返回的可继续操作对象都应优先给出 copyable `ref`。
- 如果匹配多个实体，工具返回候选 refs，要求 agent 改用更完整 ref。
- 如果类型不匹配，工具返回期望 label，例如 `需要 file:csv 或 file:db`。

### 参数命名约定

- 图谱实体入口：`ref`
- JSON 内部位置：`pointer`
- SQL 文本：`sql`
- 行号：`start_line`, `end_line`, `limit`
- agent 工具入口只暴露 `ref`；`path`/`file` 不作为工具参数。
- JSON 内部位置直接写在 ref 中，例如 `jd(ref="file.json#/records")`。

## find：统一实体发现和语义匹配

### 命名

统一实体发现工具建议命名为 `find`。

原因：

- 旧的实体枚举命名容易强化文件系统 path 心智。
- 单独的语义检索工具容易让 agent 以为只能做语义搜索，不能列实体。
- agent 的真实需求是“找到图谱实体”，包括按 ref 模式列出、按关键词过滤、按语义检索。
- `find` 短、直觉、通用，和 ref 图谱访问模型更一致。

### 目标接口

```json
find({
  "ref": "*:file",
  "limit": 50
})
```

用于实体枚举。

```json
find({
  "ref": "*",
  "query": "track number",
  "limit": 20
})
```

用于全图语义匹配。

```json
find({
  "ref": "*:col",
  "query": "track number",
  "limit": 10
})
```

表示先用 `ref` 限定候选范围，再做关键词/语义排序。

`ref` 始终必填。需要全图搜索时使用 `ref:"*"`，不要省略 ref。

### 返回格式

统一返回：

```text
ref | labels | brief | source | why_matched
```

示例：

```text
context/csv/trans.csv:file:csv:text    57.0 MB | 交易流水表
context/csv/trans.csv/account_id:col:INT    账户 ID | matched query: account/transaction owner
```

要求：

- 每条结果都必须给 copyable `ref`。
- 如果带 query，给简短 `why_matched`。
- 结果里不要只返回 name，避免 agent 再猜路径。

### find 与其他工具的关系

新工具心智：

```text
find  找实体
meta  看实体元信息
read  读 file ref 原文
grep  在 file ref 原文中定位
jd    探查 JSON file ref
query 查询表格 file/workspace ref
```

这比现在的 `find + meta + read + query(ref)` 更稳定。

## 统一 resolver

需要抽一个公共 resolver，供所有工具复用：

```python
resolve_ref(workspace, ref, expected_labels=None) -> ResolvedEntity
```

返回内容应包括：

- `ref`: stable graph ref
- `labels`
- `meta`
- `project`
- `display_ref`
- `open_file` handle 或 source handle
- `db_connect` handle
- `source_path` 仅内部使用，不暴露给 agent

职责：

1. 支持 project 前缀：`task_250::...`
2. 支持 path-style file ref：`context/csv/a.csv`
3. 支持 structured ref：`context/db/a.db/table/col`
4. 支持 label filter：`:file:csv`, `:col`
5. 匹配多个实体时返回清晰候选。
6. 不允许工具绕过 workspace/storage 直接访问 OS path，除非 workspace 显式允许且工具是 bash。

## 工具改造计划

### 1. read

目标：稳定读取图谱 file 实体的原文行。

目标接口：

```json
read({
  "ref": "context/doc/Laboratory.md:file",
  "start_line": 428,
  "limit": 80
})
```

也支持 CSV/JSON 原文：

```json
read({
  "ref": "context/csv/trans.csv:file",
  "start_line": 1,
  "limit": 20
})
```

能力：

- 支持 `file:text`, `file:md`, `file:csv`, `file:tsv`, `file:json`。
- 返回行号。
- 支持 `start_line + limit` 或 `start_line + end_line`。
- 如果 ref 不是 file，提示可用 `meta(ref)` 或候选 file refs。
- 错误提示改为图谱语言：

```text
未找到唯一 file 实体 ref="README.md"。
请先使用 find({"ref":"*:file"}) 查看可读取的 file ref。
```

### 2. grep

目标：替代 bash grep/head/awk 中的原文检索部分。

目标接口：

```json
grep({
  "ref": "context/csv/trans.csv:file",
  "pattern": ",2779,",
  "literal": true,
  "ignore_case": false,
  "before": 2,
  "after": 2,
  "limit": 50
})
```

能力：

- 支持 text/md/csv/tsv/json 原文流式扫描。
- 支持 literal/regex。
- 支持 before/after 上下文。
- 返回 line number。
- 输出中提示下一步可用 `read(ref, start_line=...)`。

后续可选增强：

```json
grep({
  "ref": "context/csv/trans.csv:file",
  "where": {"account_id": "2779", "operation": "VYBER"},
  "select": ["trans_id", "date", "operation", "amount"],
  "limit": 20
})
```

但字段级过滤更推荐交给扩展后的 `query`。

### 3. jd

目标：从 JSON 结构浏览工具升级为 JSON record 探查工具。

目标接口：

```json
jd({
  "ref": "context/json/drivers.json:file",
  "pointer": "/records"
})
```

record 查询：

```json
jd({
  "ref": "context/json/drivers.json:file",
  "pointer": "/records",
  "where": {"driverRef": "yoong"},
  "select": ["driverId", "forename", "surname", "number"],
  "limit": 20
})
```

能力：

- `pointer` 使用 JSON Pointer。
- 自动识别 list-of-dict records。
- 支持 `where` 等值过滤。
- 支持 `select`。
- 支持 `count`, `distinct`, `topk`。
- 嵌套 dict/list 简洁展示为 JSON 摘要。

兼容：

- `path="context/json/a.json#/records"` 解析为 `ref="context/json/a.json:file", pointer="/records"`。

### 4. query

目标：统一表格数据 SQL 查询入口。

#### 阶段 1：DB ref 查询

把参数从 `file` 改为 `ref`：

```json
query({
  "ref": "context/db/results.db:file",
  "sql": "SELECT * FROM results LIMIT 5"
})
```

保持现有 SQLite 只读限制。

#### 阶段 2：CSV/TSV ref 查询

支持：

```json
query({
  "ref": "context/csv/trans.csv:file",
  "sql": "SELECT COUNT(*) AS count FROM this WHERE account_id = 2779"
})
```

设计：

- 单文件 CSV/TSV 查询时默认表名为 `this`。
- 同时可注册 basename 表名，例如 `trans`。
- 导入到只读临时 SQLite 或缓存 SQLite。
- header 作为列名，保留原始列名。
- CSV/TSV 缓存表列先统一按 TEXT 导入，数值计算时在 SQL 中显式 `CAST(...)`。
- SQL 中有空格列名时要求 quote：`"School Name"`。

缓存：

```text
${TMPDIR}/pontis_query_cache_<uid>/<hash>.sqlite
```

cache key：

```text
source ref + size + line_count + char_count
```

#### 阶段 3：JSON records 查询

支持 flat records：

```json
query({
  "ref": "context/json/drivers.json:file",
  "sql": "SELECT driverId, forename, surname FROM this WHERE driverRef = 'yoong'"
})
```

规则：

- 自动找顶层 `records` array；找不到则要求 agent 使用 `jd` 确认 pointer。
- 当前实现也支持顶层 list[dict]，以及 `records/data/items/rows/results` 中的 list[dict]。
- list-of-dict 转 SQLite table。
- 嵌套 dict/list 存为 JSON string。
- 默认表名 `this` 和 basename stem。

#### 阶段 4：task workspace 查询

支持：

```json
query({
  "ref": "task:*",
  "sql": "SELECT ... FROM trans JOIN account ..."
})
```

或：

```json
query({
  "ref": ".",
  "sql": "SELECT ... FROM frpm JOIN satscores ..."
})
```

设计：

- 当前 task/project 下所有 DB tables、CSV/TSV files、JSON records 注册到同一个 SQLite workspace。
- 表名需要避免冲突：
  - DB: `db__results__results`，同时无冲突时提供 `results`
  - CSV: `csv__trans`，同时无冲突时提供 `trans`
  - JSON: `json__drivers`，同时无冲突时提供 `drivers`
- `query` 出错时返回 available tables。

#### query 错误提示

当前错误如 `no such table: atom` 不够可恢复。应改成：

```text
SQL error: no such table: atom

Available tables in ref=".":
- csv__atom (source: context/csv/atom.csv, alias: atom)
- db__bond__bond (source: context/db/bond.db, alias: bond)

Try:
SELECT * FROM csv__atom LIMIT 5
```

### 5. find / meta

目标：让 agent 发现可查询表，而不是猜 SQL 表名。旧实体枚举职责并入 `find(ref=...)`。

CSV file meta 增加：

```text
query tables:
- this (when query ref is this file)
- trans
- csv__trans
```

JSON file meta 增加：

```text
query table:
- drivers (records: $.records)
```

可选：为 CSV/JSON records 创建虚拟 table 节点：

```text
context/csv/trans.csv/trans:table
context/json/drivers.json/drivers:table
```

这会让 `find({"ref":"*:table"})` 覆盖 DB/CSV/JSON 的可查询表。

迁移后对应：

```json
find({"ref": "*:table"})
```

### 6. search

目标：字段语义消歧更可靠。长期 `search` 的职责并入 `find(ref=..., query=...)`。

改进方向：

- 对 `*:col`、`*:table`、`*:chunk`、`*:pattern`、`*:knowledge` 提供更明确的推荐。
- no result 时给出下一步建议。
- 提高 `knowledge:lesson/pattern` 权重，特别是 reflection 写入的经验。
- 返回结果中必须包含 copyable `ref` 和 source。

示例：

```json
find({
  "ref": "*:col",
  "query": "track number"
})
```

### 7. final answer 结构检查

这不是限制工具，而是防止格式型 0 分。

可做为 guardrail 或 prompt checklist。

检查项：

- `Which X has Y` 是否只输出 X。
- `What is Y of X` 是否只输出 Y。
- `full name` 是否误把 `first_name` + `last_name` 拼成一列。
- 数值是否输出为 JSON number。
- 是否输出了中间计算列。
- 聚合粒度是否明确。

## agent 工具说明同步

工具 schema 应逐步改成：

```json
read:  { "ref": "...", "start_line": 1, "limit": 80 }
grep:  { "ref": "...", "pattern": "...", "literal": true }
jd:    { "ref": "...", "pointer": "/records" }
query: { "ref": "...", "sql": "SELECT ..." }
meta:  { "ref": "..." }
find:  { "ref": "...", "query": "..." }
```

兼容旧参数和旧工具名，但不在 prompt/examples 中展示。

同步修改位置不能只看工具实现，还必须包括：

- `agent/tools.py`：OpenAI tool schema、executor 注册、旧参数兼容。
- `agent/prompt/_tool.py`：agent 的总工具心智和推荐调用顺序。
- `agent/tool_use/<tool>/prompt.py`：单个工具的详细系统提示词。
- `agent/config.py`：各 mode 默认暴露的工具列表。
- guardrail 中引用工具名的地方，例如 `exploration_check`、`sql_utils`、`tool_abuse`。

每新增或重命名一个工具，都要同时检查这些位置，否则 agent 会看到新 schema，但系统提示仍按旧工具心智行动。

## 实施顺序

### P0：统一 ref 心智和基础工具

1. 写 `ref` 规范到 docs。
2. 抽公共 `resolve_ref`。
3. 新增 `find`，先包装现有实体枚举/检索能力。
4. 改 `read` 使用 `ref`，兼容 `path`。
5. 改 `grep` 使用 `ref`，支持原文行号和上下文。
6. 改工具 prompt/schema，隐藏 path/file 心智。

### P1：query 支持表格文本文件

1. `query(ref=db_file)` 保持现状。
2. `query(ref=csv_file)` 支持 `this` 表。
3. CSV/TSV 导入缓存。
4. `meta(csv_file)` 显示 query table 名。
5. query 错误时返回 available tables 和 source。

### P2：JSON 和工作区查询

1. `jd(ref, pointer, where, select)`。
2. `query(ref=json_file)` 支持 flat records。
3. `query(ref=".")` 支持 task workspace。
4. `find({"ref":"*:table"})` 展示 CSV/JSON 虚拟表。

### P3：语义检索和长文本增强

1. find 的 ref+query 排序增强。
2. chunk/read/grep 联动。
3. 叙事文档 fact extraction 的轻量工具或 grep 扩展。
4. final answer 结构检查 guardrail。

## 验证方案

### 单元测试

- `read(ref=csv_file)` 返回行号。
- `grep(ref=csv_file)` 支持 literal/regex/context。
- `jd(ref=json_file, pointer="/records", where=...)` 返回过滤结果。
- `query(ref=csv_file, sql="SELECT COUNT(*) FROM this")` 可运行。
- `query(ref=db_file)` 原行为不回退。
- `find(ref="*:file")` 与旧实体枚举结果一致。
- `find(ref="*:col", query="track number")` 能返回字段候选并带 why_matched。
- 多实体 ref 返回候选。
- 旧参数 `path/file` 兼容。

### KDD representative 回归

重点重跑这些题：

- `task_38`: CSV grep/query 替代 awk。
- `task_80`: CSV query 替代 47 次 bash。
- `task_86`: 字段消歧 + query。
- `task_163`: JSON/CSV/DB 混合源。
- `task_199`: CSV + DB join。
- `task_344`: CSV 医疗聚合。
- `task_396`: 长 Markdown grep/read。
- `task_418`: 长 Markdown + 阈值。

观察指标：

- bash 调用数下降。
- find 调用替代旧实体发现工具。
- query/read/grep/jd 调用数上升。
- SQL 错误可恢复。
- public proxy recall 是否提升。

## 关键设计取舍

1. 不新增复杂 `csv_query`，优先增强 `query`，让表格数据统一用 SQL。
2. 不禁止 bash，但让 bash 不再是最顺手的默认入口。
3. 所有工具以 `ref` 为入口，避免 agent 形成 OS path 心智。
4. CSV/JSON 查询先做单文件，再做 task workspace。
5. 旧实体发现工具并入 `find`，减少 agent 需要记忆的实体发现工具。
6. 旧接口兼容一段时间，但 prompt 只教新接口。
