# Pontis 项目总结

## 项目定位

Pontis 是一个**项目数据知识图谱提取与 AI 分析系统**。核心思路是：将一个任意项目文件夹中的结构化和半结构化数据（数据库、CSV、JSON、文本等）自动提取为知识图谱，然后通过 LLM Agent 以自然语言交互方式回答关于数据的问题。

Pontis 不是直接让 AI 读原始文件，而是先通过 Extractor 管线构建一个结构化的中间层（`.pontis/` 影子目录），Agent 只与这个中间层交互。这样做的好处：
- AI 不需要直接访问原始数据，减少幻觉和误读
- 元数据经过多轮统计、检测、AI 总结，质量高于单次 AI 全量阅读
- 工具层可以精确控制 AI 看到什么信息，降低 token 消耗

## 整体流程

```
┌──────────────────────────────────────────────────────────────┐
│                      用户项目文件夹                            │
│         event.db, expense.csv, config.json, report.md        │
└────────────────────────┬─────────────────────────────────────┘
                         │  python -m extractor ./
                         ▼
┌──────────────────────────────────────────────────────────────┐
│              Extractor 管线 (9 Phase)                         │
│  骨架 → 实体展开 → 统计 → 关系检测 → AI 总结                   │
└────────────────────────┬─────────────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────────────┐
│                  .pontis/ 知识图谱                            │
│   文件元数据 + 逻辑实体 + 统计信息 + 关系边 + AI 总结            │
└────────────────────────┬─────────────────────────────────────┘
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
        CLI 交互     Web 前端     Writer Agent
       (pontis)    (浏览器)     (自动化脚本)
            │            │            │
            ▼            ▼            ▼
┌──────────────────────────────────────────────────────────────┐
│              PontisAgent (LLM + Tool Calling)                 │
│   只读工具: glob, grep, read, meta, lookup, search, bash      │
│   写入工具: create_entity, update_meta                        │
└──────────────────────────────────────────────────────────────┘
```

## 核心概念

### 1. 虚拟文件系统 (VFS)

`.pontis/` 是用户项目文件夹的影子目录，结构完全镜像源数据：

```
.pontis/
├── _edges.yml                          # 关系边（知识图谱的边）
├── event.db/                           # 每个源文件一个目录
│   ├── _meta.yml                       # 文件级元数据
│   └── _entity/                        # 逻辑实体
│       ├── event.table/_meta.yml
│       ├── event.event_name.TEXT.col/_meta.yml
│       └── event.user_id__to__users.id.fk/_meta.yml
├── config.json/
│   ├── _meta.yml
│   └── _entity/
│       └── $.records.pattern/_meta.yml
└── report.md/_meta.yml
```

每个 `_meta.yml` 存储该节点的所有元数据（统计信息、AI 总结等），关系边统一存储在根级 `_edges.yml`。

### 2. 逻辑实体

逻辑实体是从文件中提取的语义对象，不是原始行或单个值，而是有意义的结构：

| 类型后缀 | 含义 | 命名规则 | 示例 |
|----------|------|----------|------|
| `.table` | 数据库表 | `{表名}.table` | `users.table` |
| `.col` | 数据列 | `{表}.{列}.{类型}.col` | `users.id.INT.col` |
| `.fk` | 外键关系 | `{表A}.{列A}__to__{表B}.{列B}.fk` | `users.dept_id__to__dept.id.fk` |
| `.view` | 视图 | `{名称}.view` | `active_users.view` |
| `.overlap` | 列重叠 | 同 fk 格式 `.overlap` | 统计检测的列值重叠 |
| `.rel` | 语义关系 | 同 fk 格式 `.rel` | LLM 验证的列关联 |
| `.pattern` | JSON 路径模式 | `$.路径.pattern` | `$.records[].pattern` |
| `.chunk` | 文本分片 | `{名称}.chunk` | 长文档分段 |

### 3. path::entity 统一路径语法

所有工具使用统一的 `path::entity` 语法定位数据：

