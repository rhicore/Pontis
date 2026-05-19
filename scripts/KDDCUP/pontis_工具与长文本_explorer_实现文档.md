# Pontis KDD 工具与长文本 Explorer 状态文档

本文档记录 KDD 适配目前已经落地的内容，以及接下来还需要实现或验证的事项。已实现的大段设计不再展开，细节以代码为准。

## 当前结论

KDD 第一阶段需要的三类能力已经基本具备：

1. 文本检索与回读：`grep` / `read` 通过 storage 的 `open_file` 句柄访问 `:file:text`。
2. JSON 探查：`jd` 通过 JSON VFS 路径浏览 JSON 内部结构。
3. 长文本、JSON 结构、CSV 表格总结：通过 agent explorer 创建 `chunk`，总结 JSON/pattern 和 CSV/列。

还没完全收口的关键点：

1. `read(chunk_ref)` 还没实现，现在只能通过源文件路径和 chunk 行号回读。
2. 大 JSON 现在仍主要是 `json.load`，`jd` 多次调用会重复解析，hidden 大文件可能需要缓存或采样优化。
3. CSV 的 AI summary 已有逐文件 agent，但还没做批量 runner 的并发/限流策略。
4. KDD runner 已能串起静态、AI 和 agent 阶段，但还需要更多 public task 端到端验收。

## 已实现

### 工具目录与 workspace 安全

已去掉旧工具目录前缀：

```text
tool/FS_grep        -> tool/grep
tool/DB_query       -> tool/query
tool/SH_bash        -> tool/bash
agent/tool_use/...  同步改名
```

已新增 workspace 访问辅助：

```text
tool/utils/workspace_access.py
```

它负责：

- 判断 workspace 是否允许直接访问本地 fs。
- 通过 graph 解析文件节点的 `_file_open/file_open`。
- 给 `grep/read/jd/text_chunk/json_pattern_summary` 提供统一文件解析入口。

`bash` 现在会检查 workspace source，非本地 fs workspace 不允许绕过 storage 直接执行文件系统访问。

### TextModule

`storage/stores/text.py` 已负责给普通文本文件打 `:file:text`，并补充轻量 metadata：

```text
encoding
line_count
char_count
file_size
modified_at
```

设计边界保持清晰：

- `open_file` 仍来自 FS/file 层。
- `:text` 只表示这个文件可以被 `grep/read/长文本 explorer` 当普通文本读取。
- 不新增第二套 text handle。

### grep

实现位置：

```text
tool/grep/tool.py
agent/tool_use/grep/prompt.py
```

当前行为：

- 默认只搜索 `:file:text`。
- 优先使用 storage 返回的 `open_file` 句柄逐行读取。
- 只在安全的本地 fs workspace、且解析到单个本地文件时，用 ripgrep 优化。
- 支持 `content/count/files_with_matches`、`ignore_case`、`file_pattern`、`offset/head_limit`。

### read

实现位置：

```text
tool/read/tool.py
agent/tool_use/read/prompt.py
```

当前行为：

- 默认只读取 `:file:text`。
- 通过 `open_file` 按行号读取。
- 单次最多 500 行，输出有行号。

当前缺口：

- 还不能直接 `read(ref="<chunk ref>")`。
- 推荐下一步让 read 支持 `:chunk` sugar：解析 chunk 的相邻源文件边和 `start_line/end_line`，再回读源文本文件。

### CSV profile

实现位置：

```text
storage/stores/csv_schema.py
extractor/modules/csv_column_stats.py
extractor/modules/csv_column_sample.py
extractor/modules/csv_column_topk.py
explorer/csv_summary.py
extractor/engine.py -> agent_csv_summary
```

当前行为：

- `csv_schema` 只读 header + 前 100 行，用来创建列节点和粗略类型。
- `csv_column_stats` 已改成单文件单 pass，同时更新所有列。
- `csv_column_sample` / `csv_column_topk` 保留为兼容入口，实际委托给 `csv_column_stats`。
- profile 使用 CPC sketch 估算 cardinality，Space-Saving 估算 top-k。
- 不再按“每列重新扫一遍整个 CSV”执行。
- `csv_column_stats.generate(..., file="data.csv")` 支持单文件 profile，方便 explorer 先补齐目标 CSV 的统计。
- `agent_csv_summary` 已实现：先确保目标 CSV 有列 profile，再让 writer agent 基于 `meta/read/grep` 为 CSV 文件和列节点写 `brief/detail`。
- CSV/TSV 文件的 `meta` 默认显示已包含 `brief/detail`。

