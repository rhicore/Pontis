# 知识层寻址方案对比分析

## 背景

当前 Pontis 知识图谱的所有实体都以文件为锚点，通过 `::` 分隔符寻址：

```
formula_1.db::drivers.table        ← 文件 :: 实体
formula_1.db::drivers.points.REAL.col
```

现在需要引入**跨文件的知识实体**（convention、few_shot、pattern 等），这些知识没有自然的文件锚点。
核心问题：**知识实体应该怎么寻址？**

---

## 现状：`::` 的深度绑定

`::` 不是表面的显示约定，而是贯穿全系统的寻址原语：

| 层 | 使用位置 | 作用 |
|---|---|---|
| **Store** | `resolve_ref()` / `_ref_from_parts()` | 拆分 path + entity_name |
| **Store** | `find_nodes()` | 第一段匹配文件，后续段沿边遍历 |
| **Store** | `_build_index()` | 判断节点类型（文件 vs 实体） |
| **Storage** | `_meta.yml` 的 `_entity_name` 字段 | 存储 `::` 右侧部分 |
| **Tool** | glob / meta / read / grep / search | 所有 ref 参数都依赖 `::` |
| **Tool** | `path_parser.py` | 整个文件基于 `::` 拆分 |
| **Tool** | `formatters.py` | 检测 `::` 决定显示格式 |
| **Extractor** | ~20 个提取器模块 | `ref.split("::", 1)` 构建实体 |
| **Guardrail** | sql_check / sql_join_check / sql_disambig_check | 解析 ref 中的表名/列名 |
| **Prompt** | `_base.py` / `_entities.py` | 教 LLM 使用 `::` 语法 |

全链路约 50+ 处硬编码 `::` 分割逻辑。

---

## 方案 1：取消 `::`，统一 `/` 路径

### 设计

```
formula_1.db/tables/drivers                    ← 表
formula_1.db/tables/drivers/columns/speed      ← 列
knowledge/conventions/no_concat_names           ← 知识
```

所有实体用文件系统风格的层级路径寻址，不再区分"文件"和"实体"。

### 对 agent 的影响

**优势：**
- **路径是 LLM 最熟悉的命名空间**。所有训练数据中都有大量文件路径、URL、Python import 路径。LLM 不需要学习自定义的 `::` 语法
- **glob 语义更直觉**：`formula_1.db/tables/*/columns/*` vs `formula_1.db::*.table::*.*.*.col`。前者是标准 glob，LLM 天然会写；后者是自定义语法，需要 prompt 教
- **知识节点的可发现性最好**：`knowledge/` 目录和 `formula_1.db/` 平级，agent 做 `glob "*"` 自然就能看到，不需要知道特殊命名空间
- **与主流 agent 框架一致**：Claude Code、Cursor、OpenAI file_search 都用路径寻址

**劣势：**
- `::` 的两阶段遍历语义丢失。当前 `glob "data.db::*.table"` 是"先找文件，再沿边遍历"，换成 `/` 后需要重新设计遍历引擎
- Store 内部的 `_entity_name` / `_files` 字段结构需要重写
- 50+ 处代码需要同步修改，风险高

### 改动评估

| 模块 | 改动程度 |
|---|---|
| Store (store.py) | **重写** — resolve_ref、find_nodes、_build_index 全改 |
| path_parser.py | **重写** — 整个文件逻辑翻转 |
| formatters.py | **中** — 显示逻辑调整 |
| 10+ tool 文件 | **中** — ref 参数解析 |
| ~20 extractor 模块 | **中** — ref 构建方式 |
| Prompt | **小** — 反而更简单（不需要教 `::`） |
| Guardrail | **小** — 路径解析调整 |

**结论：终极形态，但当前阶段改动量和风险都太大。**

---

## 方案 2：虚拟命名空间

### 设计

```
formula_1.db::drivers.table                          ← 数据实体（不变）
_knowledge_::no_concat_names.convention              ← 知识实体（虚拟锚点）
_knowledge_::bird_train_q123.few_shot
```

`_knowledge_` 不是真实文件，而是一个虚拟命名空间。`::` 含义不变，只是"文件"部分可以是虚拟的。