```
**/*.db                     → 所有数据库文件
**/*.db::*.table            → 所有数据库中的表
event.db::event.*.col       → event 表的所有列
event.db::event.user_id__to__users.id.fk  → 具体的外键
```

### 4. 虚属性 (Virtual Properties)

不存储在 `_meta.yml` 中，在读取时按需计算（如 file_size、modified_at、row_count 等）。只补充缺失字段，已有字段优先（尊重 extractor 预计算的值）。

## 模块详解

### extractor/ — 数据提取管线

9 个 Phase 顺序执行，从骨架到 AI 总结：

| Phase | 职责 | 核心模块 |
|-------|------|----------|
| 1 | 扫描源文件，生成 VFS 骨架 | `skeleton.py` |
| 1.5 | 展开实体（表/视图/列/JSON 模式） | `db_basic.py`, `csv_basic.py`, `serialized_basic.py`, `text_basic.py` |
| 2 | DB 文件信息 + 表/列统计 | `db_info.py`, `db_table_info.py`, `db_column_stats/sample/topk.py` |
| 3 | CSV 文件信息 + 列统计 | `csv_info.py`, `csv_column_stats/sample/topk.py` |
| 4 | JSON 结构模式提取 | `json_pattern.py`（递归探查、Map 检测、命名模式识别） |
| 5 | 文本文件统计 | `text_info.py`（行数、字符数、编码等） |
| 6 | 外键关系检测 | `db_table_relations.py`（物理 FK + 命名规则推断） |
| 7 | 列值重叠检测 | `db_column_overlap.py`（Jaccard 相似度、漏斗过滤） |
| 8 | 列关系 LLM 打分 | `db_column_rel.py`（启发式分数 + LLM 语义验证） |
| 9 | AI 总结 | `ai_db/table/column/json/text_summary.py`（两次 LLM：detail → brief） |

共享基础设施：
- `utils.py`：VFSStorage（`.pontis/` 读写）、NodeRef（节点引用）、LLMClient、Config
- `ai_utils.py`：两轮 LLM 调用策略（先 detail 后 brief），brief ≤ 50 字符

### storage/ — 存储抽象层

| 文件 | 职责 |
|------|------|
| `store.py` | ProjectStore：Agent 使用的统一存储接口，包含只读方法（get_meta、glob_entities、read）和写入方法（write_meta、create_entity_dir、add_edges） |
| `virtual_props.py` | 虚属性入口：enrich_meta() 补充计算属性 |
| `virtual_props_extract/` | 按类型分模块的虚属性计算函数（common、directory、database、table、textfile） |

### agent/ — 智能体核心

| 文件 | 职责 |
|------|------|
| `agent.py` | PontisAgent：LLM 主循环（chat / chat_stream / run），接受可注入的 ToolRegistry 和 system_prompt |
| `tools.py` | ToolRegistry 注册表模式：`build_readonly_registry()` 7 个只读工具，`build_writer_registry()` 追加 2 个写入工具 |
| `system_prompt.py` | 只读模式 system prompt：Pontis 概念、工具策略、项目概览 |
| `writer_prompt.py` | 写入模式 system prompt：扩展读取工具说明 + create_entity/update_meta 使用规则 |
| `config.py` | Agent 配置加载（provider/model/api_key） |

**Agent 双模式设计**：
- **只读模式**（CLI/Web）：7 个只读工具，不修改 `.pontis/`
- **写入模式**（自动化脚本）：9 个工具（7 只读 + 2 写入），可创建实体和更新 meta

### tool_use/ — 工具实现

每个工具子目录包含 `tool.py`（实现）和 `prompt.py`（LLM 可读描述）。

**只读工具（7 个）：**

| 工具 | 功能 |
|------|------|
| `glob` | 按 path::entity 模式搜索文件和实体，返回分页结果 |
| `grep` | 正则搜索文件内容（ripgrep），支持 content/files_with_matches/count 模式 |
| `read` | 读取文件或实体数据（DB 表/列直接 SQL 查询，chunk 读 _raw） |
| `meta` | 查看元数据，按类型配置展示默认字段，支持 `all` 和 `property=` 精确查询 |
| `lookup` | 按属性值查找实体（支持 `INT > 100`、`STR = "active"` 等谓词） |
| `search` | 关键词搜索所有元数据 |
| `bash` | 执行 shell 命令（兜底工具） |

