# Claude Code Prompt 组装与 Cache 机制调研

本文整理 Claude Code 源码中与 prompt 组装、tools/schema 注入、环境与工作区上下文注入、以及 prompt caching / KV cache 友好设计相关的实现。

目标回答 5 个核心问题：

1. system prompt 是在哪构造的？
2. tool schema 是走原生 `tools` 还是拼进 prompt？
3. repo / workspace 信息是静态前缀还是每轮动态拼接？
4. 每轮请求时，哪些 message 会重新生成？
5. 有没有显式为 prefix/KV cache 做稳定化设计？

## 结论摘要

- Claude Code 不是把所有上下文一次性拼成一个大字符串后塞给模型，而是明确分层成 `system`、`messages`、`tools`。
- `system prompt` 本身也不是单块文本，而是先构造成 `string[] sections`，再在 API 层切成带 `cache_control` 的 `system` text blocks。
- tool schema 走原生 API `tools` 字段，不走 system prompt 文本。
- 环境、项目、工作区相关信息被拆成不同层：
  - `systemContext` 追加到 system prompt 尾部
  - `userContext` 以前置 meta user message 的方式注入
  - 文件片段、工具结果、运行时检索内容留在 `messages`
- 源码里有大量显式 cache-friendly 设计，不是偶然形成的结构。

## 1. Prompt 组装总入口

请求主循环在 [src/query.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/query.ts:213)。

`query()` 的输入已经是分层结构：

- `messages`
- `systemPrompt`
- `userContext`
- `systemContext`
- `toolUseContext`

对应定义见 [QueryParams](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/query.ts:181)。

这说明 Claude Code 在进入 query loop 前，就已经把“system prompt / messages / tools / context”分层准备好了，而不是在一个函数里直接拼一个最终大字符串。

真正发请求时，`query.ts` 调用：

- `prependUserContext(messagesForQuery, userContext)`
- `appendSystemContext(systemPrompt, systemContext)`
- `deps.callModel({ messages, systemPrompt, tools, ... })`

见 [src/query.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/query.ts:660)。

因此最终请求形态是：

- `system`: 独立字段
- `messages`: 独立字段
- `tools`: 独立字段

不是“大 system prompt + 用户消息附在里面”的设计。

## 2. System Prompt 是怎么构造的

默认 system prompt 的主入口在 [src/constants/prompts.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/constants/prompts.ts:444) 的 `getSystemPrompt()`。

它返回的是 `Promise<string[]>`，而不是单个字符串。

### 2.1 分段构造

`getSystemPrompt()` 会先准备一组动态 section：

- `session_guidance`
- `memory`
- `ant_model_override`
- `env_info_simple`
- `language`
- `output_style`
- `mcp_instructions`
- `scratchpad`
- `frc`
- `summarize_tool_results`
- `token_budget`
- `brief`

见 [src/constants/prompts.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/constants/prompts.ts:467)。

然后再与一组静态 section 合并：

- `getSimpleIntroSection()`
- `getSimpleSystemSection()`
- `getSimpleDoingTasksSection()`
- `getActionsSection()`
- `getUsingYourToolsSection()`
- `getSimpleToneAndStyleSection()`
- `getOutputEfficiencySection()`

见 [src/constants/prompts.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/constants/prompts.ts:561)。

### 2.2 不是每轮都重新变

这些动态 section 并不等于“每轮都变”。大部分 section 通过 [systemPromptSection()](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/constants/systemPromptSections.ts:17) 做 memoize，直到 `/clear` 或 `/compact` 才清掉缓存。

只有少数明确会波动、且源码作者承认会破坏 cache 的 section 才用 [DANGEROUS_uncachedSystemPromptSection()](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/constants/systemPromptSections.ts:32)。

最典型的是 `mcp_instructions`，原因注释直接写明：

- MCP server 可能在 turn 之间 connect/disconnect
- 这会 bust prompt cache

见 [src/constants/prompts.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/constants/prompts.ts:513)。

## 3. 静态 / 动态边界

这是 Claude Code 在 cache 设计上最关键的一层。

### 3.1 显式边界 marker

源码定义了 [SYSTEM_PROMPT_DYNAMIC_BOUNDARY](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/constants/prompts.ts:114)：

- marker 之前：跨组织可缓存的静态内容
- marker 之后：用户或会话相关的动态内容

注释里明确写了：

- 前半部分可以用 `scope: 'global'`
- 后半部分不应该缓存

### 3.2 API 层按边界切块

[splitSysPromptPrefix()](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/api.ts:321) 会把 system prompt sections 切成多个 `SystemPromptBlock`：

- `cacheScope: 'global'`
- `cacheScope: 'org'`
- `cacheScope: null`

然后 [buildSystemPromptBlocks()](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/services/api/claude.ts:3213) 再把这些块转成 Anthropic API 的 `system` text blocks，并按块设置 `cache_control`。

