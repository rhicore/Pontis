# Claude Code Agent 记忆系统调研报告

## 结论摘要

Claude Code 的记忆系统不是一个向量库或单一数据库，而是一套以 Markdown 文件为核心的分层持久化机制。它把“长期可复用事实”和“当前会话连续性”分开处理：

- **Auto Memory / Memdir**：主 Agent 的长期项目记忆，默认存放在 `~/.claude/projects/<sanitized-project-root>/memory/`。
- **Agent Memory**：自定义子 Agent 的专属长期记忆，可按 `user`、`project`、`local` 三种作用域保存。
- **Relevant Memory Prefetch**：每轮用户输入后，用轻量模型从记忆文件清单里挑最多 5 条相关记忆，以附件方式注入上下文。
- **Extract Memories**：对话结束点后台 fork 一个记忆抽取 Agent，把新学到的长期信息写入 Auto Memory。
- **Session Memory**：独立于长期记忆的“当前会话笔记”，用于长会话压缩和恢复上下文。
- **Team Memory**：在团队功能打开时，把项目记忆拆为 private/team 两套目录，并通过服务端 API 同步 team 目录。

整体设计偏工程化：用文件系统做持久层、用 `MEMORY.md` 做索引、用 frontmatter 做可检索元数据、用 prompt 约束写入规范、用后台 forked agent 把抽取成本从主对话中移出。

## 1. 存储模型

### 1.1 Auto Memory 目录

Auto Memory 是否启用由 `isAutoMemoryEnabled()` 决定，优先级是环境变量、`--bare`、远程持久存储条件、settings，最后默认开启。代码明确说明该开关覆盖 memdir、agent memory 和过去会话搜索等能力，见 [src/memdir/paths.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/memdir/paths.ts:21)。

目录解析逻辑在 `getAutoMemPath()`：

- 可通过 `CLAUDE_COWORK_MEMORY_PATH_OVERRIDE` 全路径覆盖。
- 可通过可信 settings 的 `autoMemoryDirectory` 覆盖。
- 默认路径是 `<memoryBase>/projects/<sanitized-git-root>/memory/`，其中 `memoryBase` 通常是 `~/.claude`，远程模式可用 `CLAUDE_CODE_REMOTE_MEMORY_DIR` 覆盖，见 [src/memdir/paths.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/memdir/paths.ts:79) 和 [src/memdir/paths.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/memdir/paths.ts:207)。

### 1.2 文件形态

记忆目录中有两类文件：

- `MEMORY.md`：入口索引，不保存完整记忆，只保存到 topic 文件的一行链接。
- 其他 `*.md`：真正的记忆文件，带 frontmatter，例如 `name`、`description`、`type`。

`MEMORY.md` 有硬限制：最多 200 行、25KB。超限会截断并插入警告，见 [src/memdir/memdir.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/memdir/memdir.ts:34) 和 [src/memdir/memdir.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/memdir/memdir.ts:57)。

### 1.3 记忆类型

系统把长期记忆限定为四类：

- `user`：用户角色、目标、知识背景、协作偏好。
- `feedback`：用户对 Claude 工作方式的纠正或确认。
- `project`：不可从代码/git 推导出的项目背景、目标、事件、决策。
- `reference`：外部系统入口，例如 Linear、Slack、Grafana。

提示词同时明确“不该保存”的内容：代码结构、架构、git 历史、已有 CLAUDE.md 内容、临时任务状态等。生成规则在 `buildMemoryLines()` 中拼装，见 [src/memdir/memdir.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/memdir/memdir.ts:187)。

## 2. 主 Agent 记忆如何注入

系统 prompt 构建时会调用 `loadMemoryPrompt()`，把记忆机制说明注入 Claude Code 的系统提示中。Auto Memory 开启时，它会确保目录存在，并返回一段“如何保存、何时读取、如何信任记忆”的指导文本，见 [src/memdir/memdir.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/memdir/memdir.ts:419)。

注意：主系统提示里默认主要注入“记忆操作说明”，而完整 topic 记忆不是全部常驻上下文。`MEMORY.md` 作为索引会被加载或用于提示，真正的相关 topic 文件靠后续召回机制按需注入。

## 3. 记忆写入链路

### 3.1 主 Agent 直接写入

主 Agent 在系统 prompt 中被告知：用户明确要求 remember 时立即保存；保存时先写 topic 文件，再在 `MEMORY.md` 添加索引行。保存格式和去重要求都由 `buildMemoryLines()` 生成，见 [src/memdir/memdir.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/memdir/memdir.ts:199)。

### 3.2 后台 Extract Memories

如果主 Agent 没有主动写记忆，`extractMemories` 会在 query loop 完成后后台运行。文件头注释说明它会从当前 session transcript 中抽取长期记忆，写入 Auto Memory，并通过 `runForkedAgent` 复用父对话的 prompt cache，见 [src/services/extractMemories/extractMemories.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/services/extractMemories/extractMemories.ts:1)。

