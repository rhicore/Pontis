# Pontis KDD 工具与长文本 Explorer 实现文档

本文档只描述实现方案，不直接改代码。目标是在当前 Pontis 架构基本定型的前提下，为 KDD Cup 任务补齐三个能力：

1. `grep` 工具：用于检索文本内容，必须通过文本文件的 handle 接口访问原文。
2. `jd` 工具：JSON 探查工具，按 JSON VFS 路径浏览 JSON 内部结构。
3. 长文本 explorer：读取一个文本文件，生成相邻 chunk 实体，每个 chunk 记录行号范围和 AI 总结，并鼓励使用子智能体并行处理。

## 背景判断

当前相关代码位置：

```text
tool/FS_grep/tool.py
agent/tool_use/FS_grep/prompt.py
extractor/modules/json_pattern.py
explorer/analyze.py
agent/tools.py
storage/stores/text.py
storage/stores/base.py
```

已有 `TextModule` 会给文本文件节点生成：

```text
text_handle = <pontis:project:text:text:relative/path>
```

该 pointer 解析后返回 `SrcHandle`，并提供：

```text
ports: ["open", "path"]
```

因此，文本检索工具不应该绕过 storage 直接猜物理路径，而应该优先通过 graph 中的 `text_handle` 获取 `SrcHandle`，再用 `open` port 读取文本。

## Public 数据集观察

对 `KDDCUP/public/input` 的 50 个任务做了抽样和统计，结论会影响工具优先级：

```text
difficulty:
  easy    15
  medium  23
  hard    11
  extreme  1

modality combinations:
  csv+json+knowledge       12
  csv+db+knowledge         10
  csv+db+json+knowledge     7
  db+json+knowledge         6
  csv+doc+knowledge         6
  json+knowledge            3
  db+doc+knowledge          2
  csv+db+doc+knowledge      1
  json+doc+knowledge        1
  db+json+doc+knowledge     1
  doc+knowledge             1
```

比较大的文件包括：

```text
task_330/context/csv/Match.csv              279 MB
task_257/context/db/postHistory.db          276 MB
task_250/context/csv/postHistory.csv        229 MB
task_249/250/257/context/json/posts.json    166 MB
task_420/context/db/cards.db                 62 MB
task_38/context/csv/trans.csv                60 MB
task_218/context/json/schools.json           26 MB
task_257/context/json/users.json             20 MB
task_418/context/doc/*.md total             371 KB
```

因此：

- `jd` 是高优先级：public 中 JSON 很多，而且不少 JSON 是 `{"table": "...", "records": [...]}` 形式的大表 dump。
- `grep` 也高优先级：需要在大 JSON、Markdown 文档和 knowledge 中快速定位关键词。
- 长文本 explorer 对 public 的收益主要在 hard/extreme 文档题；public 最大文档约数十万字符，但 hidden extreme 可能明显更长。
- 单纯“漂亮地展示 JSON 下一层”不够，`jd` 必须对大 list/table-shaped JSON 做分页、schema 摘要和截断，否则 agent 会在 `records` 这种大数组前卡住。

## 一、grep 工具实现

### 目标

`grep` 用于在项目文本文件中搜索正则或字符串，支持：

- 搜索单个文本文件
- 搜索目录下所有文本文件
- 搜索 graph 中已投影的 `text` 实体
- 返回匹配行和行号
- 支持分页、大小写选项、文件 glob 过滤

### 当前问题

当前 `tool/FS_grep/tool.py` 的核心路径是：

1. 通过 Cypher 找 `n.src`
2. 从 `src.get("path")` 拿物理路径
3. 调 ripgrep 搜物理文件

但 `TextModule` 当前暴露的是 `text_handle`，不是统一 `src` 字段。因此对于文本文件实体，grep 应优先查：

```cypher
MATCH (n:text {path: $path}) RETURN n.text_handle AS handle
```

或者按 name/ref 解析到唯一文本节点后返回 `text_handle`。

### 推荐实现位置

修改现有文件：

```text
tool/FS_grep/tool.py
agent/tool_use/FS_grep/prompt.py
```

不新增第二个 grep 工具，避免 agent 工具面变复杂。

### 解析策略

新增一个内部 resolver：

```text
_resolve_text_sources(workspace, path, current_cwd) -> list[TextSource]
```

`TextSource` 建议包含：