### 对 agent 的影响

**优势：**
- **改动最小**。`::` 语义完全不变，Store 核心逻辑不动
- 数据实体的寻址和遍历方式完全不受影响
- 知识实体有独立的命名空间，语义清晰

**劣势：**
- **可发现性差**。agent 做 `glob "*"` 只会看到真实文件，看不到 `_knowledge_`。需要 agent 知道去 `glob "_knowledge_::*"` — 这是一个隐式约定
- agent 需要学习两套规则："找数据用 `*.db::`，找知识用 `_knowledge_::`"。认知负担反而增加
- `_knowledge_` 是 Pontis 专有的魔法值，LLM 训练数据中没有任何参考，完全依赖 prompt 指令
- **违反最小惊讶原则**：agent 看到 `_knowledge_::foo.convention`，第一反应是去找 `_knowledge_` 文件，但找不到

### 改动评估

| 模块 | 改动程度 |
|---|---|
| Store (store.py) | **小** — `resolve_ref` 允许 `_knowledge_` 不对应真实文件 |
| find_nodes | **小** — 支持 `_knowledge_::*` 模式 |
| Tool / Extractor | **无** — `::` 语义不变 |
| Prompt | **小** — 增加一段 `_knowledge_` 的说明 |
| 新增：知识实体创建器 | **小** — 新 extractor 或脚本 |

**结论：最快能跑起来的方案，但 agent 的可发现性和认知负担是硬伤。**

---

## 方案 3：知识文件

### 设计

```
knowledge/conventions.yaml::no_concat_names.convention
knowledge/few_shots.yaml::bird_train_q123.few_shot
```

知识实体照常以 `文件::实体` 寻址，只是"文件"是人工或系统创建的知识容器文件。

### 对 agent 的影响

**优势：**
- **零改动**。现有 Store、工具、Extractor、Prompt 全部不动
- **可发现性最好**：agent 做 `glob "*"` 自然看到 `knowledge/` 目录和其中的 yaml 文件，与发现 `.db` 文件的体验完全一致
- **agent 零认知负担**：不需要学习新规则，对知识实体的操作（glob / meta / read / search）与数据实体完全相同
- **人可读可编辑**：yaml 文件本身就是文档，开发者可以直接阅读和修改知识
- **与现有 agent 框架一致**：大多数 agent 框架（Claude Code 的 CLAUDE.md、Cursor 的 .cursorrules）都把配置和知识放在项目目录下的特殊文件中

**劣势：**
- "一个知识节点也要创建一个文件"感觉多余
- 知识文件和元数据文件（`.pontis/`）的边界模糊 — 为什么不直接放 `.pontis/` 里？

### 改动评估

| 模块 | 改动程度 |
|---|---|
| Store | **无** |
| Tool / Extractor | **无**（或增加一个 yaml 提取器） |
| Prompt | **无**（或增加 yaml 实体类型说明） |

**结论：最 boring 的方案，但对 agent 来说一致性最好。**

---

## Agent 认知负担对比

LLM 的认知负担取决于两个因素：**需要学多少自定义规则** 和 **操作是否能复用已有心智模型**。

| 维度 | 方案 1 (统一 `/`) | 方案 2 (虚拟命名空间) | 方案 3 (知识文件) |
|---|---|---|---|
| 寻址规则数量 | 1 套（路径） | 2 套（文件 `::` + 虚拟 `::`） | 1 套（`文件::实体`） |
| LLM 对规则的熟悉度 | 高（路径是通用概念） | 低（`_knowledge_` 是魔法值） | 中（`::` 需要学，但规则统一） |
| 可发现性 | 高（`glob "*"` 看到一切） | 低（需要知道 `_knowledge_`） | 高（`glob "*"` 看到一切） |
| 操作一致性 | 完全统一 | 数据和知识操作相同但入口不同 | 完全统一 |
| prompt 复杂度 | 低（路径语法自解释） | 中（需要解释虚拟命名空间） | 低（复用现有 prompt） |

---

## 主流 Agent 框架的 Knowledge 寻址设计

以下逐一分析 8 个主流框架如何解决"agent 怎么发现和访问知识"这个问题。