执行时机和保护逻辑：

- 只在主 Agent 上运行，子 Agent 不触发，见 [src/services/extractMemories/extractMemories.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/services/extractMemories/extractMemories.ts:527)。
- 如果检测到主对话已经写过记忆文件，则跳过后台抽取，避免重复，见 [src/services/extractMemories/extractMemories.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/services/extractMemories/extractMemories.ts:345)。
- 抽取前扫描已有记忆清单，减少重复写入，见 [src/services/extractMemories/extractMemories.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/services/extractMemories/extractMemories.ts:395)。
- 抽取 Agent 最多 5 轮，避免陷入验证或调查，见 [src/services/extractMemories/extractMemories.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/services/extractMemories/extractMemories.ts:415)。

权限上，抽取 Agent 只能读文件、grep/glob、执行只读 shell，以及在 memory 目录内 Write/Edit。这降低了后台 Agent 修改业务代码或执行危险命令的风险。

## 4. 记忆召回链路

Claude Code 使用两级召回：

### 4.1 扫描索引与 frontmatter

`scanMemoryFiles()` 会递归扫描记忆目录下的 Markdown 文件，排除 `MEMORY.md`，只读取前 30 行 frontmatter，提取 filename、description、type、mtime，并最多保留 200 个文件。这让召回成本与“记忆头信息”相关，而不是全文相关，见 [src/memdir/memoryScan.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/memdir/memoryScan.ts:27)。

### 4.2 轻量模型选择相关记忆

`findRelevantMemories()` 会把用户 query 和记忆 manifest 发给 Sonnet，让它最多选 5 个“明确有用”的文件。它会过滤已经 surfaced 的记忆，避免重复占用上下文，见 [src/memdir/findRelevantMemories.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/memdir/findRelevantMemories.ts:18) 和 [src/memdir/findRelevantMemories.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/memdir/findRelevantMemories.ts:39)。

### 4.3 非阻塞预取

`startRelevantMemoryPrefetch()` 每轮用户输入启动一次异步预取。注释明确说它与主模型 streaming 和工具执行并行，收集时如果还没完成就跳过，避免阻塞主 turn，见 [src/utils/attachments.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/attachments.ts:2334)。

### 4.4 注入为附件

选中的记忆文件会被读取并作为 `relevant_memories` attachment 注入。读取时按行数和字节数截断，截断时提示可用 Read 工具查看完整文件，见 [src/utils/attachments.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/attachments.ts:2285)。

如果用户 `@agent-xxx` 提到某个有 memory 的 Agent，召回范围会切换到该 Agent 的记忆目录；否则默认查 Auto Memory，见 [src/utils/attachments.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/attachments.ts:2196)。

## 5. 子 Agent 专属记忆

Agent Memory 是 Claude Code 子 Agent 的专属长期记忆。类型定义为：

- `user`：`<memoryBase>/agent-memory/<agentType>/`
- `project`：`<cwd>/.claude/agent-memory/<agentType>/`
- `local`：本地项目 `.claude/agent-memory-local/<agentType>/`；远程持久存储时落到远程 memory mount 下。

具体路径逻辑见 [src/tools/AgentTool/agentMemory.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/tools/AgentTool/agentMemory.ts:12) 和 [src/tools/AgentTool/agentMemory.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/tools/AgentTool/agentMemory.ts:46)。

自定义 agent 的 frontmatter 或 JSON 定义中可配置 `memory: user|project|local`。加载 agent 定义时，如果 Auto Memory 开启且配置了 memory，会把 `loadAgentMemoryPrompt()` 追加到该 Agent 的 system prompt；同时给该 Agent 补充 Read/Write/Edit 工具以便操作记忆文件，见 [src/tools/AgentTool/loadAgentsDir.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/tools/AgentTool/loadAgentsDir.ts:430) 和 [src/tools/AgentTool/loadAgentsDir.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/tools/AgentTool/loadAgentsDir.ts:711)。

`loadAgentMemoryPrompt()` 会按作用域附加额外说明：user 记忆要跨项目通用，project 记忆面向项目并可随版本控制共享，local 记忆面向本地机器且不进 VCS，见 [src/tools/AgentTool/agentMemory.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/tools/AgentTool/agentMemory.ts:138)。

## 6. Session Memory：会话连续性而非长期偏好

Session Memory 是另一套机制，目标不是保存长期偏好，而是维护当前会话的结构化笔记，服务于 compact 和恢复。

默认阈值：

- 首次初始化：上下文达到 10000 token。
- 后续更新：距离上次抽取增长 5000 token。
- 工具调用数：至少 3 次。

配置定义见 [src/services/SessionMemory/sessionMemoryUtils.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/services/SessionMemory/sessionMemoryUtils.ts:15)。

运行方式：