```text
display_path: str       # 相对项目路径，用于输出
handle: SrcHandle       # text handle
line_count: int | None
char_count: int | None
```

解析顺序：

1. 如果 `path` 是空或 `.`：
   - 查询所有 `:text` 节点
   - 返回每个节点的 `text_handle`

2. 如果 `path` 指向具体文件：
   - 优先 `MATCH (n:text {path: $path}) RETURN n.text_handle`
   - 找不到时按 basename/name 精确匹配
   - 必须唯一，否则返回歧义提示

3. 如果 `path` 指向目录：
   - 查询 `path STARTS WITH directory_prefix` 的 `:text` 节点
   - 或先用目录 handle 获取物理目录，再过滤已投影文本文件

4. 如果 `glob` 存在：
   - 在 graph 层或 Python 层按 `fnmatch` 过滤 `display_path`

### 读取策略

对于每个 `TextSource`：

```text
handle.get("open")("r", encoding="utf-8", errors="ignore")
```

逐行流式读取，不一次性读完整文件。

如果 `SrcHandle` 没有 `open` port，但有 `path` port，才 fallback 到物理 path + ripgrep。

这样可以保留 ripgrep 对普通文件的性能，同时让文本 source 成为标准路径。

### 匹配策略

第一版支持 Python `re` 即可：

- `ignore_case` -> `re.IGNORECASE`
- `output_mode=content` -> `file:line:content`
- `output_mode=count` -> `file:count`
- `output_mode=files_with_matches` -> 文件列表

分页语义沿用现有参数：

```text
head_limit
offset
```

分页应作用在最终结果列表上。

### 输出格式

`content`：

```text
path/to/file.md:123:matched line text
path/to/file.md:128:another matched line

[Showing results with pagination = limit: 250, offset: 0]
```

`count`：

```text
path/to/file.md:12
other.md:3

Found 15 total occurrences across 2 files.
```

`files_with_matches`：

```text
Found 2 files
path/to/file.md
other.md
```

### 安全约束

- 不允许 path escape；所有 path 必须通过 graph source 或 `ctx.source` 解析。
- 不直接拼接用户路径访问任意绝对路径。
- 单文件读取要设置最大扫描行数或最大字节数保护，默认可先给 20MB 上限。
- 对超长文件建议提示使用长文本 explorer，而不是在 grep 中返回大量内容。

### Prompt 更新

`agent/tool_use/FS_grep/prompt.py` 应补充：

- grep 可以搜索 `:text` 文件实体
- `path` 是项目相对路径或文本文件 ref
- 大文件只返回匹配行，不要用 grep 读取全文
- 如果需要理解长文档结构，应调用长文本 explorer 产出的 chunk 实体或 search/meta

## 二、jd 工具实现

这里的 `jd` 指 JSON discovery / JSON directory 工具。它不是 join detect，而是 JSON 内部 VFS 的 `ls`：agent 给一个 JSON 文件路径或 JSON 内部路径，工具展示该节点下一层内容。

### 目标

`jd` 的参数设计和访问路径沿用既有 JSON VFS 设计，只优化展示格式。

旧设计要点已经合并到这里：

- JSON/YAML/sample/topK 等序列化文件都应有 source/handle，指向系统中存储该文件的位置。
- 对 sample 这类需要缓存的内容，可以在 `.sample` 或等价 cache 目录下生成二进制缓存再读取。
- 当 `ls` 进入 JSON 等序列化文件时，走 JSON 内部浏览逻辑，不再只是展示普通文件。
- 对容器值展示内部元素数量，如 `5 pairs`、`120 items`。
- 对标量值展示真实值，长字符串强制截断。
- 对 null 直接展示 `null`。
- key 中的空格、斜杠等特殊字符通过 URL Encoding 表示。

典型调用：

```text
jd(path="data.json")                  # 查看根节点
jd(path="data.json#/users")           # 查看 users 下一级
jd(path="data.json#/users/0")         # 查看第 0 个 user
jd(path="data.json#/metadata/title")  # 查看具体值
```

它不负责创建实体，不负责 join 关系推断，也不负责总结。它只回答“这个 JSON 路径下面有什么”。

推荐入口：

```text
tool/jd/tool.py
agent/tool_use/jd/prompt.py
```

并在 `agent/tools.py` 注册为 `jd`。

### 参数设计

第一版参数保持简单：

```json
{
  "path": "data.json#/users",
  "limit": 50,
  "offset": 0,
  "max_value_chars": 80
}
```