### 1. Claude Code — 项目目录下的 Markdown 文件

Claude Code 的知识系统完全基于项目目录下的文件：

```
~/.claude/CLAUDE.md              ← 全局知识（所有项目共享）
~/projects/my-app/CLAUDE.md      ← 项目知识（自动加载）
~/projects/my-app/src/CLAUDE.md  ← 目录级知识（进入该目录时加载）
```

**核心设计**：
- **知识就是文件**。CLAUDE.md 是标准 Markdown，人可读可编辑
- **层级发现**：agent 启动时自动沿目录树向上查找 CLAUDE.md 并加载，不需要任何特殊指令
- **无虚拟命名空间**：不存在 `_memory_::xxx` 之类的非文件引用，所有知识都通过真实文件路径定位
- **Memory 系统**：持久化记忆也存为文件（`~/.claude/projects/{project}/memory/*.md`），agent 通过读文件访问

与 Pontis 方案对应：**方案 3**。知识的载体是真实文件，agent 用和访问代码文件完全相同的方式访问知识文件。

### 2. Cursor — .cursorrules + Skills 文件

Cursor 的知识系统也是项目目录下的特殊文件：

```
project/
├── .cursorrules          ← 项目级规则（自动加载）
├── .cursor/
│   └── rules/            ← 规则目录
│       ├── general.mdc
│       └── react.mdc
├── skills.md             ← 技能定义文件（2026 新增）
```

**核心设计**：
- `.cursorrules` 是纯文本规则文件，agent 启动时自动读取
- `skills.md` 定义可复用的 agent 技能模板
- **无自定义寻址协议**：agent 用标准文件路径访问所有知识，不发明新的引用语法
- MCP（Model Context Protocol）用于连接外部工具，但知识本身仍在文件中

与 Pontis 方案对应：**方案 3**。

### 3. OpenAI Agents SDK — FileSearch + Vector Store

OpenAI 的知识系统基于上传文件 + 向量检索：

```
# 知识流程
1. 上传文件 → OpenAI Vector Store
2. Agent 通过 FileSearch 工具检索
3. 返回匹配的文件片段

# 代码示例
agent = Agent(
    tools=[FileSearchTool(vector_store_ids=["vs_xxx"])]
)
```

**核心设计**：
- **文件是知识的唯一来源**。用户上传 PDF/CSV/TXT 等文件，SDK 自动分块嵌入向量数据库
- **无虚拟节点**：不存在"不属于任何文件的知识片段"。每条检索结果都能追溯到源文件
- **路径即引用**：检索结果中包含源文件名和位置，agent 通过标准文件路径理解知识来源
- 向量数据库是**检索机制**而非**寻址机制**——寻址仍然基于文件

与 Pontis 方案对应：**方案 3**（文件为锚点）+ RAG 检索层。

### 4. CrewAI — Knowledge Source 文件 + ChromaDB

CrewAI 的知识系统支持多种文件格式，统一存入向量数据库：

```python
# 支持的知识源类型（全部基于文件或字符串）
StringKnowledgeSource(content="...")          # 字符串
TextFileKnowledgeSource(file_paths=["a.txt"])  # 文本文件
PDFKnowledgeSource(file_paths=["a.pdf"])       # PDF
CSVKnowledgeSource(file_paths=["data.csv"])    # CSV
JSONKnowledgeSource(file_paths=["data.json"])  # JSON
```

**核心设计**：
- **知识源 = 文件 + 元数据**。每条知识记录包含 source_file 和 chunk_id
- **两级知识**：Agent 级别（agent 专属知识）和 Crew 级别（所有 agent 共享），但都是文件输入
- **存储位置**：知识存入 ChromaDB（`~/.local/share/CrewAI/{project}/knowledge/`），但**每条记录都可追溯到源文件**
- **无虚拟命名空间**：不存在"无文件锚点的知识实体"。即使 StringKnowledgeSource 也会被赋予内部文件标识

关键点：CrewAI 的向量数据库是**检索加速层**，不是寻址层。知识实体的身份来源是文件，不是向量 ID。

与 Pontis 方案对应：**方案 3**。

