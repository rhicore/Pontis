# Claude Code 项目理解与工具结果折叠机制调研

## 结论摘要

Claude Code 帮模型快速理解项目，不是靠一次性构建完整项目知识图谱，而是靠一组工程化上下文机制：

- 启动时注入项目规则、日期、工作目录、git 快照等基础上下文。
- 模型触达具体文件时，按路径动态注入局部 `CLAUDE.md` / rules。
- 对宽泛代码库探索，提供搜索工具、LSP 工具、Explore 子 Agent。
- 对长会话，使用 session memory、microcompact、auto compact、context collapse 保持上下文可控。
- 对大工具输出，先落盘，再把路径和短预览放进上下文。
- 对用户界面，把连续 Read/Search/Bash 探索折叠成摘要行，减少视觉噪音。

整体思路是：**规则先行、按需检索、局部上下文注入、长历史压缩、大输出落盘**。

## 1. 项目理解机制

### 1.1 CLAUDE.md / rules 项目指令

`CLAUDE.md` 是 Claude Code 最重要的项目规则入口。加载顺序包括：

1. Managed memory，例如 `/etc/claude-code/CLAUDE.md`
2. User memory，例如 `~/.claude/CLAUDE.md`
3. Project memory，例如项目内 `CLAUDE.md`、`.claude/CLAUDE.md`、`.claude/rules/*.md`
4. Local memory，例如 `CLAUDE.local.md`

源码注释明确说明：文件按“低优先级到高优先级”加载，越靠后的内容模型越会重视；项目和本地文件通过从当前目录向上遍历发现，越靠近当前目录优先级越高。见 [src/utils/claudemd.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/claudemd.ts:1)。

`CLAUDE.md` 还支持 `@include`，可以在 Markdown 文本节点里通过 `@path`、`@./path`、`@~/path`、`@/absolute/path` 引入其他文本文件。系统会防止循环引用，并忽略不存在的 include。

### 1.2 User Context 与 System Context

每次会话会构造两类上下文：

- `getUserContext()`：注入 `CLAUDE.md` 内容和当前日期。
- `getSystemContext()`：注入 git 状态快照和少量系统级调试注入。

`getUserContext()` 会调用 `getMemoryFiles()` 和 `getClaudeMds()`，把发现到的指令文件合并成 `claudeMd` 上下文；同时注入 `currentDate`。见 [src/context.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/context.ts:155)。

`getSystemContext()` 会在非远程、且 git 指令未关闭时调用 `getGitStatus()`。git 快照包含：

- 当前分支
- main branch
- git user
- `git status --short`
- 最近 5 个 commit

它是会话开始时的快照，不会随着会话实时更新。见 [src/context.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/context.ts:36)。

### 1.3 环境与工作目录信息

system prompt 会注入当前工作目录、是否 git repo、是否 worktree 等环境信息，帮助模型明确“我在哪个项目里工作”。相关生成逻辑在 [src/constants/prompts.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/constants/prompts.ts:642)。

如果当前目录是 git worktree，prompt 会特别提醒这是隔离副本，命令应在当前 worktree 内运行，不要切回原始仓库。

### 1.4 嵌套 CLAUDE.md 按需注入

Claude Code 不只在启动时加载全局规则。模型或用户触达某个文件时，还会按该文件路径加载相关目录层级的局部规则，作为 `nested_memory` attachment 注入。

典型触发来源包括：

- 用户 `@` 提到某个文件。
- 模型调用 Read 读取某个文件。
- IDE 上下文提供当前打开文件。

`getNestedMemoryAttachments()` 会读取 `ToolUseContext.nestedMemoryAttachmentTriggers` 中的文件路径，然后调用 `getNestedMemoryAttachmentsForFile()` 查找匹配规则。见 [src/utils/attachments.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/attachments.ts:2163)。

这套机制让模型在读 `src/foo/bar.ts` 时，自动获得与 `src/foo/` 或更近目录相关的 `CLAUDE.md` / rules，而不用一开始就把全项目所有局部规则塞进上下文。

### 1.5 IDE 上下文

IDE 集成可用时，Claude Code 会把当前打开文件、选区等作为附件放进上下文。这样模型能更快知道用户关注哪个文件或代码片段，而不是先问“你指的是哪个文件”。

对应逻辑与附件系统集成在 `src/utils/attachments.ts`，例如 opened file 会先触发 nested memory 查询，再生成 `opened_file_in_ide` attachment。

### 1.6 Skill / Agent / MCP 增量附件

Claude Code 不把所有技能、Agent、MCP 指令都永久塞进 system prompt，而是使用 delta attachment 动态告知模型当前可用能力。例如：