说明：

- `path`：JSON VFS 路径，必填。
- `limit`：最多展示多少个子项，默认 50。
- `offset`：分页起点，默认 0。
- `max_value_chars`：标量值或长字符串展示截断长度，默认 80。

不要额外引入 `action=ls/get/schema` 这一层，除非后续确实需要。第一版 `jd` 就是 JSON 内部的 `ls`。

### 访问路径协议

路径格式：

```text
<json_file_path>#<json_pointer_like_path>
```

示例：

```text
product_catalog.json
product_catalog.json#
product_catalog.json#/items
product_catalog.json#/items/0
product_catalog.json#/items/0/name
product_catalog.json#/metadata/source
```

规则：

- `#` 前是项目相对 JSON 文件路径。
- `#` 后是 JSON 内部路径。
- 根路径可以写成 `file.json`、`file.json#` 或 `file.json#/`。
- dict key 使用 URL Encoding，避免空格、斜杠等字符影响路径解析。
- list index 使用十进制数字。

特殊 key 示例：

```text
data.json#/weird%20key
data.json#/a%2Fb
```

内部解析：

```text
file_path = path before "#"
json_path = path after "#"
segments = URL-decode(json_path split by "/")
```

### 访问 JSON 文件

`jd` 和 `grep` 一样，必须走 Cypher 返回的 handle，不直接拼物理路径。

推荐查询：

```cypher
MATCH (n:file {path: $file_path})
RETURN coalesce(n.json_handle, n.file_handle) AS handle
```

如果后续实现 `JsonModule`，应提供：

```text
json_handle = <pontis:project:json:json:relative/path>
```

第一版没有 `json_handle` 也可以先用 `file_handle`，只要 handle 提供：

```text
handle.has("open")
handle.get("open")
```

读取后用标准 JSON parser 解析：

```text
open_fn = handle.get("open")
json.load(open_fn("r", encoding="utf-8", errors="ignore"))
```

### 展示格式

按原 JSON VFS 逻辑，优化成表格：

```text
HasSub | Key               | Type  | Info
------+-------------------+-------+----------------
[+]   | metadata          | DICT  | 5 pairs
[+]   | users             | LIST  | 120 items
[ ]   | version           | STR   | "1.2.4-stable"
[ ]   | is_active         | BOOL  | true
[ ]   | timeout           | INT   | 3000
[ ]   | legacy_config     | NULL  | null
```

列含义：

- `HasSub`：是否可以继续向下探查。
  - `[+]` 表示 DICT/LIST，有子节点。
  - `[ ]` 表示标量或 null。
- `Key`：当前节点下的 key 或 list index。展示 decoded 后的人类可读 key。
- `Type`：`DICT | LIST | STR | INT | FLOAT | BOOL | NULL`。
- `Info`：
  - DICT：`N pairs`
  - LIST：`N items`
  - STR：截断后的字符串值
  - 数值/布尔/null：真实值

如果当前路径指向标量，输出单行：

```text
Value | Type | Info
------+------|----------------
.     | STR  | "1.2.4-stable"
```

分页提示：

```text
[Showing children 50-99 of 120; use offset=100 for next page]
```

### 大 JSON 优化

public 数据集中有多个 166 MB 的 `posts.json`，且格式类似：

```json
{
  "table": "posts",
  "records": [
    {"Id": 1, "PostTypeId": 1, "...": "..."}
  ]
}
```

所以 `jd` 对 table-shaped JSON 要做特殊展示。

当当前节点是 dict，且包含 `table` 和 `records`：

```text
HasSub | Key      | Type | Info
------+----------+------+-------------------------------
[ ]   | table    | STR  | "posts"
[+]   | records  | LIST | 91976 items; item keys: Id, PostTypeId, ...
```

当当前路径是 `file.json#/records` 且 records 是 list of dict：

```text
HasSub | Key | Type | Info
------+-----+------+---------------------------------------------
[+]   | 0   | DICT | Id=1, Title="...", OwnerUserId=8, ...
[+]   | 1   | DICT | Id=2, Title="...", OwnerUserId=24, ...
```

注意：