### 5. Devin — Knowledge Base + Playbooks

Devin 的知识系统是最接近"企业级知识管理"的：

```
# Devin 自动扫描以下内容构建知识
- README 文件
- 项目文件结构
- 约定文件（convention files）

# 高级功能
- Knowledge Base：组织级知识库，按 repo/folder 组织
- Playbooks：从成功会话中提取的可复用流程
- Knowledge Suggestions：agent 在会话中自动生成知识建议
```

**核心设计**：
- **知识按文件夹组织**：知识条目存在文件夹结构中，按 repo 分类，支持搜索和浏览
- **Playbooks 是特殊的文件**：从会话历史中提取，存为结构化文件，agent 可以引用和执行
- **MCP 访问**：所有知识管理功能通过 MCP server 暴露，但底层存储仍然是文件系统
- **自动知识发现**：agent 扫描 README、目录结构、convention 文件来理解项目，不依赖虚拟命名空间

与 Pontis 方案对应：**方案 3**（知识按文件夹组织，文件为载体）。

### 6. SWE-Agent / mini-swe-agent — 纯文件系统 ACI

SWE-Agent 的设计哲学是 **Agent-Computer Interface (ACI)**：

```
# SWE-Agent 的工具集（全部是标准文件操作）
- open <path>        # 打开文件
- edit <path>        # 编辑文件
- search_dir <pattern> # 搜索目录
- bash <command>     # 执行 shell 命令
```

**核心设计**：
- **零自定义寻址**：所有操作都用标准文件路径，没有 `::` 或任何自定义分隔符
- **知识 = 代码文件本身**：不存在单独的"知识文件"，代码库的所有文件就是 agent 的全部知识
- **ACI 原则**：给 agent 设计好用的文件操作工具，而不是设计复杂的知识图谱

SWE-Agent 的设计是方案 1（统一路径）的极端形态。但它的前提是知识本来就存在文件中（代码文件），不需要额外的知识层。

与 Pontis 方案对应：**方案 1**（统一路径，无二分）。

### 7. AutoGPT — Workspace + Vector Memory

AutoGPT 采用混合架构：

```
workspace/                    ← 工作区目录（文件操作）
├── auto_gpt_workspace.py
├── output/
│   └── research_result.txt

Memory:                      ← 向量记忆（FAISS / Pinecone / Weaviate）
├── short-term memory        ← 当前对话
├── long-term memory         ← 向量数据库
└── entity memory            ← 结构化实体记忆
```

**核心设计**：
- **双存储**：文件系统存工作产出，向量数据库存语义记忆
- **文件优先**：agent 的主要交互对象是 workspace 中的文件
- **向量记忆是辅助**：用于检索历史对话和长期知识，但不作为寻址机制
- 文件存储支持本地、Google Cloud Storage、S3

与 Pontis 方案对应：**方案 3**（文件 workspace）+ 向量检索辅助。

### 8. LangChain / LangGraph — RAG Pipeline

LangChain 的知识系统是经典的 RAG 架构：

```
Documents → Chunking → Embedding → Vector Store → Retriever → Agent
```

**核心设计**：
- **Document 是基本单元**：每个 Document 有 `page_content` + `metadata`（包含 source 文件路径）
- **文件 → 文档 → 向量**：知识来源于文件，经过处理后存入向量数据库
- **Retriever 是工具**：agent 通过 retriever 工具访问知识，不直接操作向量数据库
- **无虚拟命名空间**：所有文档都追溯到源文件

2025-2026 趋势：LangGraph 正在取代纯 LangChain 用于构建 agentic RAG，但文档 → 向量 → 检索的核心模式不变。

与 Pontis 方案对应：**方案 3**（文件为知识源）+ RAG 检索。

---

### 跨框架共性与规律

| 维度 | 所有框架的共识 |
|---|---|
| **知识载体** | **全部使用文件**。无一例外 |
| **寻址方式** | 标准文件路径，无自定义命名空间 |
| **发现机制** | 目录扫描（glob/list）或向量检索，但身份由文件定义 |
| **虚拟命名空间** | **没有框架使用**。CrewAI 的 StringKnowledgeSource 也有内部文件标识 |
| **人可编辑性** | 全部支持人直接编辑知识文件（Markdown / YAML / TXT） |
| **检索 vs 寻址** | 向量数据库是检索加速层，不是寻址机制。知识的身份来自文件，不来自向量 ID |

