# Pontis 阶段总结

## 项目定位

Pontis 是一个项目数据分析智能体。用户指定项目文件夹后，Pontis 自动提取其中所有数据文件（数据库、CSV、JSON、YAML、文本等）的元数据，构建虚拟文件系统（VFS），然后通过交互式 Agent 对话回答关于项目数据的问题。

## 架构概览

```
用户项目文件夹/
├── data.db, config.json, report.csv, ...
└── .pontis/                    ← VFS 影子目录（与源数据同构）
    ├── data.db/
    │   ├── _meta.yml           ← 文件级元数据
    │   └── _entity/
    │       ├── users.table/_meta.yml      ← 表级元数据
    │       ├── users.id.INT.col/_meta.yml ← 列级元数据
    │       └── ...
    ├── config.json/_meta.yml
    └── report.csv/_entity/...
```

工作流程分两大阶段：

1. **Extractor（提取）**：扫描源数据，生成 `.pontis/` 影子目录，写入结构化元数据
2. **Agent（分析）**：基于元数据，通过 LLM + 工具调用回答用户问题

---

## 模块结构

### extractor/ — 数据提取管线

9 个 Phase 顺序执行，从骨架到 AI 总结：

| Phase | 职责 | 模块 |
|-------|------|------|
| 1 | 扫描源文件，生成 VFS 骨架 | `skeleton.py` |
| 1.5 | 展开实体（表/视图/列） | `db_basic.py`, `csv_basic.py`, `serialized_basic.py`, `text_basic.py` |
| 2 | DB 信息与列统计 | `db_info.py`, `db_table_info.py`, `db_column_stats/sample/topk.py` |
| 3 | CSV 信息与列统计 | `csv_info.py`, `csv_column_stats/sample/topk.py` |
| 4 | JSON 模式提取 | `json_pattern.py` |
| 5 | 文本文件信息 | `text_info.py` |
| 6 | 外键关系检测 | `db_table_relations.py` |
| 7 | 列值重叠检测 | `db_column_overlap.py` |
| 8 | 列关系 LLM 打分 | `db_column_rel.py` |
| 9 | AI 总结（分两次调用 LLM） | `ai_db_summary.py`, `ai_db_table/table/column/json/text_summary.py` |

共享工具：
- `ai_utils.py`：分两次调用 LLM（第一次写 detail，第二次压缩 brief），保证 brief <= 50 字符且远短于 detail
- `utils.py`：VFSStorage、NodeRef、LLMClient 等基础设施

### agent/ — 交互式分析 Agent

| 文件 | 职责 |
|------|------|
| `agent.py` | Agent 主循环：读输入 → 调 LLM → 执行工具 → 返回结果，无工具调用轮次上限 |
| `config.py` | Agent 独立配置（provider/model/api_key） |
| `tools.py` | 7 个工具的 OpenAI function schema 注册与执行分发 |
| `system_prompt.py` | 系统提示词（项目概览 + 工具说明 + 路径约定） |

### tool_use/ — 7 个工具实现

每个工具子目录包含 `tool.py`（实现）和 `prompt.py`（LLM 可读的描述）：

| 工具 | 功能 |
|------|------|
| `glob` | 按 path::entity 模式搜索文件和实体 |
| `grep` | 正则搜索文件内容 |
| `read` | 读取文件内容或实体数据（支持 DB 表/列直接查询） |
| `meta` | 查看元数据（支持 --all 和 property= 精确查询） |
| `lookup` | 按属性查找实体 |
| `search` | 模糊搜索 |
| `bash` | 执行 shell 命令（受限） |

共享配置：
- `tool_use/utils/config.py`：统一按后缀名匹配的显示配置
  - `INFO_TYPE_CONFIG`：glob/search 的 info 模板
  - `META_TYPE_CONFIG`：meta 各类型的 default_keys + folded_keys + untruncated_keys
- `tool_use/utils/formatters.py`：格式化逻辑（info 模板渲染、meta 输出、折叠/截断控制）

---

## 关键设计决策

### 1. VFS 影子目录
`.pontis/` 目录结构与源数据一致，每个文件/实体一个 `_meta.yml`，实体放在 `_entity/` 子目录。支持 `path::entity` 语法（如 `data.db::users.id.INT.col`）。

### 2. 类型识别
统一按文件/实体后缀名识别类型（`.db`, `.table`, `.col`, `.json` 等），不依赖 meta 中的 `type` 字段。

### 3. Meta 显示配置
每个类型独立配置：
- `default_keys`：meta 默认展示哪些字段（包含 detail，不含 brief）
- `folded_keys`：折叠为一行摘要的字段（如 `.col` 的 sample/topk）
- `untruncated_keys`：不截断完整展示的字段（detail, brief）

### 4. AI 总结策略
- 分两次调用 LLM：第一次生成 detail，第二次基于 detail 压缩 brief
- Prompt 要求避免时效性差的信息（具体行数、基数等），使用定性描述
- brief <= 50 字符，LLM 自行控制字数，不做硬截断

### 5. CSV 列类型推断
`csv_basic.py` 采样前 100 行推断列类型（INT/FLOAT/TEXT），而非统一写死 TEXT。

---

## 当前状态

- Extractor 管线完整，9 Phase 全部可用
- Agent 可正常对话，7 个工具均可调用
- AI 总结质量已校验：detail 详细无格式垃圾，brief 简短准确
- Meta/glob/search 显示配置已调优
- 测试脚本 `test/run_test.py` 可记录完整 Agent 调用链到 Markdown

## 未完成 / 待改进

- `lookup` 和 `search` 工具尚未充分测试
- Extractor 尚无增量更新能力（每次全量重建）
- `path` 字段仍存储在 meta 中（大量模块依赖，暂未移除）
- 无多数据库支持（仅 SQLite）
- Agent 无多轮对话上下文压缩机制
