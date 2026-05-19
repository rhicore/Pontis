# Prompt Simplification Plan

本文档记录 Pontis 当前提示词体系的精简和清晰化改造计划。目标不是重写能力，而是让 LLM 更稳定地理解任务边界、工具边界和写入协议。

## 当前判断

当前提示词整体可用，但不够简洁稳定。主要问题是层级多、重复多、负面约束多，导致 LLM 偶尔抓错重点，例如把实体发现工具当作 JSON/CSV 行级查询工具、用裸列名、在文档 summary 里汇报 chunk 工程状态。

改造方向：

1. 每类 agent 只保留一条清晰主线：目标、工作流、写入协议、工具路由、停止条件。
2. 把“不要做什么”压缩到少量高风险规则，其他用正向协议替代。
3. 工具提示词必须明确边界，尤其 `find`、`create_entity`、`update_meta`。
4. 示例必须和当前 ref 规范一致，禁止旧的裸 ref、点式 ref、hash/pattern/chunk 旧命名继续出现在示例里。

## 统一提示词结构

后续 explorer / benchmark prompt 统一采用以下结构：

```text
# Role
你是 <具体任务 agent>。

# Goal
一句话说明最终产物。

# Required Workflow
1. ...
2. ...
3. ...

# Tool Routing
- 结构清单: find/meta
- JSON 结构: jd
- 文本定位: grep/read
- DB 精确计算: query
- 原始大文件精确计算: bash
- find: ref 必填；加 query 时查图谱实体/summary，不查原始数据行

# Write Contract
只能写哪些实体/字段，字段格式是什么。

# Stop Condition
完成哪些检查后停止。
```

## P0 修改项

### 1. `agent/tool_use/find/prompt.py`

问题：

- 当前描述是“语义检索工具”，容易被 LLM 理解成自然语言数据库查询。
- 在 JSON summary 中出现过 `find(ref=json, query="records where county is null")` 这种误用。

计划：

- 明确 `find(ref=..., query=...)` 只检索图谱实体、`brief/detail`、pattern、chunk、知识节点。
- 明确不扫描 CSV/JSON/DB 原始行。
- 给出工具路由：
  - 行级 JSON 探查用 `jd`
  - DB 行级过滤用 `query`
  - CSV 大文件精确聚合用只读 `bash`
  - 文本内容定位用 `grep/read`

验收：

- KDD extract/test 日志里不再出现用 `find` 查 JSON/CSV records 条件。

### 2. `agent/tool_use/create_entity/prompt.py`

问题：

- 示例仍包含 `table.column`、`results.points` 等点式/裸引用。
- 这和当前 path-style ref 规范冲突。

计划：

- 删除旧式点号 ref 示例。
- 所有列、表、文件示例都使用 path-style ref。
- 强调关系通过 `edges` 表达，不把 `source_path`、`file_path`、`path` 等冗余来源字段写入派生实体。
- 增加派生实体规范：
  - chunk: `0001:chunk`
  - pattern: 由 JSON 文件边表达来源，不带 `.pattern` 后缀，不带 hash

验收：

- 新日志中不再出现 `chunk-0001`、hash ref、`.pattern` 后缀、派生实体冗余 `source_path/file_path`。

### 3. `explorer/text_chunk.py`

问题：

- 当前 prompt 偏长，很多解释性内容稀释了核心任务。
- LLM 偶尔把源文件 summary 写成“生成了几个 chunk”的工程报告。

计划：

- 压缩为 5 段：
  - Goal
  - Workflow
  - Chunk Contract
  - Source File Summary Contract
  - Stop Condition
- 明确源文件 `brief/detail` 只总结文档内容本身：
  - 主题
  - 章节结构
  - 字段/概念定义
  - 规则约束
  - 歧义说明
  - 解题使用注意
- 删除“不要写 chunk 数量”这类负面句，改成正向定义“源文件 summary 的读者要理解文档内容”。

验收：

- `context/knowledge.md` 的 `brief/detail` 不再描述 chunk 个数、chunk 覆盖范围、如何 find/meta chunk。
- chunk ref 均为 `0001:chunk` 这种局部四位编号。

### 4. `explorer/json_pattern_summary.py`

问题：

- prompt 仍较长。
- JSON 文件 summary、pattern summary、工具规则混在一起。
- 虽已移除旧语义检索工具，但还需要进一步简化。

计划：

- 保留核心流程：
  1. `jd(__JSON_PATH__)`
  2. `find({"ref":"__JSON_PATH__/*:pattern"})`
  3. `meta(pattern)`
  4. `update_meta(JSON file)`
  5. `update_meta(patterns)`
