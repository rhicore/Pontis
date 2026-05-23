# Claude Code Fork 机制与主智能体循环详解

> 本文档基于 Claude Code 源码，系统梳理 `fork` 在 Claude Code 中的定位、实现链路、与普通子智能体的区别，以及主智能体循环 `query()` 如何驱动整个机制。

---

## 目录

1. [核心结论](#1-核心结论)
2. [主智能体循环在哪里](#2-主智能体循环在哪里)
3. [fork 的入口：不是单独命令，而是 AgentTool 的一种分流](#3-fork-的入口不是单独命令而是-agenttool-的一种分流)
4. [fork 为什么不是“只是一个工具”](#4-fork-为什么不是只是一个工具)
5. [fork 子上下文是怎么构造的](#5-fork-子上下文是怎么构造的)
6. [fork 的消息前缀与 Prompt Cache 设计](#6-fork-的消息前缀与-prompt-cache-设计)
7. [fork 子智能体如何复用主循环](#7-fork-子智能体如何复用主循环)
8. [后台任务、通知与恢复机制](#8-后台任务通知与恢复机制)
9. [fork 与普通 subagent 的差异](#9-fork-与普通-subagent-的差异)
10. [fork 与 coordinator mode 的关系](#10-fork-与-coordinator-mode-的关系)
11. [横切复用：fork 不是只给 AgentTool 用的](#11-横切复用fork-不是只给-agenttool-用的)
12. [调用链总结](#12-调用链总结)

---

## 1. 核心结论

先给出结论：

1. `fork` 的用户入口确实表现在 `Agent` 工具和 `/fork` 命令这一层。
2. 但从代码架构看，`fork` 不是一个孤立工具能力，而是一套贯穿 agent loop 的运行时机制。
3. 它的本质是：**复用同一套 `query()` 主循环，只是为子执行链构造了新的 `ToolUseContext`、新的消息前缀、独立 transcript、任务通知与恢复能力**。
4. 因此，`fork` 更准确的描述是：**Claude Code 在主 agent loop 之上实现的一种 sidechain / forked execution substrate**，而不只是“多了一个 tool 参数”。

---

## 2. 主智能体循环在哪里

Claude Code 的主智能体循环位于：

- `src/query.ts`

核心入口是：

- `query()`
- `queryLoop()`

`queryLoop()` 内部是一个显式的 `while (true)` 循环，见 `src/query.ts:307`。这就是整个 Claude Code agentic runtime 的中心。

每一轮循环大体做这些事情：

1. 从 `state` 中取出当前的 `messages`、`toolUseContext`、`turnCount` 等状态。
2. 做 prefetch、compact、budget 等前处理。
3. 发起一次模型请求。
4. 如果模型输出 `tool_use`，执行工具。
5. 将 `tool_result`、attachment、memory、task-notification 等重新并回消息流。
6. 生成新的 `state`，进入下一轮。
7. 没有后续工具调用时，执行 stop hooks、预算判断，并结束本轮 agent turn。

几个关键位置：

- 主循环起点：`src/query.ts:307`
- 工具执行入口：`src/query.ts:1380`
- task-notification / attachment 注入：`src/query.ts:1567`
- 递归进入下一轮：`src/query.ts:1714`

这点很重要：**fork 并没有自己单独实现另一套 agent loop，它直接复用了这条 `query()` 主循环。**

---

## 3. fork 的入口：不是单独命令，而是 AgentTool 的一种分流

`fork` 的命令入口确实存在于命令表中：

- `src/commands.ts:113`

这里通过 `FORK_SUBAGENT` feature gate 挂载了 `./commands/fork/index.js`。

但更核心的入口不在 slash command，而在 `AgentTool`：

- `src/tools/AgentTool/AgentTool.tsx`

关键逻辑位于 `src/tools/AgentTool/AgentTool.tsx:318` 附近：

- 如果显式传了 `subagent_type`，按普通子智能体处理。
- 如果没有传 `subagent_type` 且 `FORK_SUBAGENT` 开启，就进入 fork 路径。
- 如果 gate 没开，才回退到默认的 `general-purpose` agent。

也就是说，fork 的触发条件不是“调用一个完全不同的工具”，而是：

- **同一个 `Agent` 工具**
- **在省略 `subagent_type` 时触发特殊分流**

这是第一个能说明 “fork 不只是一个工具” 的证据：它被直接编码进了 `AgentTool` 的路由语义里。

---

## 4. fork 为什么不是“只是一个工具”

如果 fork 只是工具层能力，通常会看到这样的形态：

1. 定义一个 `ForkTool`
2. 收集输入
3. 发起一次子任务
4. 返回结果

但 Claude Code 不是这样实现的。这里的 fork 至少包含以下几个运行时层面的机制：

1. 子上下文隔离
2. 消息前缀继承
3. Prompt Cache 对齐
4. sidechain transcript 持久化
5. 后台任务注册
6. `task-notification` 异步回传
7. fork child 的 resume
8. 递归 fork 防护

这些都不是“单个 tool call”能自然覆盖的事情。它们构成的是一套子执行链 runtime。

---

## 5. fork 子上下文是怎么构造的

这部分的核心在：

- `src/utils/forkedAgent.ts`
- `createSubagentContext()`

`createSubagentContext()` 是 Claude Code 所有子智能体执行上下文的通用构造器，见 `src/utils/forkedAgent.ts:345`。

它默认会做几类事：

### 5.1 克隆而不是共享可变状态

- 克隆 `readFileState`
- 重建 `nestedMemoryAttachmentTriggers`
- 重建 `loadedNestedMemoryPaths`
- 重建 `dynamicSkillDirTriggers`
- 为 `discoveredSkillNames` 建新集合

这说明子 agent 不是简单共享父 agent 的运行态，而是建立隔离副本。

### 5.2 处理 abort 与权限显示

默认情况下：

- 子 agent 获得一个新的 child `AbortController`
- `getAppState()` 会包一层，使其默认 `shouldAvoidPermissionPrompts = true`

这意味着后台子链默认尽量不打断主线程 UI。

### 5.3 将大多数 mutation callback 置空

默认会把这些回调变成 no-op：

- `setAppState`
- `setInProgressToolUseIDs`
- `updateFileHistoryState`

只有在显式要求共享时才会放开，例如同步 subagent 可能共享：

- `setAppState`
- `setResponseLength`
- `abortController`

### 5.4 生成新的 agent 身份

每个子 agent 都会获得：

- 新的 `agentId`
- 新的 `queryTracking.chainId`
- 递增的 `queryTracking.depth`

这使 fork/subagent 在 tracing、transcript、resume 和任务路由层都能被区分。

### 5.5 特别关键：克隆 `contentReplacementState`

这里有一段非常关键的注释，见 `src/utils/forkedAgent.ts:388` 起。

它说明为什么子链默认要 clone `contentReplacementState`：

- fork 需要处理父消息里的 `tool_use_id`
- 如果 replacement state 不一致，会导致内容替换决策不一致
- 决策不一致会让 wire prefix 不一致
- wire prefix 不一致就会导致 Prompt Cache miss

这说明 fork 的实现目标之一就是：**让子链尽量保持与父链兼容的缓存语义**。

---

## 6. fork 的消息前缀与 Prompt Cache 设计

fork 的另一个核心文件是：

- `src/tools/AgentTool/forkSubagent.ts`

这里定义了一个 synthetic agent：

- `FORK_AGENT`

关键属性：

- `agentType: 'fork'`
- `tools: ['*']`
- `model: 'inherit'`
- `permissionMode: 'bubble'`

设计意图很明确：

1. 工具池与父 agent 对齐
2. 模型与父 agent 对齐
3. 权限提示向父终端冒泡
4. 关键目标是让 API request prefix 尽可能 byte-identical

### 6.1 fork child 不是 fresh agent，而是继承父消息前缀

普通 `subagent_type=xxx` 的 agent，一般是 fresh prompt，需要完整 briefing。

fork child 不是这样。它会构造一段特殊前缀，逻辑在 `buildForkedMessages()`，见 `src/tools/AgentTool/forkSubagent.ts:107`。

做法是：

1. 保留父 assistant message 的完整内容
2. 抽出其中所有 `tool_use`
3. 为每个 `tool_use` 构造相同占位文本的 `tool_result`
4. 在最后拼接当前 child 的 directive

形成的结构是：

```text
[...父历史, assistant(完整 tool_use), user(统一 placeholder tool_results + 当前 directive)]
```

这套设计的目的不是表面上的“把父上下文传下去”这么简单，而是更强的约束：

- **不同 fork child 的前缀尽量一致**
- **只有最后的 directive 文本不同**
- **从而最大化 Prompt Cache 命中率**

### 6.2 fork boilerplate 是运行时规则，不只是提示词装饰

`buildChildMessage()` 会注入一大段 fork boilerplate，见 `src/tools/AgentTool/forkSubagent.ts:171`。

其中关键规则包括：

- 你是 forked worker，不是主 agent
- 不要再次 fork / 不要再委派
- 不要闲聊、不要做 meta commentary
- 直接用工具
- 改了文件就提交并回报 commit hash

这段内容不是普通 UX 文案，它实际上承担了运行时约束的一部分。

### 6.3 递归 fork 防护

在 `AgentTool.tsx:325` 和 `forkSubagent.ts:73`，可以看到双重防护：

1. 检查 `toolUseContext.options.querySource === 'agent:builtin:fork'`
2. 回退到扫描消息里是否包含 fork boilerplate tag

这样做是因为 autocompact 可能改写消息，但 `context.options.querySource` 仍然保留。

这不是简单工具调用会需要考虑的问题，而是 runtime 层的健壮性设计。

---

## 7. fork 子智能体如何复用主循环

真正把 fork 接入主循环的函数是：

- `runForkedAgent()`
- 文件：`src/utils/forkedAgent.ts`

这段代码的结构非常直接：

1. 读取 `CacheSafeParams`
2. 用 `createSubagentContext()` 构造隔离上下文
3. 组装 `initialMessages`
4. 调用 `query()`
5. 收集消息、累计 usage、写 transcript

见 `src/utils/forkedAgent.ts:489` 到 `src/utils/forkedAgent.ts:620`。

这说明 fork 的真实执行模型是：

```text
fork = 同一套 query()
     + fork 专用 initialMessages
     + fork 专用 ToolUseContext
     + fork 专用 transcript / tracking
```

所以它是“运行时机制”而不是“额外工具”的第二个核心证据。

### 7.1 CacheSafeParams 的作用

`CacheSafeParams` 定义在同文件前部，包含：

- `systemPrompt`
- `userContext`
- `systemContext`
- `toolUseContext`
- `forkContextMessages`

这些字段会被明确视为 cache-critical params。

注释里写得很清楚：fork 的 API 请求必须尽量和父请求保持一致，否则 prompt cache 会失效。

### 7.2 runForkedAgent() 不只是 AgentTool 用

这也很关键。`runForkedAgent()` 不是只给用户显式 fork 用的，而是运行时的通用 fork substrate。后面第 11 节会详细展开。

---

## 8. 后台任务、通知与恢复机制

fork/subagent 真正成为“长期运行机制”，不是靠一次函数调用，而是靠任务系统。

关键部件包括：

- `src/tasks/LocalAgentTask/LocalAgentTask.tsx`
- `src/tools/AgentTool/agentToolUtils.ts`
- `src/tools/AgentTool/resumeAgent.ts`

### 8.1 后台注册

异步 agent 会通过 `registerAsyncAgent()` 注册成后台任务，见 `src/tasks/LocalAgentTask/LocalAgentTask.tsx:466`。

它会：

- 初始化 task output
- 创建 abort controller
- 注册 cleanup
- 将任务写入 `AppState`
- 标记 `isBackgrounded: true`

这说明 fork/agent 不是“一次性调用后即忘”，而是系统里的一等任务对象。

### 8.2 生命周期驱动

后台 agent 的执行由 `runAsyncAgentLifecycle()` 驱动，见 `src/tools/AgentTool/agentToolUtils.ts:520`。

它会：

1. 拉起子流 `makeStream()`
2. 增量更新任务进度
3. 维护 task summary
4. 完成后调用 `completeAsyncAgent()`
5. 再做分类、通知、收尾

这是一套完整的异步 agent 生命周期管理器。

### 8.3 主循环如何接收异步结果

主线程不会一直阻塞等待子 agent 完成，而是通过 `task-notification` 收消息。

`query.ts` 在每轮工具执行后，会取命令队列里发给当前 agent 的 notification，并把它们转成 attachment 重新并回 loop，见 `src/query.ts:1567`。

规则是：

- 主线程只消费 `agentId === undefined` 的 prompt
- 子 agent 只消费发给自己 `agentId` 的 `task-notification`

这套机制意味着：

- fork 结果回到主线程，不是靠同步返回值
- 而是靠 agent loop 每轮主动 drain 自己可见的通知队列

这正是“贯穿 agent loop”的体现。

### 8.4 transcript 与 resume

每个 subagent/fork 都会把消息写到 sidechain transcript。

普通运行时：

- `runAgent()` 会记录 `initialMessages`
- 后续每条 recordable message 继续 append

fork runtime 里：

- `runForkedAgent()` 也会记录 `initialMessages`
- 持续写 sidechain transcript

恢复逻辑在 `src/tools/AgentTool/resumeAgent.ts`：

1. 读取 transcript 和 metadata
2. 清理 orphaned / unresolved 消息
3. 重建 `contentReplacementState`
4. 如果是 resumed fork，尽量重建父 system prompt
5. 重新进入 `runAgent()`

这说明 fork 不是 fire-and-forget 的匿名调用，而是可恢复的执行链。

---

## 9. fork 与普通 subagent 的差异

虽然两者都复用 `createSubagentContext()` 和 `query()`，但设计目标明显不同。

### 9.1 上下文来源不同

普通 subagent：

- fresh start
- 需要主 agent 显式 briefing

fork：

- 继承父上下文
- 直接从父 assistant/tool_use 位置切出 sidechain

### 9.2 工具与 thinking 策略不同

在 `runAgent.ts:679` 可以看到：

- fork child 走 `useExactTools`
- thinking config 尽量继承父请求
- 普通 subagent 常常会关闭 thinking 以节约成本

### 9.3 cache 目标不同

普通 subagent 追求的是任务隔离、工具裁剪、职责明确。

fork 则显式追求：

- cache-identical prefix
- system prompt byte-exact 继承
- replacement state 一致

这已经不是一个普通“spawn worker”模式，而是专门为“上下文继承 sidechain”做的实现。

### 9.4 交互语义不同

普通 subagent 更像“给一个同事派任务”。

fork 更像“把当前 agent 自己从这个节点分叉出去继续跑，但不污染主线程上下文”。

---

## 10. fork 与 coordinator mode 的关系

`forkSubagent.ts` 开头明确写了：

- fork 与 coordinator mode 互斥

见 `src/tools/AgentTool/forkSubagent.ts:29`。

`isForkSubagentEnabled()` 会在以下场景直接返回 false：

- coordinator mode
- non-interactive session

为什么互斥？

因为 coordinator mode 已经有自己的多 worker 编排模型，定义在：

- `src/coordinator/coordinatorMode.ts`

coordinator 的角色不是“自己分叉上下文”，而是：

1. 自己负责综合与对用户回复
2. 通过 `Agent`、`SendMessage`、`TaskStop` 等工具调度 worker
3. 通过 `<task-notification>` 接收 worker 结果

也就是说：

- fork 模式强调“当前 agent 的上下文分叉”
- coordinator mode 强调“中心调度器 + 多 worker”

它们是两种不同的编排哲学，因此被设计成互斥是合理的。

---

## 11. 横切复用：fork 不是只给 AgentTool 用的

如果 `fork` 只是 AgentTool 的一种特殊分支，那它的实现大概率只会出现在 `src/tools/AgentTool/` 里。

但实际不是。`runForkedAgent()` 在多个系统服务里被复用：

### 11.1 Session Memory

- 文件：`src/services/SessionMemory/sessionMemory.ts`
- 用途：后台提取会话记忆

这里先用 `createSubagentContext()` 做 setup，再调用 `runForkedAgent()` 跑 memory extraction。

### 11.2 Prompt Suggestion

- 文件：`src/services/PromptSuggestion/promptSuggestion.ts`
- 用途：生成 prompt suggestion

这里甚至专门强调：不要改动任何会影响父请求 cache key 的参数。

### 11.3 Extract Memories

- 文件：`src/services/extractMemories/extractMemories.ts`
- 用途：自动记忆抽取

这里设置了 `skipTranscript: true`，说明 fork substrate 还能按场景裁剪。

### 11.4 Agent Summary

- 文件：`src/services/AgentSummary/agentSummary.ts`
- 用途：定时 fork 子 agent 自己的会话做摘要

注释里直接写明：

- “Forks the sub-agent's conversation every ~30s using runForkedAgent()”

### 11.5 Auto Dream / Magic Docs

- `src/services/autoDream/autoDream.ts`
- `src/services/MagicDocs/magicDocs.ts`

这些后台机制也都在沿用相同的 fork 思路。

这足以说明：**fork 在 Claude Code 中已经是一种通用运行时基础设施，而不是 AgentTool 的局部特性。**

---

## 12. 调用链总结

把上面的链路压缩成一张简单调用图，可以这样理解：

### 12.1 主线程

```text
用户输入
  -> query()
  -> queryLoop()
  -> 模型输出 tool_use
  -> AgentTool.call()
```

### 12.2 进入 fork 分流

```text
AgentTool.call()
  -> 未指定 subagent_type
  -> isForkSubagentEnabled() = true
  -> 选择 FORK_AGENT
  -> buildForkedMessages()
  -> createSubagentContext()
  -> runForkedAgent() / runAgent()
```

### 12.3 fork child 执行

```text
runForkedAgent()
  -> query()
  -> queryLoop()
  -> 工具执行
  -> sidechain transcript
  -> 后台任务状态更新
  -> 完成后发 task-notification
```

### 12.4 主线程收结果

```text
主线程下一轮 queryLoop()
  -> drain queuedCommandsSnapshot
  -> getAttachmentMessages()
  -> 收到 task-notification
  -> 再由主线程决定是否继续 / 汇总 / 回复用户
```

---

## 总结

如果只看表层 API，可以说 Claude Code 的 fork 是由 `Agent` 工具触发的一种能力。

但如果看源码架构，更准确的表述应该是：

- `fork` 的入口在工具层
- `fork` 的实现落在运行时层
- `fork` 的执行依赖统一的 `query()` 主循环
- `fork` 的可用性依赖 sidechain context、transcript、notification、resume、cache-safe prefix 这些底层机制

所以，回答最初的问题：

**是的，Claude Code 的 fork 不是“仅仅一个工具”，而是一套贯穿 agent loop 的机制。**

它的本质是：

**在同一个主智能体运行时框架中，支持从当前上下文切出一条独立但可恢复、可通知、可缓存对齐的子执行链。**

---

## 13. 主对话循环的三元结构：用户对话、模型回复、工具调用

理解 Claude Code 的主循环时，一个常见误区是把它想成三套并列流程：

1. 用户对话循环
2. 大模型回复循环
3. 工具调用循环

实际上，源码实现不是三套循环，而是**一套统一的 message-state loop**。

核心文件仍然是：

- `src/query.ts`

主循环里的三个核心中间状态是：

- `messagesForQuery`
- `assistantMessages`
- `toolResults`

其中：

- `messagesForQuery` 表示本轮送给模型的上下文基座
- `assistantMessages` 表示本轮模型流式输出累积出来的 assistant 消息
- `toolResults` 表示本轮工具执行后回灌到消息流里的 user/attachment 消息

### 13.1 用户输入如何进入主循环

用户消息不是在 `queryLoop()` 里临时读取的，而是在进入 `query()` 之前就已经被追加到了 `params.messages` 里。

因此在每轮循环开始时：

- `state.messages` 已经包含当前用户输入
- `messagesForQuery` 是从 `state.messages` 派生出来的当前上下文视图

见：

- `src/query.ts:270`
- `src/query.ts:365`

也就是说，Claude Code 的循环并不是“等用户说一句 -> 进一次模型 -> 退出”，而是：

- 以当前完整消息历史为输入
- 不断让 assistant 和 tool_result 在同一个消息数组上继续增长
- 直到这一轮 assistant 不再要求继续

### 13.2 模型回复如何进入主循环

每轮会调用一次模型 streaming 接口：

- `deps.callModel(...)`
- 见 `src/query.ts:659`

流式返回的 `assistant` 消息会被持续 push 进：

- `assistantMessages`

见：

- `src/query.ts:826`

同时，如果某个 assistant content block 里有 `tool_use`：

- 会被抽出来放入 `toolUseBlocks`
- 并将 `needsFollowUp = true`

见：

- `src/query.ts:829`

这里有一个非常重要的语义：

- `needsFollowUp` 才是“这一轮要不要继续走工具阶段”的核心信号
- 不是单纯依赖 API 的 `stop_reason === tool_use`

### 13.3 工具调用如何进入主循环

当 `toolUseBlocks` 非空时，主循环不会结束，而会进入工具执行阶段：

- `runTools(...)`
- 或 `StreamingToolExecutor`

见：

- `src/query.ts:1380`

工具执行产物不会作为一个“循环外副作用”存在，而是会重新变成消息的一部分：

- 标准 `tool_result`
- attachment
- task-notification
- memory attachment

然后统一 push 回：

- `toolResults`

因此 Claude Code 的对话循环其实可以理解成：

```text
用户消息
  -> 模型输出 assistant
  -> assistant 要求工具
  -> 执行工具
  -> 工具结果转成消息再塞回上下文
  -> 再次调用模型
```

### 13.4 下一轮是怎么形成的

如果本轮发生了工具调用，那么主循环在末尾会构造下一轮状态：

```text
next.messages =
  messagesForQuery
  + assistantMessages
  + toolResults
```

见：

- `src/query.ts:1715`

这就是 Claude Code 的核心闭环：

```text
messages -> model -> assistantMessages -> tools -> toolResults -> messages
```

所以从架构上说：

- 用户对话
- 大模型回复
- 工具调用

不是三套循环，而是**同一套消息驱动状态机中的三个阶段**。

---

## 14. 主循环里的 fork 插入点

有了上面的三元结构，就能精确回答：

> fork 出来的记忆检索、记忆反写，以及其他 fork，分别插在主循环哪一步？

先说一个关键结论：

- **记忆检索通常不是 fork**
- **记忆反写通常才是 fork**

### 14.1 记忆检索：不是 fork，而是 prefetch + attachment 注入

主循环开始时会启动：

- `startRelevantMemoryPrefetch(...)`

见：

- `src/query.ts:301`
- `src/utils/attachments.ts:2361`

这个过程会：

1. 从最近一条真实用户消息抽出 query
2. 搜索 memory files
3. 用 `sideQuery()` 做选择

真正做选择的是：

- `findRelevantMemories(...)`
- `sideQuery(...)`

见：

- `src/memdir/findRelevantMemories.ts:39`
- `src/memdir/findRelevantMemories.ts:98`

它何时进入主对话？

是在工具阶段之后、进入下一轮模型调用之前，被转成 attachment 注入：

- `src/query.ts:1592`

所以记忆检索时序是：

```text
本轮开始
  -> 启动 memory prefetch
  -> 模型输出 / 工具执行
  -> 注入 memory attachments
  -> 下一轮模型看到这些 memories
```

因此记忆检索的位置是：

- **本轮前半段启动**
- **本轮后半段注入**
- **下一轮采样生效**

但它**不属于 fork 子链**。

### 14.2 Session Memory 反写：post-sampling 阶段 fork

`SessionMemory` 的写回是 fork，而且位置比较靠前。

主循环在本轮模型流式输出完成后，会先触发：

- `executePostSamplingHooks(...)`

见：

- `src/query.ts:1000`

`SessionMemory` 通过 `registerPostSamplingHook(...)` 挂进来，见：

- `src/services/SessionMemory/sessionMemory.ts:373`

而它内部会：

- `runForkedAgent({ querySource: 'session_memory', ... })`

见：

- `src/services/SessionMemory/sessionMemory.ts:318`

所以 `session_memory` 的位置是：

```text
模型本轮 assistant 输出完成
  -> post-sampling hooks
  -> fork 一个 session_memory 子链
  -> 反写 memory 文件
```

### 14.3 extractMemories：stop-hook 阶段 fork

另一类记忆反写不在 post-sampling，而在 stop-hook 阶段。

主循环只有在 `!needsFollowUp` 时，才会进入 stop-hook 路径：

- `src/query.ts:1062`

然后调用：

- `handleStopHooks(...)`

见：

- `src/query.ts:1267`

`handleStopHooks()` 内部会 fire-and-forget 地启动：

- `executeExtractMemories(...)`

见：

- `src/query/stopHooks.ts:141`

而真正执行时会：

- `runForkedAgent({ querySource: 'extract_memories', ... })`

见：

- `src/services/extractMemories/extractMemories.ts:415`

所以这条链的位置是：

```text
本轮 assistant 已经不再要求 follow-up
  -> stop hooks
  -> 后台 fork extract_memories
  -> 反写长期记忆
```

### 14.4 其他主循环相关 fork

除了记忆相关 fork，主线程这条 `query()` 路径上还会涉及几类重要 fork：

#### A. 用户显式 fork

这是最典型的“主循环中段 fork”。

assistant 在工具阶段调用 `Agent` 工具，且省略 `subagent_type` 时：

- 进入 `FORK_AGENT`

见：

- `src/tools/AgentTool/AgentTool.tsx:318`

它发生的位置是：

- **模型已经输出 tool_use**
- **主循环正在执行工具**

也就是严格意义上的：

- **tool phase 中段 fork**

#### B. compact fork

主循环会在每轮正式调用模型前执行：

- `deps.autocompact(...)`

见：

- `src/query.ts:454`

compact 实现内部会：

- `runForkedAgent({ querySource: 'compact', ... })`

见：

- `src/services/compact/compact.ts:1188`

这说明 compact fork 的位置是：

- **本轮模型采样之前**

#### C. reactive compact fork

如果模型本轮返回 prompt-too-long / media-size 等可恢复错误：

- 主循环会走 `reactiveCompact.tryReactiveCompact(...)`

见：

- `src/query.ts:1119`

这个恢复过程本身也会依赖 compact fork 逻辑。

所以它的位置是：

- **本轮模型输出之后**
- **stop hooks 之前**

#### D. promptSuggestion

这个 fork 挂在 stop-hook 阶段：

- `src/query/stopHooks.ts:138`

内部会：

- `runForkedAgent({ querySource: 'prompt_suggestion', ... })`

见：

- `src/services/PromptSuggestion/promptSuggestion.ts:319`

#### E. autoDream

这个也是 stop-hook 阶段启动：

- `src/query/stopHooks.ts:149`

内部会：

- `runForkedAgent({ querySource: 'auto_dream', ... })`

见：

- `src/services/autoDream/autoDream.ts:224`

### 14.5 哪些不该算作“主线程中段 fork”

为了避免混淆，下面这些虽然复用了 fork substrate，但不属于主线程正常 turn 的“中段 fork”：

- `agent_summary`
  - 这是对子 agent 会话做定时摘要
  - 见 `src/services/AgentSummary/agentSummary.ts:109`
- `/btw` side question
  - 这是额外 fork 一个轻量问答 agent
  - 见 `src/utils/sideQuestion.ts:80`
- 各类独立 sideQuery
  - 这些是旁路模型调用，不走 `runForkedAgent()`

---

## 15. 一张时间轴看主循环与 fork

把主线程这一轮的关键阶段压成时间轴，可以写成：

```text
Turn 开始
  -> state.messages 已包含用户输入
  -> startRelevantMemoryPrefetch()
  -> 预处理：snip / microcompact / collapse / autocompact
     -> 这里可能触发 compact fork
  -> 调模型，流式收集 assistantMessages
  -> 若 assistant 含 tool_use：
     -> 执行工具
     -> 这里可能显式触发 Agent fork
     -> 收集 toolResults / task-notification / attachments
     -> 注入 memory attachments
     -> 进入下一轮 query
  -> 若 assistant 不再要求工具：
     -> executePostSamplingHooks()
        -> 这里可能触发 session_memory fork
     -> 错误恢复（reactive compact）
        -> 这里可能触发 compact fork
     -> handleStopHooks()
        -> 这里可能触发 extract_memories / prompt_suggestion / auto_dream
     -> Turn 结束
```

---

## 16. 主对话循环伪代码

下面这段伪代码不是 1:1 复刻源码，而是尽量保留 `query.ts` 的真实控制流和关键分支，帮助理解整个主对话循环。

```typescript
async function query(params) {
  let state = {
    messages: params.messages,           // 已包含用户最新输入
    toolUseContext: params.toolUseContext,
    turnCount: 1,
    transition: undefined,
    maxOutputTokensRecoveryCount: 0,
    hasAttemptedReactiveCompact: false,
    pendingToolUseSummary: undefined,
  }

  const pendingMemoryPrefetch =
    startRelevantMemoryPrefetch(state.messages, state.toolUseContext)

  while (true) {
    let messagesForQuery = projectCurrentConversation(state.messages)

    // 1. 本轮采样前预处理
    messagesForQuery = applyToolResultBudget(messagesForQuery)
    messagesForQuery = maybeSnip(messagesForQuery)
    messagesForQuery = maybeMicrocompact(messagesForQuery)
    messagesForQuery = maybeCollapse(messagesForQuery)

    const compactResult = await maybeAutocompact(messagesForQuery)
    if (compactResult) {
      yield compactBoundaryMessages(compactResult)
      messagesForQuery = buildPostCompactMessages(compactResult)
    }

    state.toolUseContext.messages = messagesForQuery

    const assistantMessages = []
    const toolUseBlocks = []
    const toolResults = []
    let needsFollowUp = false

    // 2. 调模型，流式接收 assistant 输出
    for await (const message of callModel(messagesForQuery)) {
      if (!isWithheldRecoverableError(message)) {
        yield message
      }

      if (message.type === 'assistant') {
        assistantMessages.push(message)

        const blocks = extractToolUseBlocks(message)
        if (blocks.length > 0) {
          toolUseBlocks.push(...blocks)
          needsFollowUp = true
        }
      }
    }

    // 3. assistant 没要求工具：进入 turn 收尾阶段
    if (!needsFollowUp) {
      yieldResolvedToolUseSummaryIfAny()

      const recovery = await maybeRecoverPromptTooLongOrMaxTokens(
        messagesForQuery,
        assistantMessages,
        state,
      )
      if (recovery.type === 'retry') {
        state = recovery.nextState
        continue
      }
      if (recovery.type === 'terminal_error') {
        yield recovery.errorMessage
        return
      }

      // post-sampling hooks
      // 这里可能触发 session_memory fork
      executePostSamplingHooks([
        ...messagesForQuery,
        ...assistantMessages,
      ])

      // stop hooks
      // 这里可能触发 extract_memories / prompt_suggestion / auto_dream
      const stopHookResult = await handleStopHooks(
        messagesForQuery,
        assistantMessages,
        state.toolUseContext,
      )
      if (stopHookResult.preventContinuation) {
        return
      }
      if (stopHookResult.blockingErrors.length > 0) {
        state.messages = [
          ...messagesForQuery,
          ...assistantMessages,
          ...stopHookResult.blockingErrors,
        ]
        continue
      }

      return
    }

    // 4. assistant 要求工具：进入工具阶段
    for await (const update of runTools(
      toolUseBlocks,
      assistantMessages,
      state.toolUseContext,
    )) {
      if (update.message) {
        yield update.message
        toolResults.push(normalizeToConversationMessage(update.message))
      }
      if (update.newContext) {
        state.toolUseContext = update.newContext
      }
    }

    // 5. 注入 attachment / task-notification / memory attachments
    for await (const attachment of getAttachmentMessages(
      [...messagesForQuery, ...assistantMessages, ...toolResults]
    )) {
      yield attachment
      toolResults.push(attachment)
    }

    const memoryAttachments = await collectPrefetchedMemoriesIfReady(
      pendingMemoryPrefetch,
      state.toolUseContext.readFileState,
    )
    for (const mem of memoryAttachments) {
      yield mem
      toolResults.push(mem)
    }

    // 6. 形成下一轮上下文
    state.messages = [
      ...messagesForQuery,
      ...assistantMessages,
      ...toolResults,
    ]
    state.turnCount += 1

    if (exceedsMaxTurns(state.turnCount)) {
      yield maxTurnsAttachment()
      return
    }
  }
}
```

---

## 17. 最终归纳

如果只保留最核心的一句话，可以这样概括 Claude Code 的主对话循环：

**Claude Code 维护一条统一的消息状态链，模型回复和工具结果都只是这条消息链上的新增片段；fork 则是在这条链的特定时刻切出 sidechain，执行额外任务后再通过通知、摘要或持久化结果影响主链。**

因此：

- 记忆检索通常不是 fork，而是 prefetch + attachment
- 记忆反写通常是 fork，但可能挂在 post-sampling 或 stop-hook
- 显式 Agent fork 发生在工具执行阶段
- compact fork 发生在采样前或错误恢复阶段

这些 fork 共同说明：Claude Code 的主循环不是一个“单线程对话 -> 单次模型调用”的简单流程，而是一个可插入 sidechain、可恢复、可压缩、可异步维护的 agent runtime。