**关键结论**：即使框架内部使用向量数据库（CrewAI、OpenAI、LangChain），知识的**身份**仍然绑定到源文件。向量数据库解决的是"怎么快速找到相关知识"，不是"知识住在哪"。这是一个清晰的**两层分离**：

1. **寻址层**：文件路径 — 知识的身份和命名空间
2. **检索层**：向量 / BM25 / glob — 知识的发现和访问

Pontis 当前的设计（Store 存储实体 + glob/search/meta 访问）已经是这个模式的实现。知识文件方案只是复用这个现有模式。

---

## 知识交付机制：模型实际看到什么

上面分析了知识的存储和寻址。但更关键的问题是：**知识如何到达模型的 context window？模型需要主动读取，还是框架强制注入？**

这直接决定了 Pontis 知识层的设计：知识是放进 Store 让 agent 通过工具发现，还是直接注入 prompt 让 agent 被动接收？

### 交付模式分类

| 模式 | 机制 | 模型需要行动吗？ | 使用者 |
|---|---|---|---|
| **系统注入** | 知识写入 system prompt，每次请求都带 | 不需要 | ChatGPT |
| **用户消息注入** | 知识 prepend 到 user prompt | 不需要 | Claude Code |
| **条件注入** | 匹配 trigger 时注入 context | 不需要 | Devin、Cursor |
| **框架自动检索** | kickoff 时自动 query + 注入 | 不需要 | CrewAI |
| **模型主动调用工具** | 模型决定何时调用 file_search tool | 需要 | OpenAI Assistants |

### Claude Code — 用户消息注入（模型完全被动）

**模型看到的**：每条 user message 前面都 prepend 了 CLAUDE.md 的内容。模型不觉得自己在"读文件"，它看到的就是用户消息的一部分。

```
# 模型实际收到的 user message 结构
[CLAUDE.md 全局内容]
[CLAUDE.md 项目内容]
[MEMORY.md 索引]
---
[用户实际输入]
```

**关键细节**：
- CLAUDE.md 不是注入到 system prompt，而是 **prepend 到 user prompt**
- System prompt 只有 ~2,900 tokens，定义 "你是什么"
- CLAUDE.md 内容可以是几千到上万 tokens，全部自动注入
- `MEMORY.md`（记忆索引）也是自动加载到 context 的，模型不需要主动 Read
- 模型**有** Read 工具可以读这些文件，但**不必须用**——内容已经在 context 里了
- 对话过程中如果记忆被更新，下次消息自动带上新内容

**模型视角**：我收到了一段很长的用户消息，开头是项目规则和记忆。我按照这些规则行动。

### ChatGPT — 系统注入（最激进的强制注入）

**模型看到的**：System prompt 中有一个 `Model Set Context` 区段，包含所有记忆条目。模型把这些当作系统级指令对待。

```
# ChatGPT system prompt 中的记忆区段
MODEL SET CONTEXT
1. [2025-05-02]. The user likes ice cream and cookies.
2. [2025-05-04]. The user lives in Seattle.
...

ASSISTANT RESPONSE PREFERENCES       ← 15 条自动提取的偏好
NOTABLE PAST CONVERSATION TOPICS      ← 8 条对话主题摘要
HELPFUL USER INSIGHTS                 ← 14 条用户画像
RECENT CONVERSATION CONTENT           ← ~40 条最近对话（含用户原始消息）
USER INTERACTION METADATA             ← 17 条使用统计
```

**关键细节**：
- 模型**没有读取记忆的工具**。`bio` tool 只能写入，不能读取
- 所有记忆（显式写入的 + 自动提取的）全部 force-inject 进 system prompt
- 不只是用户主动保存的记忆，还包括系统自动从历史对话中提取的偏好、主题摘要、用户画像
- 每条记忆都带 `Confidence` 标签（high/medium/low），影响模型对记忆的信任度
- 模型**无法忽略**这些信息——它们在 system prompt 中，与核心指令同级