- 明确 summary 来源只来自 `jd` 和 pattern 原始结构。
- 明确不写精确行数、数组长度等易过期信息。
- 保留工具参数合法 JSON 要求，但放进统一 “Write Contract”。

验收：

- JSON pattern summary 不再调用 `find`。
- pattern 由 AI 写 `brief/detail`，静态 `json_pattern.py` 不写 `brief/detail`。
- 日志中 `update_meta({})` 显著减少；解析失败由 parser 容错兜底。

### 5. `explorer/csv_summary.py`

问题：

- `find` 工具仍暴露给 CSV summary agent。
- 旧日志中出现过裸列名导致多实体匹配。

计划：

- 移除旧语义检索工具。
- 工作流固定为：
  1. `meta(__CSV_PATH__)`
  2. `find({"ref":"__CSV_PATH__/*:col"})`
  3. 对 find 返回的每个 path-style col ref 调 `meta`
  4. 写 CSV 文件 summary
  5. 写每个列 summary
- 强制列 ref 直接复制 `find` 结果，不自己拼裸列名。

验收：

- CSV summary 日志中列 ref 形如 `context/csv/file.csv/column:col:TYPE`。
- 不再出现 `匹配到多个实体: account_id:col:INT` 这类错误。

## P1 修改项

### 6. `scripts/KDDCUP/test_public.py`

问题：

- KDD test prompt 清楚但偏长。
- “探索纪律”容易让 agent 机械执行所有工具，而不是按题目选择最短路径。

计划：

- 压缩为：
  - Output Contract
  - Evidence Rule
  - Tool Routing
  - Workflow
  - Hard Guards
- `find(ref=..., query=...)` 用于查摘要和概念，不和 `query/jd/meta` 并列为主路径。
- 明确精确答案优先 `query` 或只读 Python 计算。

验收：

- 测试阶段工具调用更短。
- 对 DB 题优先 `find/meta/query`，少做无效语义检索。
- 输出仍保持唯一 JSON 代码块。

### 7. `agent/tool_use/find/prompt.py`

问题：

- 当前是完整 URN 技术文档，较长。
- 对多数 agent 来说，先掌握常用形式比完整语法更重要。

计划：

- 前置 “Common Patterns”：
  - `*:file`
  - `*:file:db`
  - `context/csv/data.csv/*:col`
  - `context/json/data.json/*:pattern`
  - `context/knowledge.md/*:chunk`
  - `project::*:table`
- 完整语法放后面，减少首次阅读负担。

验收：

- agent 更倾向使用 path-style ref，而不是裸 `*:col` 后再歧义解析。

### 8. `agent/tool_use/update_meta/prompt.py`

问题：

- 没有明确强调合法 JSON 参数和引用复制原则。

计划：

- 增加：
  - `ref` 优先复制 `find/meta` 返回的完整展示 ref。
  - `fields` 只写 `brief/detail`。
  - 文本里避免裸英文双引号；字段值示例用中文引号、单引号或反引号。

验收：

- `update_meta({})` 解析失败减少。

## P2 修改项

### 9. DB explorer prompts

涉及：

- `explorer/analyze.py`
- `explorer/join_detect.py`
- `explorer/disambiguate.py`
- `explorer/readme.py`

问题：

- 内容较长，历史约束较多。
- 部分提示词仍可能诱导点式 ref。

计划：

- 统一 path-style ref 规范。
- 删除与当前 KDD 数据无关的旧 BIRD 专属措辞。
- 将“禁止全局扫项目”改为“按数据库 -> 表 -> 列的定向流程”。

验收：

- DB agent 阶段不再生成旧格式 ref。
- README 节点不重复创建，优先更新已有文件节点或统一 README 实体策略。

## 实施顺序

建议按以下顺序落地：

1. `find` prompt 和 `create_entity` prompt。
2. `text_chunk`、`json_pattern_summary`、`csv_summary` 三个 KDD extraction 关键 explorer。
3. `find`、`update_meta` 工具提示词。
4. KDD test prompt。
5. DB explorer prompts。

每一步只改 prompt，不改业务逻辑；改完跑：

```bash
uv run python -m py_compile <changed files>
uv run python scripts/tool/test_tools.py
```

然后选 2-3 个复杂 public task 做 extract smoke：

```bash
uv run python scripts/KDDCUP/extract_public.py --task task_250 --task-workers 1
```

检查日志：

- 无旧 chunk/pattern 命名。
- 无裸列名歧义。
- 无 `find` 行级查询误用。
- `knowledge.md` summary 不提 chunk 工程状态。
- JSON/CSV 文件和派生实体都有可用 `brief/detail`。