这不是“逻辑上区分静态和动态”，而是“请求字节级结构上显式区分静态和动态”。

## 4. Tool Schema 怎么注入

### 4.1 走原生 `tools` 字段

tool schema 的转换入口是 [toolToAPISchema()](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/api.ts:104)。

它把每个 tool 转成 API schema：

- `name`
- `description`
- `input_schema`
- 可选 `strict`
- 可选 `defer_loading`
- 可选 `cache_control`

最终请求里通过 `tools: allTools` 发给模型，见 [src/services/api/claude.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/services/api/claude.ts:1701)。

因此：

- 工具说明不混进 system prompt
- 工具 schema 与用户问题是分层的
- 高变化工具集合不会污染 system prompt 文本本身

### 4.2 tool schema 做了 session-stable cache

`toolToAPISchema()` 内部有明确注释：

- base schema 按 session 缓存
- 避免 mid-session 的 feature flag 变化或 `tool.prompt()` 漂移导致工具数组字节变化

见 [src/utils/api.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/api.ts:126)。

### 4.3 工具顺序显式稳定化

[mergeAndFilterTools()](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/toolPool.ts:51) 里有专门注释：

- built-ins 必须保持连续前缀
- 内置工具和 MCP 工具分别排序
- 为 prompt-cache stability 服务

这说明“工具顺序稳定”是明确的设计要求，不是 incidental behavior。

## 5. Repo / Workspace / 环境信息怎么进 Prompt

Claude Code 没把所有项目上下文都塞到 system prompt 里，而是分成了两层：

- `systemContext`
- `userContext`

定义在 [src/context.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/context.ts:1)。

### 5.1 `systemContext`

`getSystemContext()` 里主要包含：

- `gitStatus`
- 可选 `cacheBreaker`

见 [src/context.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/context.ts:100)。

这些内容通过 [appendSystemContext()](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/api.ts:437) 追加到 `systemPrompt` 后面。

### 5.2 `userContext`

`getUserContext()` 里主要包含：

- `claudeMd`
- `currentDate`

见 [src/context.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/context.ts:143)。

这些内容不是直接混入用户消息正文，而是通过 [prependUserContext()](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/api.ts:449) 包装成一个前置的 meta user message：

- `<system-reminder>`
- `isMeta: true`

这使得：

- 项目约束和用户问题分层
- 用户原始问题文本本身不被污染
- 运行时上下文仍然能在 `messages` 层追加，而不必回写 system prompt 主体

### 5.3 这些信息也做了 conversation-level 缓存

`getSystemContext()` 与 `getUserContext()` 都用了 `memoize()`，源码注释直接写了：

- “cached for the duration of the conversation”

见：

- [getSystemContext() 注释](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/context.ts:100)
- [getUserContext() 注释](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/context.ts:143)

所以虽然每轮 query 前会调用它们，但会话内大多数情况下返回稳定内容。

## 6. 每轮请求时，哪些内容会重新生成

交互式主线程在发起 query 前，确实会重新取：

- `getSystemPrompt(...)`
- `getUserContext()`
- `getSystemContext()`

见 [src/screens/REPL.tsx](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/screens/REPL.tsx:2535)。

但要区分“函数再次调用”和“prompt 字节变化”。

### 6.1 每轮一定变化的

- 当前用户消息
- 最近 assistant/tool_result 消息
- `messagesForQuery` 在 compact / snip / microcompact / collapse 后的视图

见 [src/query.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/query.ts:365)。

### 6.2 每轮调用、但通常不变的

- 大部分 system prompt sections
- `claudeMd`
- `gitStatus` 快照
- `currentDate` 在同一天内不变

### 6.3 少数可能变化、且会破坏 cache 的

- MCP instructions
- 某些 feature-gated 动态 section
- 特意用于 debug/实验的 cache breaker

## 7. 请求发送前还会如何改写消息

真正送往模型前，`messagesForQuery` 会经过一系列处理：

- `applyToolResultBudget()`
- `snipCompactIfNeeded()`
- `microcompact()`
- `applyCollapsesIfNeeded()`
- `autocompact()`

见 [src/query.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/query.ts:379)。

这说明 Claude Code 维护的是：

- 内部完整会话状态 `state.messages`
- 每轮送模视图 `messagesForQuery`

也就是说，它在“持久会话历史”和“本轮请求视图”之间还有一层投影。

这层投影设计也有利于 prefix 稳定，因为可以优先只处理尾部和超预算部分，而不去重写整段 system prompt。

## 8. 明确为 Prompt Cache / KV Cache 做的设计

这部分在源码里非常明显。

### 8.1 system prompt static/dynamic boundary

前面已经提到：

- 明确 boundary marker
- 明确 `global/org/null` 三种 cache scope

见：

- [src/constants/prompts.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/constants/prompts.ts:114)
- [src/utils/api.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/api.ts:321)

### 8.2 stable tool ordering

工具排序显式稳定化，见 [src/utils/toolPool.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/toolPool.ts:63)。