**模型视角**：这些是系统给我的指令级信息，我必须遵循。

### Cursor — 条件注入（注入 + 按需激活）

**模型看到的**：规则被注入到 system prompt 开头，但不是所有规则都时刻生效。

```
# Cursor 的规则注入两阶段
Stage 1: 注入 — .cursorrules 内容写入 system prompt
Stage 2: 激活 — 根据条件决定规则是否生效
  - alwaysApply: true → 永远生效
  - requestable → 模型根据上下文自行决定是否应用
  - agent → 由 agent 智能判断
```

**关键细节**：
- 规则内容始终在 context 中（已注入），但**是否被遵循**取决于激活条件
- `alwaysApply: true` 的规则等同于 ChatGPT 的系统注入——模型必须遵循
- `requestable` 规则虽然在 context 中，但模型可以忽略
- Cursor 会**持续重新注入** system prompt 到长对话中，防止上下文漂移

**模型视角**：system prompt 里有规则列表。有些是强制的，有些是参考性的。

### Devin — Trigger 匹配的条件注入

**模型看到的**：当 Devin 的当前任务与某个 Knowledge 条目的 trigger 描述匹配时，该条目的内容被注入 context。

```
# Devin Knowledge 条目结构
trigger: "when working with Python projects, code review conventions"
content: "Always use type hints. Prefer dataclasses over dicts..."

# 当 Devin 执行 code review 任务时 → 匹配 trigger → 注入 content
# 当 Devin 执行 bug fix 任务时 → 不匹配 → 不注入
```

**关键细节**：
- 每个 Knowledge 条目**必须有 trigger 描述**，没有 trigger 的知识不会被检索
- 不是 RAG 向量检索——是 trigger 关键词匹配
- Playbooks（可复用流程）也通过类似机制按需注入
- 模型**不主动检索知识**——框架在每轮推理前自动匹配并注入

**模型视角**：我在执行任务时，context 中突然出现了一段相关知识。我不知道它从哪来，但它和我的任务相关。

### CrewAI — 框架自动检索 + 注入

**模型看到的**：`crew.kickoff()` 时，框架自动将 task prompt 改写为检索 query，从 ChromaDB 检索相关 chunks，注入到 agent 的 context 中。

```
# CrewAI 知识交付流程（全自动）
1. 用户输入 task prompt
2. 框架自动调用 LLM 改写 prompt 为检索 query
   "关于用户的问题" → "用户偏好 设置"
3. 框架用 query 检索 ChromaDB
4. 检索到的 chunks 注入到 agent 的 task context
5. agent 看到的 = task prompt + 注入的知识 chunks
```

**关键细节**：
- Agent **没有知识检索工具**。知识检索由框架在 `kickoff()` 时自动完成
- 每个 agent 有独立的知识 collection，但检索和注入都是隐式的
- 支持 agent 级和 crew 级两层知识，但交付机制相同
- 知识只在 kickoff 时注入一次，不是每轮对话都检索

**模型视角**：我收到了一个任务描述，里面已经包含了相关知识片段。

### OpenAI Assistants API — 模型主动调用工具

**模型看到的**：模型有一个 `file_search` tool，模型自己决定何时调用。调用后返回的文档 chunks 作为 tool output 出现在对话中。

```
# OpenAI FileSearch 交付流程
1. 用户提问
2. 模型判断：需要查知识 → 调用 file_search(query)
3. 系统返回匹配的文档 chunks（作为 tool output）
4. 模型基于 chunks 生成回答
```

**关键细节**：
- 这是**唯一需要模型主动行动的**框架
- 模型需要判断"这个任务需要查资料吗？"——增加了推理负担
- 但好处是模型可以精确控制检索时机和查询内容
- 2025-2026 版本中，OpenAI 也在探索自动检索（模型不调用 tool 时系统也注入）

**模型视角**：我有一个工具可以搜索文档。当我需要额外信息时，我调用它。

---

### 交付模式对比与对 Pontis 的启示

