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