当前缺口：

- 对极宽 CSV，单 pass 会为每列维护 sketch/counter，仍要观察内存。
- `agent_csv_summary` 是逐文件 agent；如果 CSV 文件数量很多，需要 runner 加并发/限流策略。
- AI summary 不读取完整大 CSV，只能基于列 profile、header 和少量样例行总结，因此业务含义模糊的列要允许写“不确定”。

### jd

实现位置：

```text
tool/jd/tool.py
agent/tool_use/jd/prompt.py
```

当前行为：

- 路径格式：`file.json#/records/0/name`。
- 通过 JSON 文件节点的 `open_file` 读取。
- 只展示当前 JSON 路径的直接子项。
- 输出列固定为 `key/index | value type | value info`，不在每行重复显示子路径。
- value type 使用 `DICT/ARRAY/STR/INT/FLOAT/BOOL/NULL`。
- 支持 `limit/offset/max_value_chars`。
- list[dict] 会展示 array item keys 摘要。

当前缺口：

- 每次调用都会重新 `json.load`，大 JSON 多轮探查会慢。
- 暂无 streaming/path-level parser；超大 JSON 仍可能有内存和时间风险。

### json_pattern

实现位置：

```text
extractor/modules/json_pattern.py
```

当前行为：

- 离线读取 JSON 文件。
- 提取重复结构 pattern：`ARRAY`、`DICT`、map-like dict。
- 创建 `:pattern` 节点。
- pattern 节点名只使用 JSON path；来源 JSON 文件通过 `(file)-[:RELATED_TO]->(pattern)` 边表达。

pattern 名示例：

```text
context/json/posts.json/$.records.[n]:pattern
```

pattern meta：

```text
json_path
type
pattern
```

当前缺口：

- 旧图里如果已经有老命名 pattern，需要清理或重跑。
- 当前仍是全量 `json.load`，对特别大的 hidden JSON 可能需要采样/streaming 版本。

### agent_json_pattern_summary

实现位置：

```text
explorer/json_pattern_summary.py
extractor/engine.py -> agent_json_pattern_summary
```

当前行为：

- 每个 JSON 文件启动一个 writer agent。
- agent 必须用 `jd` 查看 JSON 顶层和关键子路径。
- agent 读取该 JSON 文件下的 pattern 节点。
- agent 给 JSON 文件本身写 AI 总结。
- agent 给每个 pattern 节点写 `brief/detail`，解释该 JSON path 的结构片段、字段含义和解题用途。

推荐运行：

```bash
uv run python -m extractor run json_pattern ./project
uv run python -m extractor run agent_json_pattern_summary ./project
```

或指定单文件：

```bash
uv run python -m explorer.json_pattern_summary ./project --file context/json/posts.json
```

当前缺口：

- 还没有在 public 任务上做端到端质量检查。
- pattern 很多时成本可能高，需要观察后决定是否加筛选/分页策略。

### agent_text_chunk

实现位置：

```text
explorer/text_chunk.py
extractor/engine.py -> agent_text_chunk
```

当前行为：

- 每个待处理文本文件启动一个 writer coordinator agent。
- coordinator 自己用 `read` 判断语义 chunk。
- 长文本可用子智能体读取行号范围并返回 chunk 建议。
- chunk label 只有 `chunk`，没有 `text_chunk`。
- chunk 名只使用四位局部编号，不带 `chunk-` 前缀，不拼文件名、目录名、hash 或完整路径：

```text
0001:chunk
0002:chunk
```

chunk meta：

```text
chunk_index
start_line
end_line
brief
detail
```

图关系：

```text
(source text file)-[:RELATED_TO]->(chunk)
```

chunk 之间不连边。来源通过 `(source text file)-[:RELATED_TO]->(chunk)` 表达；顺序通过同一源文件边下的 `chunk_index` 表达。

语义约束：

- chunk 是语义聚拢的文本片段实体，不要求完整覆盖全文。
- 无价值目录、页眉页脚、重复模板可以跳过。
- 每个 chunk 必须能通过源文件 + 行号回查。

当前缺口：

- 还没实现 `read(chunk_ref)`。
- 还没在真实 KDD 文档上验证 chunk 质量和成本。

### Neo4j 本地环境

当前 worktree 已处理两个问题：

- `storage/neo4j/graph.py` 会读取本地 `.neo4j/neo4j.env`，避免缺少 `NEO4J_PASSWORD` 时发出坏 auth token。
- `execute_cypher` 对 transient Bolt 断连做了重试。