- 不要递归展开每条 record 的所有字段。
- `Info` 只展示前几个关键字段，按 `max_value_chars` 截断。
- 对 list of dict，额外在表格前或后提示 item schema / key list。
- 对大文件可以用进程内 LRU cache，key 为 `(path, mtime, size)`，避免 agent 多次调用 `jd` 时反复解析同一个 166 MB JSON。
- 如果担心内存，设置 `max_cached_json_bytes`，超大 JSON 只缓存 schema/sample，不缓存完整对象。

第一版可以使用 Python 标准 `json.load`。在 KDD public 规模下，166 MB JSON 在 64 GB 内存限制中可接受，但必须避免每次工具调用都重复全量解析。

### 与 grep 的配合

`jd` 适合结构浏览，不适合全文搜索。对于大 JSON 中按关键词找记录，应先用 `grep`：

```text
grep(pattern="Computer Game Datasets", path="json/posts.json", output_mode="content")
```

然后根据命中的字段名、附近内容或后续 JSON 表格能力继续定位。后续如果这类需求频繁，应新增 `json_find` 或 `json_query`，但不要把第一版 `jd` 做成万能查询器。

### 与 json_pattern.py 的关系

`jd` 是在线探查工具。

`extractor/modules/json_pattern.py` 是离线结构归纳模块。

```text
jd:
  agent 按需浏览 JSON 内部路径，类似 ls。

json_pattern.py:
  自动提取重复结构、schema pattern，并写入 graph/meta。
```

两者可以共享 JSON path 解析和类型判定工具函数，但不要合并成一个工具。

建议抽一个小的内部工具模块：

```text
tool/jd/json_vfs.py
  split_json_vfs_path(path)
  encode_key(key)
  decode_key(segment)
  json_type(value)
  summarize_json_value(value, max_value_chars)
  iter_children(value, limit, offset)
```

`tool/jd/tool.py` 和 `extractor/modules/json_pattern.py` 可按需复用其中的纯函数。

### Prompt 更新

`agent/tool_use/jd/prompt.py` 应说明：

- `jd` 用于探查 JSON 文件内部结构。
- `path` 使用 `file.json#/a/b/0` 形式。
- key 中有特殊字符时使用 URL Encoding。
- `jd` 只展示当前路径的直接子项，不递归展开整个 JSON。
- 如果返回 `[+]`，说明可以继续把该 key 拼到 path 后继续探查。
- 大 JSON 必须分页，不要一次展开所有子项。

### 注册点

需要改：

```text
agent/tools.py
agent/config.py
agent/tool_use/jd/prompt.py
tool/jd/tool.py
```

建议 mode：

- `readonly` 默认包含 `jd`
- `benchmark` 默认包含 `jd`
- `writer` 默认包含 `jd`

因为 `jd` 是只读工具，可以安全进入 readonly。

## 三、长文本 Explorer

### 目标

新增一个 explorer，针对单个超长文本文件生成 chunk 实体。

每个 chunk 实体必须包含：

- chunk 序号
- 对应源文件 path
- 起始行号
- 结束行号
- 字符数或 token 估计
- AI 总结 brief/detail
- 与源文本文件的边
- 与前后相邻 chunk 的边

这个 explorer 主要服务 KDD 的 hard/extreme 文档任务：避免 agent 每次直接读超长 doc，而是先通过 chunk summary 定位相关段落。

### 推荐文件

```text
explorer/text_chunk.py
```

注册到 extractor：

```text
extractor/engine.py
  _REGISTRY["agent_text_chunk"] = text_chunk.generate
```

可独立运行：

```bash
python -m explorer.text_chunk ./my_project --file context/doc/report.md
```

如果沿用 extractor CLI，也可以：

```bash
python -m extractor run agent_text_chunk ./my_project
```

但 extractor 当前模块签名只接收 `workspace`，所以如果需要指定单文件，建议先做独立 CLI，后续再加 config 参数。

### Chunk 实体模型

实体 ref 建议：

```text
<source_path>#chunk-0001:chunk:text_chunk
```

示例：

```text
doc/races.md#chunk-0001:chunk:text_chunk
```

注意：`create_entity` 用 `:` 解析标签，所以实体名中不要包含额外冒号。

meta 字段建议：

```json
{
  "brief": "第1段至第80行，概述赛事背景和年份索引。",
  "detail": "该 chunk 覆盖源文件 doc/races.md 的第 1-80 行，主要介绍……",
  "source_path": "doc/races.md",
  "chunk_index": 1,
  "start_line": 1,
  "end_line": 80,
  "char_count": 12345,
  "line_count": 80,
  "summary_status": "ai_generated",
  "summary_model": "<model name>",
  "summary_updated_at": "<iso datetime>"
}
```