| 维度 | 系统注入 (ChatGPT) | 用户消息注入 (Claude Code) | 条件注入 (Devin/Cursor) | 框架自动 (CrewAI) | 模型主动 (OpenAI) |
|---|---|---|---|---|---|
| **模型需要学什么** | 什么都不用学 | 什么都不用学 | 什么都不用学 | 什么都不用学 | 需要学何时调用工具 |
| **Token 开销** | 高（每次都带） | 高（每次都带） | 中（按需） | 中（一次） | 低（按需） |
| **模型遵从度** | 最高（system 级） | 高（user 级） | 高（注入时） | 中（context 中） | 取决于模型判断 |
| **知识量上限** | 受 context 限制 | 受 context 限制 | 无限（按需注入） | 受检索质量限制 | 无限（按需调用） |
| **灵活性** | 低 | 低 | 中 | 中 | 最高 |

**核心发现**：5 个框架中 4 个选择**强制注入**，只有 OpenAI FileSearch 让模型主动调用。而且 OpenAI 也在往自动注入方向演进。

**对 Pontis 知识层的设计含义**：

Pontis 当前的设计是让 agent 通过 `glob → meta → read` 工具链**主动发现和读取**知识——这对应的是 OpenAI FileSearch 模式（模型主动）。但主流框架的实践表明：

1. **强制性知识（规则、约定）应该注入 prompt，而不是放在 Store 里等 agent 发现**。Pontis 的 `_benchmark.py` 提示词（"不要拼接列"、"不要 ROUND"）实际上已经是注入模式了——这些规则直接写在 prompt 里，agent 不需要去 Store 里读
2. **参考性知识（few-shot、pattern 统计）可以放在 Store 里让 agent 按需读取**。这类知识量大、按场景需要不同子集，适合工具访问模式
3. **两层混合是最常见的做法**：Claude Code 把核心规则注入 CLAUDE.md，但把详细文件留给 Read 工具；Cursor 把规则注入 system prompt，但代码文件需要模型主动读取

**具体建议**：
- `conventions.yaml`（跨库规则）→ **注入 prompt**，与当前 `_benchmark.py` 的做法一致
- `few_shots.yaml`（SQL 示例）→ 放 Store 中，agent 通过工具按需读取
- `patterns.yaml`（SQL 模式统计）→ 放 Store 中，agent 通过工具按需读取

---

## 推荐方案

### 阶段一（当前）：方案 3 — 知识文件

理由：
1. **零改动验证**。不需要改任何现有代码，立刻能验证知识层对 benchmark 的效果
2. **agent 一致性最好**。LLM 不需要学任何新东西
3. **与主流框架一致**。`knowledge/` 目录下的 yaml 文件就是项目知识

具体做法：
```
example_data/bird/
├── formula_1.db
├── card_games.db
├── california_schools.db
├── knowledge/
│   ├── conventions.yaml    ← 跨库约定（不拼接列、不 ROUND 等）
│   ├── few_shots.yaml      ← 从 train set 提取的 SQL 示例
│   └── patterns.yaml       ← 常见 SQL 模式统计
```

知识 yaml 的提取器和数据 db 的提取器并行运行，知识实体同样进 Store。

### 阶段二（长期）：视需要决定是否迁移到方案 1

如果未来 Pontis 从 SQLite 专用的 Text-to-SQL 工具演化为通用数据分析 agent：
- 数据源不只有文件（API、数据库连接、流式数据）
- 实体不再有自然的文件锚点
- `::` 的"文件优先"语义成为限制

此时再做方案 1 的统一路径重构，那时有更多实际使用场景支撑设计决策。

---

## 总结

| | 方案 1 (统一 `/`) | 方案 2 (虚拟 NS) | 方案 3 (知识文件) |
|---|---|---|---|
| 改动量 | **大**（50+ 文件） | **小**（3-5 文件） | **无** |
| Agent 认知负担 | **最低** | **最高** | **低** |
| 可发现性 | **高** | **低** | **高** |
| 与主流框架一致 | 部分 | **不一致** | **一致** |
| 当前风险 | **高** | **低** | **无** |
| 长期架构纯度 | **最高** | **中** | **中** |

**现在选方案 3 验证效果，架构重构留给方案 1 的时机。**