**写入工具（2 个）：**

| 工具 | 功能 |
|------|------|
| `create_entity` | 创建逻辑实体 + 可选写入 meta + 可选自动建边 |
| `update_meta` | 合并更新 meta 字段（brief/detail，可扩展其他字段） |

**共享配置**：
- `utils/config.py`：按后缀的显示配置（INFO_TYPE_CONFIG、META_TYPE_CONFIG）+ 分页配置
- `utils/formatters.py`：格式化逻辑（info 模板渲染、meta 输出、折叠/截断控制）
- `utils/path_parser.py`：path::entity 语法解析

### 入口点

| 入口 | 用途 | 命令 |
|------|------|------|
| `pontis_cli.py` | CLI 交互 | `pontis <folder>` |
| `run_web.py` | Web 前端 | `python run_web.py [--host] [--port]` |
| `writer_agent.py` | 自动化写入 | `python writer_agent.py <path> --task "任务描述"` |
| `extractor/__main__.py` | 数据提取 | `python -m extractor <project_dir>` |

### 配置系统

`common/config.py` — `PontisConfig` 数据类，双 LLM 配置：
- Extractor 配置：廉价模型（默认 `deepseek-chat`）用于批量元数据生成
- Agent 配置：强推理模型（默认 `deepseek-reasoner`）用于交互分析

配置加载优先级：`~/.pontis/config.yml` → `<project>/pontis.yml` → 环境变量

### Web 前端

FastAPI + SSE (Server-Sent Events) 架构：
- 后端 `front-end/server.py`：`/api/validate`（路径验证）、`/api/chat`（SSE 流式对话）、会话管理
- 前端 `front-end/static/`：暗色主题聊天 UI，工具调用折叠展示，`?project=` URL 参数指定项目路径

## 关键设计决策

### 1. Agent 与原始数据隔离
Agent 不直接读原始文件，只通过 ProjectStore 访问 `.pontis/` 中的结构化元数据。这保证了：
- AI 不会误读原始数据格式
- 工具层控制 AI 看到的信息粒度
- 减少不必要的 token 消耗

### 2. 工具分页与显示配置
按类型后缀配置显示模板和分页限制（glob 默认 100 条、grep 250 条等），避免单次工具调用返回过多数据。

### 3. Prompt 引导减少 token 浪费
System prompt 中明确指导 AI 的工作流：先 glob 后 meta 再 read、避免全量 read、利用上下文已有信息、meta 的 detail 通常已够用。

### 4. 可注入的 Agent 架构
PontisAgent 通过 ToolRegistry 和 system_prompt 参数支持不同模式，只读/写入模式共享核心 LLM 循环，仅工具集和提示词不同。

### 5. AI 总结的两轮调用策略
先让 LLM 生成详细 detail，再基于 detail 压缩 brief（≤50 字）。避免一次生成 brief 时丢失重要信息。

### 6. CSV 列类型推断
采样前 100 行推断列类型（INT/FLOAT/TEXT），而非统一标记为 TEXT。

## 当前状态

- Extractor 管线完整，9 Phase 全部可用
- Agent 只读模式稳定，7 个工具均正常工作
- Writer Agent 可用（create_entity + update_meta），支持 CLI 和 Python API
- Web 前端可用，支持 SSE 流式对话
- 虚属性模块化重构完成，新类型只需添加模块并注册
- AI 总结质量已校验
- 测试脚本 `test/run_test.py` 可记录完整 Agent 调用链

## 待改进

- `lookup` 和 `search` 工具尚未充分测试（search 目前是关键词匹配，未接入向量检索）
- Extractor 无增量更新能力（每次全量重建）
- 仅支持 SQLite 数据库
- Agent 无多轮对话上下文压缩机制（长对话后 token 会膨胀）
- Writer Agent 创建的实体未标记来源（后续可在 meta 中加 `_source: ai`）
- `.chunk` 文本分片实体尚未实现