边：

```text
source text file <-> chunk-0001
chunk-0001 <-> chunk-0002
chunk-0002 <-> chunk-0003
```

如果之后需要更清晰的边类型，目前工具只有 `RELATED_TO`，先用无类型边即可；在 detail 中写明“相邻 chunk”。

### 行号与切分策略

第一版按行切分，不做复杂语义切分。

默认参数：

```text
max_lines_per_chunk = 120
overlap_lines = 10
max_chars_per_chunk = 16000
```

切分规则：

1. 按行读取全文。
2. 每个 chunk 最多 120 行。
3. 如果 120 行超过 `max_chars_per_chunk`，按字符上限提前截断。
4. chunk 间保留 10 行 overlap，方便上下文连续。
5. 记录真实 `start_line` / `end_line`。

对于 Markdown，可在后续版本优化：

- 优先按标题边界切分
- 表格不拆断
- 代码块不拆断

第一版先保证行号准确和稳定。

### 访问文本文件的方式

必须通过 `text_handle`：

```cypher
MATCH (n:text {path: $path}) RETURN n, n.text_handle AS handle
```

解析 `handle` 后使用：

```text
handle.get("open")
```

读取文本。

如果没有 `text_handle`，说明 TextModule 没投影该文件，应先通过 `glob`/Cypher 触发 text module，或者返回明确错误。

### Explorer 运行流程

推荐 coordinator 流程：

1. 解析目标文本文件
   - 如果指定 `file`，只处理该文件
   - 如果未指定，扫描所有 `:text` 文件，跳过短文本

2. 读取文件元信息
   - path
   - line_count
   - char_count
   - existing chunk entities

3. 判断是否需要重建
   - 如果文件 `modified_at` 或 `char_count` 变化，旧 chunk 标记 stale 或重建
   - 第一版可以简单跳过已存在 chunk，除非 `force=true`

4. 按行切 chunk

5. 为每个 chunk 生成 AI summary

6. 创建 chunk 实体

7. 添加边
   - 文件到每个 chunk
   - 相邻 chunk 到相邻 chunk

8. 更新源文件 detail
   - 标注“已生成 N 个 chunk，行号范围……”

### 子智能体策略

这个 explorer 的 prompt 应明确鼓励使用子智能体。

Coordinator 不应自己总结所有 chunk。它负责：

- 切分计划
- 分配 chunk 批次
- 校验输出格式
- 创建实体和边

子智能体负责：

- 阅读指定 chunk 文本
- 给出 `brief` 和 `detail`
- 标注关键实体、时间、指标、表名、术语
- 不创建实体，除非 coordinator 明确授权

建议每个子智能体处理 3-5 个 chunk，避免上下文太大。

子智能体任务模板：

```text
你负责总结源文件 {source_path} 的以下 chunk。

要求：
- 每个 chunk 输出 brief 和 detail
- brief ≤ 50 字
- detail 必须包含该 chunk 的主题、关键对象、关键指标/时间/名称，以及和前后文可能相关的线索
- 不要虚构，无法判断就写不确定
- 输出 JSON，按 chunk_index 分组

Chunk:
{chunk_text_with_line_numbers}
```

### Chunk 文本传递格式

传给子智能体时应带行号：

```text
[L120-L155]
120 | ...
121 | ...
...
155 | ...
```

这样 AI summary 能准确引用行号范围。

### Prompt 要点

`explorer/text_chunk.py` 中建议放两个 prompt：

```text
COORDINATOR_PROMPT
CHUNK_SUMMARY_PROMPT
```

Coordinator prompt 应强调：

- 优先使用 `grep` 定位目标文件
- 通过 `text_handle` 读取文本
- 长文本必须分块，不要整篇塞进一次 LLM
- 多用子智能体处理 chunk summary
- 写实体前先检查是否已有 chunk
- chunk 实体必须记录 start_line/end_line
- 相邻 chunk 必须加边
- summary 必须基于 chunk 原文，不要凭空补全

### 与 search 的关系

长文本 explorer 产出的 chunk 实体可以直接服务现有 `search`：

- chunk brief/detail 进入 graph 后，agent 可以先 search chunk summary
- 找到相关 chunk 后，再用 grep 或未来的 read_text_range 工具读取对应行号

