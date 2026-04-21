现在extractor模块已经将数据库转化成了文件树状结构

你现在要写的就是读取文件树状结构，提供给大模型用的命令接口，本章优先关注ls命令






3. ls 输出规范（四字段模型）
无论进入哪种目录，ls 的输出必须严格对齐以下四个字段：
[HasSub] | [Name] | [Info] | [Brief]

字段填充逻辑：
[HasSub]: 存在子节点（如目录、列表、字典）显示 [+]，叶子节点显示 [ ]。

[Name]: 节点名称。注意： 必须包含完整的后缀名以标识类型（如 users.table, id.INT.col, data.json）。

[Info]: 类型相关的统计摘要。

.db: 显示包含的表/视图数量（如 3 tables, 1 view）。

.table: 显示行数和列数（如 1000 rows, 5 cols）。

.col: 显示唯一值计数或分布（如 Dist: 100）。

.chunk: 显示字符数或 Token 数（如 500 chars）。

序列化文件 (JSON/YAML等): 遵循之前的逻辑（如 5 pairs 或 12 items）。

[Brief]: 从该节点关联的元数据文件中读取 AI 生成的简短描述。如果没有，则留空，显示一个横杠-就行

4. 节点分发逻辑 (The Dispatcher)
实现一个 NodeFactory，根据文件后缀名自动分发到对应的处理器：

目录（无后缀）: 处理为 RawDataNode。

数据库组件: .db, .table, .col, .view, .fk, .rel, .flow。

文档组件: .md, .txt, .pdf 映射为容器，内部为 .chunk。

序列化组件: .json, .yaml, .xml, .toml, .hcl 映射为虚拟目录，内部递归应用 ROOT 逻辑。

5. 开发要求
解耦渲染与数据获取：ls 命令应调用 VFSNode 接口获取数据，然后使用 Tabulate 或类似的库渲染表格，确保 glob 引擎可以只调用 list_children 而不触发表格渲染。

路径转换：支持将虚拟路径转换为 .pontis 影子目录中的物理 YAML/二进制文件路径。

分页保护：在 list_children 中强制实现 offset 和 limit 参数。

复用空间：代码结构应允许 grep 模块通过递归调用 list_children 并执行 get_content().contains(pattern) 来轻松实现。

示例输入/输出参考：
输入: ls .pontis/dev.db/
输出:

Plaintext
[HasSub] | [Name]           | [Info]             | [Brief]
---------|------------------|--------------------|----------------------------
[+]      | users.table      | 10,000 rows, 8 cols| 用户核心基本信息表
[+]      | orders.table     | 50,000 rows, 4 cols| 2025年度所有交易流水记录
[ ]      | active_v.view    | 2 sources          | 活跃用户筛选视图
[ ]      | user_fk.fk       | users -> auth      | 物理外键关联
请先生成核心 BaseNode 类和 NodeFactory 的代码结构。



这是一份为您准备的中文提示词（Prompt），专门针对 **Claude Code** 或 **Cursor** 等 AI 编程助手设计。它详细描述了 Pontis VFS 中针对 JSON 的 `ls` 逻辑，并为后续的全局搜索（Grep）和模式匹配（Glob）打下了架构基础。

---

# Prompt: Pontis VFS - 序列化文件（JSON）`ls` 处理器实现

## 1. 背景与角色
你是一名资深后端架构师。我们正在开发 **Pontis VFS**，这是一个将复杂结构化数据（数据库、JSON、CSV 等）抽象为“可导航树状虚拟文件系统”的项目，旨在让 LLM Agent 使用标准的文件操作指令探索数据。

你的任务是实现**针对序列化文件（以 JSON 为核心）的 `ls` 处理逻辑**。

## 2. 逻辑模型：容器-根节点模式 (Container-Root Model)
为了保证逻辑统一，当 Agent 进入一个 JSON 文件时，必须采用以下映射模型：
- **入口点**：进入 `data.json/` 目录后，第一个看到的节点永远是虚拟根节点 `ROOT.[TYPE]`（例如 `ROOT.DICT` 或 `ROOT.ARRAY`）。
- **层级导航**：所有实际数据都挂载在 `ROOT` 之下。例如，`data.json` 里的键 `user` 的虚拟路径为 `data.json/ROOT.DICT/user.STR`。
- **设计意图**：这解决了标量 JSON（如整个文件只有一个字符串）的对齐问题，并物理隔离了“文件元数据”与“数据内容”。

## 3. `ls` 输出规范
当在 JSON 容器内部调用 `ls` 时，输出必须是一个格式化表格，包含以下字段：
`[HasSub] | [Key].[TYPE] | [Info]`

### 格式化规则：
- **[HasSub]**：如果值是 `DICT` 或 `LIST`，显示 `[+]`；如果是标量（STR, INT, BOOL, NULL），显示 `[ ]`。
- **[Key].[TYPE]**：
    - **TYPE**：必须显式带上 JSON 原始类型后缀（`DICT`, `LIST`, `STR`, `INT`, `BOOL`, `NULL`）。
- **[Info]**：
    - **DICT**：显示键值对数量（如 `5 pairs`）。
    - **LIST**：显示元素数量（如 `120 items`）。
    - **SCALAR (STR/INT/BOOL)**：直接显示其真实值。如果字符串超过 20 个字符，进行截断并保留前缀（如 `"This is a long des..."`）, 可以显示其总长度
    - **NULL**：直接显示 `null`。

## 4. 功能需求
### A. 路径解析器 (Path Resolver)
- 实现一个递归解析器，能够将 VFS 虚拟路径（如 `/api.json/ROOT.DICT/users.LIST/0.DICT/name.STR`）准确映射回内存中的 JSON 节点。
- 支持 `..`（返回上级）和 `.`（当前目录）的导航逻辑。

### B. 为 Grep 和 Glob 做准备（核心）
- **迭代器模式**：`ls` 处理器不应只返回字符串，而应返回一个“虚拟节点对象”的迭代器。每个对象需包含 `name`, `type`, `is_directory`, `get_content()` 等方法。
- **分页支持**：针对 `LIST` 类型，必须支持 `--offset` 和 `--limit` 参数（默认 limit 100），防止大型数组撑爆上下文。
- **懒加载/流式处理**：考虑到大文件（如 100MB+ 的 JSON），解析逻辑应尽量避免全量加载，支持只解析当前 `ls` 路径所需的层级。

### C. 边界情况
- **空容器**：显示 `0 pairs` 或 `0 items`。
- **特殊字符**：确保 Key 中的斜杠 `/` 或反斜杠 `\` 在显示时被编码，在 `cd` 进入时能被还原寻址。

## 5. 任务目标
请生成 `ls_serialized_handler` 模块的代码。该模块应与物理文件读取解耦，接受一个 JSON 对象（或懒加载访问器）并返回符合上述规范的结构化表格输出。

---

### 给 Claude 的执行建议（开发提示）：
1. **类型安全性**：建议在内部定义一套 Enum 来管理 `DICT`, `LIST`, `STR` 等类型。
2. **解耦设计**：将“数据提取”与“表格渲染”分开。这样后续 `glob` 模块可以直接调用“数据提取”部分，而不需要处理表格字符串。
3. **性能**：如果可能，利用类似 `ijson` (Python) 或流式 JSON 解析器来处理大型文件。