### 8.3 session-stable tool schema bytes

tool schema 基础部分做缓存，见 [src/utils/api.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/api.ts:126)。

### 8.4 beta header latch

在 API 请求层，多个 beta header 被做成 sticky-on latch，避免 mid-session 开关变化导致 cache key 改变。

见 [src/services/api/claude.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/services/api/claude.ts:1412)。

### 8.5 单一 message-level cache breakpoint

[addCacheBreakpoints()](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/services/api/claude.ts:3063) 的注释明确解释了为什么每个请求只放一个 message-level `cache_control` marker，以及 `skipCacheWrite` 如何避免 fork 的尾部污染 KV cache。

### 8.6 fork/subagent 共享父请求 cache-safe prefix

[src/utils/forkedAgent.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/forkedAgent.ts:48) 里直接定义了 `CacheSafeParams`，注释写明 Anthropic API cache key 由这些部分组成：

- system prompt
- tools
- model
- messages prefix
- thinking config

fork 子代理必须尽量保持这些参数与父请求一致，以复用 prompt cache。

### 8.7 避免旧消息内容在后续 turn 被改写

`toolResultStorage` 相关逻辑有大量注释强调：

- 某个 tool result 一旦决定被替换/压缩，后续必须保持同样决策
- 否则会破坏已缓存 prefix

相关入口见 [applyToolResultBudget()](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/query.ts:379)。

## 9. 对 5 个优先问题的直接回答

### 9.1 system prompt 是在哪构造的？

默认入口是 [getSystemPrompt()](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/constants/prompts.ts:444)，会话实际生效版本再由 [buildEffectiveSystemPrompt()](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/systemPrompt.ts:41) 叠加 agent/custom/append prompt。

### 9.2 tool schema 是走原生 tools 还是拼进 prompt？

走原生 `tools` 字段。转换逻辑在 [toolToAPISchema()](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/utils/api.ts:104)，最终随 API 请求发送，见 [src/services/api/claude.ts](/nfsdat2/home/bcchenslm/Projects/claude-code-source-code/src/services/api/claude.ts:1701)。

### 9.3 repo / workspace 信息是静态前缀还是每轮动态拼接？

两者都有，但被刻意分层：

- 会话级稳定信息尽量 memoize
- `claudeMd` 进入 `userContext`
- `gitStatus` 进入 `systemContext`
- 运行时检索和工具结果保留在 `messages`

不是“全部扔进 system prompt 前缀”。

### 9.4 每轮请求时，哪些 message 会重新生成？

每轮都会重新生成的是 `messagesForQuery` 这个送模视图；当前用户消息、最新 tool_result、compact 后摘要等都会更新。system/user/system context 虽然也会重新取，但大部分内容在会话内是稳定缓存的。

### 9.5 有没有显式为 prefix/KV cache 做稳定化设计？

有，而且非常明确。包括：

- system prompt dynamic boundary
- `cacheScope` 分层
- stable tool ordering
- session-stable tool schema cache
- sticky beta headers
- fork cache-safe params
- 单一 cache breakpoint
- 历史 tool result 替换决策冻结

## 10. Claude Code 与“待改实现”的对照模板

| 模块 | Claude Code 做法 | 常见反模式 | 是否影响 KV cache | 建议改法 |
|---|---|---|---|---|
| system prompt | `string[]` 分段构造，静态/动态边界显式 | 每轮重拼整块大字符串 | 高 | 拆分成稳定前缀和动态尾部 |
| tools schema | 走原生 `tools` 字段 | 工具说明直接拼进 system prompt | 高 | 改成原生 tools |
| workspace context | `systemContext` / `userContext` 分层 | 所有环境信息塞进 system prompt 正文 | 中到高 | 按层拆开，减少 system 主体波动 |
| repo summary / CLAUDE.md | 会话级缓存，作为 user meta context | 每轮重新注入 README/项目说明全文 | 高 | 固定为 session prefix 或独立 context message |
| runtime retrieval | 作为后部 `messages` / `tool_result` | 临时检索结果混进 system prompt | 高 | 只追加到消息尾部 |
| reflection / subagent | fork 继承 cache-safe params | 每次 reflection 重建全套 prompt | 高 | 复用父请求前缀，避免重建 |

## 11. 对你当前实现的直接启发

如果你的目标是提升 prompt caching / KV cache 命中率，最优先检查这几件事：

1. tool schema 是否仍在 prompt 文本里，而不是原生 `tools`
2. 是否缺少一个显式的 static/dynamic boundary
3. README / ontology / 项目说明是否被每轮重写
4. reflection / planner / verifier 是否每次都重建整块 prompt
5. 动态上下文是否被插在 system prompt 前半段，而不是附在后部 messages

如果你要对照 Claude Code 重构，一条最核心原则就是：

> 让高变化内容尽量只出现在请求尾部，让 system prompt、tool schema、环境与项目级说明尽量保持稳定字节序列。