- `agent_listing_delta`
- `mcp_instructions_delta`
- `deferred_tools_delta`
- skill discovery 相关附件

这些附件在每轮构造 prompt 附件时生成，减少 prompt cache 被频繁打破，也让模型只看到当前相关的能力变化。

### 1.7 搜索、LSP 与 Explore 子 Agent

模型主动理解项目时，主要依赖这些工具：

- `Read`：读取文件。
- `Grep` / `Glob`：查找文本和文件。
- Bash 搜索命令：例如 `rg`、`find`、`ls`。
- LSP 工具：语义级跳转、hover、definition、reference 等。
- Explore 子 Agent：适合宽泛、多步代码库调研。

system prompt 中明确建议：简单定向搜索直接用搜索工具；宽泛代码库探索或简单搜索不足时，使用 Explore agent。见 [src/constants/prompts.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/constants/prompts.ts:378)。

## 2. 工具结果与上下文折叠机制

Claude Code 中“折叠”不是单一机制，而是三层不同东西：

1. **大工具结果落盘**：减少发给模型的 tool result 内容。
2. **上下文 compact/collapse**：减少历史消息占用的上下文 token。
3. **UI 折叠**：减少终端界面噪音，不一定改变模型上下文。

### 2.1 大工具结果落盘

工具结果会先经过 `toolResultStorage`。如果结果太大，Claude Code 不会把完整输出直接塞回模型上下文，而是：

1. 把完整结果写入磁盘。
2. 在 tool result 中放一个短预览。
3. 告诉模型完整输出保存在哪个文件。

保存目录是：

```text
~/.claude/projects/<project>/<sessionId>/tool-results/
```

文件名来自 `tool_use_id`，扩展名是 `.txt` 或 `.json`。路径逻辑见 [src/utils/toolResultStorage.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/toolResultStorage.ts:95)。

替代消息形态大致是：

```xml
<persisted-output>
Output too large (...). Full output saved to: /path/to/tool-results/<id>.txt

Preview (first 2KB):
...
</persisted-output>
```

生成逻辑在 `buildLargeToolResultMessage()`，见 [src/utils/toolResultStorage.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/toolResultStorage.ts:187)。

这样做的关键好处是：**模型不会丢失完整结果的位置，但上下文里只承载小预览**。如果模型需要完整输出，可以再用 Read 工具读取落盘文件。

### 2.2 单个结果过大时的持久化

`maybePersistLargeToolResult()` 会检查 tool result 内容大小：

- 空结果会替换成简短完成提示，避免模型看到空 tool_result 后异常停顿。
- 图片内容不会落盘替换，必须原样发送。
- 文本结果超过阈值时，调用 `persistToolResult()` 写入文件，再用 `<persisted-output>` 消息替换。

相关逻辑见 [src/utils/toolResultStorage.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/toolResultStorage.ts:268)。

阈值由工具自身的 `maxResultSizeChars` 和全局默认值共同决定，也可被 GrowthBook 配置覆盖。

### 2.3 多个工具结果合计过大时的预算控制

并行工具调用可能出现一种情况：每个结果单独不算太大，但多个 tool_result 合并成一个 API user message 后总量过大。

为此有 `enforceToolResultBudget()`：

- 按“API 层会合并成同一条 user message”的边界收集 tool_result。
- 如果这一组结果总大小超过预算，就挑最大的 fresh result 落盘替换。
- 已经处理过的 `tool_use_id` 会被冻结，后续 turn 不改变决策。
- 已替换过的结果会从内存 Map 里复用完全相同的替换文本，保证 prompt cache 稳定。

核心说明见 [src/utils/toolResultStorage.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/toolResultStorage.ts:740)。

这一步在 query loop 每次发请求前执行，且位于 microcompact 之前。见 [src/query.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/query.ts:367)。

### 2.4 Query 前的上下文处理流水线

每次准备请求模型前，`query.ts` 会把当前消息复制成 `messagesForQuery`，然后依次处理：

```text
messagesForQuery
  -> applyToolResultBudget
  -> snipCompactIfNeeded
  -> microcompact
  -> contextCollapse.applyCollapsesIfNeeded
  -> autoCompact
  -> 发给模型
```

对应代码位于 [src/query.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/query.ts:367)。

各层作用不同：

- **applyToolResultBudget**：把大 tool_result 或合计过大的 tool_result 替换成预览和文件路径。
- **snipCompact**：裁剪历史中可删除的片段。
- **microcompact**：细粒度压缩旧 tool result 或缓存相关内容。
- **contextCollapse**：把部分历史投影成 collapsed view。
- **autoCompact**：上下文接近阈值时，用 compact agent 生成摘要并替换旧消息窗口。