建议后续补一个 `read_text_range` 工具，但第一版不是必须。

## 四、建议新增 read_text_range 工具

虽然用户本轮没有明确要求，但长文本 chunk 生成后，agent 需要按行号回读原文。建议作为后续小工具：

```text
tool/read_text_range/tool.py
agent/tool_use/read_text_range/prompt.py
```

输入：

```json
{
  "path": "doc/races.md",
  "start_line": 120,
  "end_line": 180
}
```

输出：

```text
doc/races.md:L120-L180
120 | ...
121 | ...
```

它同样必须通过 `text_handle` 的 `open` port 读取。

如果不想新增工具，`grep` 可以临时承担定位作用，但 grep 不适合无关键词的精确行号读取。

## 五、推荐实现顺序

### Step 1: 改 grep handle 访问

优先级最高。KDD 文档任务需要稳定检索文本。

验收：

```text
grep(pattern="Singapore", path="doc/races.md", output_mode="content")
```

必须能通过 `text_handle.open` 返回：

```text
doc/races.md:123:...
```

### Step 2: 做 read_text_range

这能让 chunk summary 和原文回查闭环。

验收：

```text
read_text_range(path="doc/races.md", start_line=100, end_line=140)
```

返回带行号原文。

### Step 3: 做长文本 explorer

先支持指定单文件：

```bash
python -m explorer.text_chunk ./project --file doc/races.md
```

验收：

- 创建 `doc/races.md#chunk-0001:chunk:text_chunk`
- meta 有 `start_line/end_line/brief/detail`
- chunk 与源文件有边
- 相邻 chunk 有边

### Step 4: 做 jd 工具

实现 JSON 内部 VFS 探查工具，参数和访问路径沿用 `file.json#/path/to/node` 风格。

验收：

```text
jd(path="constructors.json")
jd(path="constructors.json#/0")
jd(path="constructors.json#/0/name")
```

能返回当前 JSON 节点的直接子项表格，包含 `HasSub | Key | Type | Info`。

## 六、对 KDD runner 的影响

KDD 适配时，推荐每题预处理流程：

1. 初始化 workspace。
2. 对 JSON 文件运行轻量结构探查：
   - 小 JSON：可跑 `json_pattern`
   - 大型 `{"table","records"}` JSON：生成 schema/sample/top-level summary，避免全量 materialize 每条 record
3. 对 `context/doc/*.md` 或超长 `knowledge.md` 运行 `agent_text_chunk`。
4. 对数据库运行基础 extractor：
   - schema
   - stats
   - fk
   - overlap
5. 解题时让 agent 按需调用 `jd` 探查 JSON 内部结构。
6. 解题时优先 search/meta chunk summary，再 read_text_range 回查原文。

这样能避免两类问题：

- hidden hard/extreme 任务中把整篇文档塞进 prompt。
- public/hidden medium 任务中把上百 MB JSON 直接展开给模型。

## 七、风险与约束

| 风险 | 处理 |
| --- | --- |
| grep 绕过 handle 访问物理路径 | 优先 `text_handle.open`，只在无 handle 时 fallback |
| chunk 太碎导致实体太多 | 默认 120 行一块，短文不 chunk |
| AI summary 幻觉 | summary prompt 强制基于原文，detail 记录行号范围 |
| 子智能体写入冲突 | 子智能体只返回 summary，coordinator 统一写实体 |
| jd 展开过大 JSON | 默认只展示直接子项，使用 limit/offset 分页 |
| KDD 时间开销过大 | 只对超长文本运行 chunk explorer，短文本直接 grep/read |

## 八、最终形态

工具层：

```text
grep              # 文本检索，走 text_handle
read_text_range   # 按行号读取文本，走 text_handle
jd                # JSON 内部 VFS 探查，走 json/file handle
```

Explorer 层：

```text
agent_text_chunk  # 超长文本切 chunk、总结、建相邻实体
json_pattern      # JSON 重复结构和 schema pattern 离线提取
```

数据图中会新增：

```text
(text file)-[:RELATED_TO]-(chunk)
(chunk)-[:RELATED_TO]-(next chunk)
```

chunk meta 中保留：

```text
source_path, chunk_index, start_line, end_line, brief, detail
```

这套适配能让 Pontis 在 KDD 的长文档和多源推理任务里先通过 graph/chunk summary 缩小范围，再回到原文和 SQL 做精确计算。