- 只在 `repl_main_thread` 上运行，不在子 Agent、teammate 等上下文运行，见 [src/services/SessionMemory/sessionMemory.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/services/SessionMemory/sessionMemory.ts:275)。
- 通过 post-sampling hook 注册，且只有 auto-compact 开启时才启用，见 [src/services/SessionMemory/sessionMemory.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/services/SessionMemory/sessionMemory.ts:357)。
- 抽取时同样使用 `runForkedAgent`，但权限更窄：只允许 Edit 精确的 session memory 文件，见 [src/services/SessionMemory/sessionMemory.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/services/SessionMemory/sessionMemory.ts:315) 和 [src/services/SessionMemory/sessionMemory.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/services/SessionMemory/sessionMemory.ts:457)。

这套笔记文件可被 `sessionMemoryCompact` 读取，用于替代传统 compact 摘要，减少长会话压缩时的信息损失。

## 7. Team Memory

当 `TEAMMEM` 构建特性和 GrowthBook gate 开启时，记忆目录拆成 private 和 team 两层：

- private：当前用户私有，仍在 Auto Memory 根目录。
- team：共享目录 `<autoMemPath>/team/`。

combined prompt 会明确两种 scope，并把不同类型记忆如何选择 private/team 写进提示词。团队记忆还额外禁止保存敏感数据，见 [src/memdir/teamMemPrompts.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/memdir/teamMemPrompts.ts:1)。

Team Memory 有同步服务：

- 服务端 API 以 GitHub repo slug 为作用域。
- pull 时服务端覆盖本地同 key 文件。
- push 时只上传本地 hash 与服务端 checksum 不同的条目。
- 本地删除不会传播到服务端，下次 pull 会恢复。

这些语义写在同步服务文件头注释中，见 [src/services/teamMemorySync/index.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/services/teamMemorySync/index.ts:1)。watcher 会在启动时先 pull，再 watch team 目录，变更后 debounce push，见 [src/services/teamMemorySync/watcher.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/services/teamMemorySync/watcher.ts:1)。

## 8. 设计特点与取舍

### 8.1 文件系统优先

记忆被设计为用户可见、可编辑、可版本化的 Markdown 文件，而不是隐藏数据库。这带来几个好处：

- 易调试：用户和 Agent 都能 Read/Edit。
- 易迁移：目录可被远程 mount 或 settings 覆盖。
- 易控制：通过文件权限和路径 allowlist 限制写入范围。

代价是需要额外处理目录安全、索引膨胀、重复写入、陈旧信息等问题。

### 8.2 索引常驻，正文按需

`MEMORY.md` 只是短索引，topic 文件按需召回。这是典型的 token 控制设计：把“发现入口”放进上下文，把“详细内容”延迟到确有需要时再注入。

### 8.3 抽取和召回都不阻塞主流程

召回使用 prefetch，后台抽取使用 forked agent，并且有 turn 数上限、工具权限上限、coalescing 逻辑。这说明系统目标是“不让记忆机制破坏主 Agent 响应路径”。

### 8.4 记忆不是事实源

prompt 中要求对记忆保持怀疑：记忆可能过期，使用前要和当前文件或外部资源验证；如果记忆与当前观察冲突，信当前观察，并更新或移除陈旧记忆。这个策略降低了长期记忆污染决策的风险。

### 8.5 子 Agent 记忆隔离

子 Agent 的记忆按 agent type 单独建目录。主用户提到某个 Agent 时，相关记忆召回会优先查该 Agent 目录。这避免了不同专业 Agent 之间偏好和经验互相污染。

## 9. 一句话架构图

```text
用户输入
  -> startRelevantMemoryPrefetch()
      -> scanMemoryFiles(frontmatter)
      -> Sonnet 选最多 5 个相关记忆
      -> relevant_memories attachment 注入
  -> 主 Agent 响应/工具调用
  -> stop hooks
      -> extractMemories forked agent 写 Auto/Team Memory
      -> sessionMemory post-sampling hook 更新当前会话笔记

子 Agent 启动
  -> 读取 agent 定义 memory: user|project|local
  -> loadAgentMemoryPrompt()
  -> 专属 MEMORY.md + topic 文件注入其 system prompt
```

## 10. 可复用经验

如果要借鉴 Claude Code 的 agent memory 设计，核心不是“加一个 memory 表”，而是这几条约束：

- 把长期记忆、会话摘要、团队共享记忆分成不同机制。
- 长期记忆只保存不可由当前代码/git/文档推导的信息。
- 用短索引 + topic 文件控制 token。
- 用 frontmatter 给召回模型提供低成本选择信号。
- 写入通过 prompt 规范和工具权限共同约束。
- 后台抽取必须能跳过、合并、限轮、失败静默，不能影响主任务。
- 对记忆读取要有陈旧性提示，重要判断必须回到当前事实源验证。