### 2.5 Context Collapse

`contextCollapse` 的注释强调：它是 read-time projection。也就是说，REPL 的完整历史仍保留，但发给模型的是经过 collapse 投影后的 view；summary 存在 collapse store 里，而不是直接替换 REPL 数组。

这让系统能在不破坏 UI 历史的情况下，减少发给模型的上下文。见 [src/query.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/query.ts:428)。

### 2.6 Auto Compact

`autoCompact` 在上下文达到阈值时运行。成功后会构造 `postCompactMessages`：

- 旧历史被 compact summary 替代。
- 尾部必要消息被保留。
- 产生 compact boundary。

query loop 随后用 `postCompactMessages` 继续当前请求。见 [src/query.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/query.ts:453)。

如果启用了 Session Memory compact，系统还可能用 session memory 文件作为摘要来源，减少传统 compact 的信息损失。

## 3. UI 层 Read/Search 折叠

除了真正影响模型上下文的压缩，还有一套 UI 折叠：`collapseReadSearchGroups()`。

它把连续的 Read、Grep、Glob、搜索型 Bash、目录 listing、memory read/write 等操作合并成一条 `collapsed_read_search` 消息。规则包括：

- 连续 search/read tool use 合并。
- 对应 tool result 归入同一个组。
- assistant 正文会打断分组。
- 普通非 collapsible 工具，例如编辑业务文件，会打断分组。
- verbose 模式下仍可展开查看每个工具调用。

实现见 [src/utils/collapseReadSearch.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/collapseReadSearch.ts:755)。

展示组件是 `CollapsedReadSearchContent`，它会显示类似：

```text
Read 3 files
Searched 5 times
Ran 2 bash commands
Recalled 1 memory
```

并维护最新 hint，例如最近读取的文件路径或搜索 pattern。见 [src/components/messages/CollapsedReadSearchContent.tsx](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/components/messages/CollapsedReadSearchContent.tsx:1)。

这主要是终端界面降噪，不等同于 autoCompact。它让用户看到“做了哪些探索”，而不是被几十条 Read/Grep 输出刷屏。

## 4. 三类折叠的区别

| 机制 | 影响模型上下文 | 影响 UI | 是否保留完整内容 | 主要目的 |
| --- | --- | --- | --- | --- |
| 大工具结果落盘 | 是 | 间接影响 | 是，保存在 tool-results 文件 | 避免大输出撑爆上下文 |
| applyToolResultBudget | 是 | 间接影响 | 是，选中的结果落盘 | 控制并行工具结果合计大小 |
| microcompact / autoCompact | 是 | 会产生 compact boundary | 旧细节被摘要替代 | 长会话续航 |
| contextCollapse | 是 | REPL 历史仍保留 | collapse store 保存摘要/投影状态 | 保留粒度同时降低上下文 |
| collapseReadSearchGroups | 通常不是主要上下文压缩 | 是 | 是，原消息在 group 内可展开 | 终端展示降噪 |

## 5. 整体流程图

```text
工具执行完成
  -> processToolResultBlock()
      -> 空结果补提示
      -> 大文本结果写入 tool-results 文件
      -> 上下文只放 persisted-output + preview

每次请求模型前
  -> applyToolResultBudget()
      -> 并行 tool_result 合计超预算则继续落盘替换
  -> snipCompactIfNeeded()
  -> microcompact()
  -> contextCollapse.applyCollapsesIfNeeded()
  -> autoCompact()
  -> 发送最终 messagesForQuery

终端展示时
  -> collapseReadSearchGroups()
      -> 连续 Read/Grep/Bash 搜索折成 collapsed_read_search
      -> verbose 模式可展开
```

## 6. 设计取舍

### 6.1 不直接丢弃大输出

大工具结果不是简单截断，而是落盘并提供预览。这样模型和用户都能追溯完整内容，只是默认不把完整内容放进上下文。

### 6.2 replacement 决策被冻结

`enforceToolResultBudget()` 会按 `tool_use_id` 记录 seen/replacement 状态。某个结果一旦以完整内容或预览形式被模型见过，后续不会随便改变。这是为了保持 prompt cache prefix 稳定。

### 6.3 UI 折叠和上下文压缩分离

UI 折叠负责可读性，compact 负责 token 预算。两者分开，避免“为了界面好看而改变模型看到的事实”。

### 6.4 多层压缩按风险递进

Claude Code 先做低风险的 tool result budget 和 microcompact，再做 contextCollapse，最后才 autoCompact 成摘要。这样尽量保留可用粒度，只有上下文压力足够大时才进入更强压缩。