本地工具测试曾验证：

```text
64 passed, 0 failed
```

### 已关闭 ai_text_summary

`ai_text_summary` 已从 extractor 默认 registry 和 BIRD AI pipeline 中移除。文件仍保留，避免外部直接 import 失败，但默认流程不再调用它。

## 剩余 TODO

### P0：必须先修

#### 1. 实现 `read(chunk_ref)`

目标：

```text
read(ref="0003:chunk")
```

内部流程：

1. 如果 `path` 解析到唯一的 `:chunk`，沿相邻边找到唯一源文本文件，并读取 chunk 的 `start_line/end_line`。
2. 再走现有 `read(source_file_path, start_line, end_line)`。
3. chunk 缺字段或 `0003:chunk` 命中多个来源文件时返回明确错误。

这样 chunk 不需要 storage handle；chunk 只是派生实体。

#### 2. 跑更多 public task 端到端验收

至少选一个有 JSON + doc 的 public 任务，跑：

```bash
json_pattern
agent_json_pattern_summary
agent_text_chunk
基础 DB/CSV extractor
csv_column_stats
agent_csv_summary
```

检查：

- `jd` 能正常浏览 JSON。
- pattern 节点能被 `find/meta` 找到。
- JSON 文件和 pattern 节点都有 AI 生成的 `brief/detail`。
- chunk 节点语义正常，不是机械垃圾切片。
- `read` 能回查 chunk 原文行号。
- CSV profile 字段能被 `meta/find/agent_analyze` 正常消费。

### P1：性能和稳定性

#### 4. 给 `jd` 加缓存或采样

当前 `jd` 每次调用都会重新 `json.load`。对 100MB 级 JSON，agent 多轮探索会重复消耗时间。

建议：

- 进程内 LRU cache，key 为 `(file_path, file_size, modified_at)`。
- 超大 JSON 只缓存 schema/sample，不缓存完整对象。
- 或为 `records` 类大数组增加轻量索引/采样读取。

#### 5. 给 `json_pattern` 加大 JSON 保护

当前 `json_pattern` 也是全量读 JSON。

建议：

- 对 `{"table": "...", "records": [...]}` 这种结构做抽样 schema。
- 超过阈值时只采样 head/tail/random records。
- 避免 hidden extreme JSON 导致内存或时间爆炸。

#### 6. pattern summary 批量策略

`agent_json_pattern_summary` 现在要求处理每个 pattern。

如果一个 JSON 生成太多 pattern，需要加策略：

- 先总结根、主要数组、主要 map。
- 小包装层可以写简短 summary。
- 大量低价值 pattern 可批量交给子智能体，或设置上限。

#### 7. CSV 专用 summary

```text
agent_csv_summary
```

实现位置：

```text
explorer/csv_summary.py
extractor/engine.py -> agent_csv_summary
```

输入信息：

- CSV 文件 meta
- 列清单
- 每列 sample/topk/cardinality/null_percentage
- 必要时少量 `grep` 定位原文

不要让 AI 直接读取几百 MB CSV 原文。

运行方式：

```bash
uv run python -m extractor run csv_column_stats,agent_csv_summary ./project
uv run python -m explorer.csv_summary ./project --file data/table.csv
```

### P2：runner 集成

#### 8. 写 KDD 预处理编排

需要有一个稳定顺序，而不是手动跑模块：

```text
1. 初始化 workspace / storage
2. json_pattern
3. agent_json_pattern_summary
4. agent_text_chunk
5. csv_column_stats
6. agent_csv_summary
7. DB extractor
8. semantic_embedding
```

实际顺序可能要按成本调整，例如 agent summary 放在结构 extractor 之后、embedding 之前。

#### 9. runner 成本开关

KDD 每题数据不同，建议加开关：

```text
--skip-agent-json-summary
--skip-text-chunk
--json-summary-only-large
--skip-csv-summary
--text-min-chars
--max-agent-files
```

避免 easy/medium 任务浪费 agent 成本。

## 推荐下一步

优先做这三个：

1. 实现 `read(chunk_ref)`，让 chunk summary 和原文回查闭环。
2. 选一个 public task 跑端到端，验证 JSON 文件 summary、pattern 结构索引、chunk summary 是否真的帮助解题。
3. 持续检查 extractor/explorer 不把关系信息重复写成 `source_path` 等冗余属性。

完成这三项后，再决定优先做 `jd/json_pattern` 的大 JSON 性能优化，还是先写 KDD runner 编排和 agent 成本开关。
