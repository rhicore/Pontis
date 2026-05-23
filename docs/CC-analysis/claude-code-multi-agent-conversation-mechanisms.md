# Claude Code 多智能体与额外对话机制调研

> 本文档把 Claude Code 中“在主用户对话之外额外开启一个 LLM 对话”的机制统一梳理。这里的“额外对话”不只包括 fork，也包括 fresh subagent、后台 agent、swarm teammate、远端 CCR session、context:fork 技能、hook agent 以及若干后台 forked 服务。

---

## 1. 调研口径

本文把以下情况都算作“额外对话”：

1. 有独立的 `messages[]` 或独立 transcript。
2. 复用 `query()` 启动新的 agent loop。
3. 通过 `runAgent()`、`runForkedAgent()` 或直接 `query()` 生成新的模型请求链。
4. 能在用户对话期间并行、后台或递归地产生新的模型输出。
5. 即使不直接展示给用户，只要它是为了当前用户会话服务的独立模型对话，也纳入。

不把普通工具执行、Bash 子进程、MCP 连接本身算作额外对话，除非它们内部又启动了 LLM loop。

---

## 2. 总览结论

Claude Code 的多智能体不是单一功能，而是几套共享底座叠加出来的：

| 机制 | 入口 | 是否新对话 | 上下文来源 | 持久化 | 用户可见性 |
|---|---|---:|---|---|---|
| fresh subagent | `Agent({ subagent_type })` | 是 | 新 prompt，默认零父上下文 | sidechain transcript | Agent 进度、结果 |
| fork subagent | `Agent()` 省略 `subagent_type`，或 `/fork` 语义 | 是 | 继承父 system prompt 和父消息前缀 | sidechain transcript | 后台任务通知 |
| background/local agent | `run_in_background`、fork 强制 async、coordinator | 是 | 同 AgentTool 路径 | sidechain transcript 和 task output | 任务面板、通知 |
| SendMessage 续聊 | `SendMessage({ to })` | 不是新建，但会恢复旧对话 | 旧 agent transcript 加新 user message | 继续写原 sidechain | 通知、任务输出 |
| worktree agent | `isolation: "worktree"` | 是 | Agent/fork 的变体 | sidechain transcript 加 worktree metadata | 返回 worktree 信息 |
| remote agent | `isolation: "remote"` | 是 | 新 CCR session | remote metadata sidecar | remote task URL 和通知 |
| swarm teammate | `TeamCreate` 后 `Agent({ team_name, name })` | 是 | 新 teammate 对话 | team file、mailbox、task state | team 面板、mailbox |
| in-process teammate | teammate mode 为 in-process | 是 | 同进程独立 history | AppState task messages | team 面板 |
| context:fork skill/slash | `SkillTool` 或 slash command | 是 | skill prompt 作为新 agent prompt | 通常只返回结果 | 进度 UI 或静默 |
| hook agent | agent hook | 是 | hook prompt 加 transcript 路径 | hook 内部 agentId | 通常不可见 |
| 后台 forked 服务 | compact、memory、suggestion 等 | 是 | 主对话 cache-safe prefix | 多数 skip transcript | 多数不可见 |
| remote-control bridge session | Web/mobile remote-control 新 session | 是 | Sessions API 创建的新 session | 远端 session 和本地子进程 | bridge UI |

核心底座有三层：

1. `query()` 是所有 LLM agent loop 的中心。
2. `runAgent()` 是 AgentTool、SkillTool、slash fork、in-process teammate 复用的标准子 Agent 编排器。
3. `runForkedAgent()` 是 compact、memory、suggestion、side question 等服务型 fork 复用的轻量封装，目标是共享 prompt cache。

---

## 3. AgentTool: fresh subagent、fork、后台 agent

`AgentTool.call()` 是用户对话内最核心的新对话入口。它先判断是否是 swarm teammate，再判断是否走 fork 或普通 subagent。见 `src/tools/AgentTool/AgentTool.tsx:239` 和 `src/tools/AgentTool/AgentTool.tsx:282`。

### 3.1 路由规则

`AgentTool` 的路由是：

1. 如果传了 `team_name` 并且传了 `name`，进入 teammate spawn。
2. 否则，如果传了 `subagent_type`，按 fresh subagent 处理。
3. 如果没传 `subagent_type` 且 `FORK_SUBAGENT` 开启，进入 fork path。
4. 如果没传 `subagent_type` 且 fork gate 关闭，回退到 `general-purpose`。

这段逻辑见 `src/tools/AgentTool/AgentTool.tsx:318`。

### 3.2 fresh subagent

fresh subagent 使用选中的 `AgentDefinition` 生成自己的 system prompt，然后把 `prompt` 包装成第一条 user message。它不是继承完整父对话，而是需要父模型在 prompt 中交代背景。见 `src/tools/AgentTool/AgentTool.tsx:513`。

实际执行由 `runAgent()` 驱动。`runAgent()` 会：

- 分配新的 `agentId`。
- 按 agent 定义解析模型、工具、MCP、system prompt。
- 调用 `createSubagentContext()` 生成隔离的 `ToolUseContext`。
- 调用 `query()` 进入标准 agent loop。
- 把消息写入 sidechain transcript，供任务面板和 resume 使用。

关键代码见 `src/tools/AgentTool/runAgent.ts:347`、`src/tools/AgentTool/runAgent.ts:700`、`src/tools/AgentTool/runAgent.ts:735` 和 `src/tools/AgentTool/runAgent.ts:748`。

### 3.3 fork subagent

fork path 使用一个 synthetic agent: `FORK_AGENT`。它不注册到普通 agent 列表，只在省略 `subagent_type` 且 gate 开启时触发。定义见 `src/tools/AgentTool/forkSubagent.ts:60`。

fork 的关键点：

- `model: "inherit"`，保持父模型。
- `tools: ["*"]` 并配合 `useExactTools`，让工具定义尽量与父请求一致。
- `permissionMode: "bubble"`，权限提示冒泡到父终端。
- 使用父对话已渲染的 system prompt，而不是重新生成 fork agent prompt。
- 用 `buildForkedMessages()` 克隆父 assistant message 中的 `tool_use`，再补统一 placeholder `tool_result` 和当前 fork directive。

`buildForkedMessages()` 的目标是让多个 fork child 的请求前缀尽量 byte-identical，从而最大化 prompt cache 命中。见 `src/tools/AgentTool/forkSubagent.ts:95` 和 `src/tools/AgentTool/forkSubagent.ts:107`。

fork 还做递归防护。fork child 虽然保留 Agent 工具以保持工具列表一致，但如果在 fork 内再次省略 `subagent_type`，会被拒绝。见 `src/tools/AgentTool/AgentTool.tsx:325` 和 `src/tools/AgentTool/forkSubagent.ts:73`。

### 3.4 async 与 foreground/background

AgentTool 支持同步等待，也支持后台执行。后台 agent 会注册为 `local_agent` task，然后异步运行 `runAsyncAgentLifecycle()`。见 `src/tools/AgentTool/AgentTool.tsx:686` 和 `src/tools/AgentTool/agentToolUtils.ts:508`。

开启 `FORK_SUBAGENT` 时，代码会强制所有 agent spawn 走 async，原因是统一使用 `<task-notification>` 交互模型。见 `src/tools/AgentTool/AgentTool.tsx:555`。

同步 agent 也会先注册为 foreground task，运行时间过长或用户操作后可以切到后台继续跑。见 `src/tools/AgentTool/AgentTool.tsx:808` 和 `src/tasks/LocalAgentTask/LocalAgentTask.tsx:526`。

### 3.5 sidechain transcript 与恢复

subagent 的 transcript 不写入主会话链，而是 sidechain。`runAgent()` 写入 initial messages 和后续 recordable messages，见 `src/tools/AgentTool/runAgent.ts:732` 和 `src/tools/AgentTool/runAgent.ts:792`。

sidechain 写入由 `recordSidechainTranscript()` 完成，见 `src/utils/sessionStorage.ts:1451`。同时 `writeAgentMetadata()` 保存 agent 类型、worktree path、description，见 `src/utils/sessionStorage.ts:274`。

`SendMessage` 可以把消息发给运行中的 agent，也可以从 transcript 恢复已经停止或被 AppState 驱逐的 agent。恢复路径见 `src/tools/SendMessageTool/SendMessageTool.ts:800` 和 `src/tools/AgentTool/resumeAgent.ts:42`。

恢复 fork 时会重新识别 `agentType === "fork"`，重新使用父 system prompt，并避免重复注入原始 fork context。见 `src/tools/AgentTool/resumeAgent.ts:99`、`src/tools/AgentTool/resumeAgent.ts:116` 和 `src/tools/AgentTool/resumeAgent.ts:166`。

---

## 4. `createSubagentContext()`: 子对话隔离边界

几乎所有本地子 Agent 都依赖 `createSubagentContext()`。它的默认策略是隔离可变状态，必要时显式共享少量回调。见 `src/utils/forkedAgent.ts:345`。

默认隔离内容包括：

- clone `readFileState`。
- 新建 nested memory、skill discovery、tool decision 集合。
- 新建 child `AbortController`。
- 包装 `getAppState()`，默认设置 `shouldAvoidPermissionPrompts`。
- `setAppState`、`setInProgressToolUseIDs`、`updateFileHistoryState` 默认 no-op。
- 生成新的 `agentId` 和新的 `queryTracking.chainId`。

其中 `contentReplacementState` 默认 clone 而不是 fresh，这是为 fork 的 prompt cache 稳定服务：父消息中的 `tool_use_id` 如果替换决策不一致，会导致 wire prefix 不一致。见 `src/utils/forkedAgent.ts:388`。

`runAgent()` 会在创建子 context 后调用同一个 `query()`。这意味着 subagent 不是另一套模型 runtime，而是主 agent loop 的隔离复用。见 `src/tools/AgentTool/runAgent.ts:747`。

---

## 5. `runForkedAgent()`: 服务型 fork 底座

`runForkedAgent()` 是另一类重要入口。它不一定是用户显式要求的 agent，但会在用户会话期间开启新的模型对话。定义见 `src/utils/forkedAgent.ts:489`。

它的输入是 `CacheSafeParams`：

- system prompt
- user context
- system context
- toolUseContext
- forkContextMessages

这些字段被明确标注为 prompt cache 的关键参数。见 `src/utils/forkedAgent.ts:46`。

`runForkedAgent()` 会创建隔离 context，把 `forkContextMessages + promptMessages` 作为 initial messages，再调用 `query()`。它可以选择写 sidechain，也可以通过 `skipTranscript` 跳过持久化。见 `src/utils/forkedAgent.ts:514`、`src/utils/forkedAgent.ts:524` 和 `src/utils/forkedAgent.ts:545`。

当前使用 `runForkedAgent()` 的服务包括：

| 调用方 | 用途 | 是否通常用户可见 |
|---|---|---|
| `compact` | 生成 compact summary | 间接可见 |
| `SessionMemory` | 更新 session memory | 通常不可见 |
| `extractMemories` | 会话后抽取长期记忆 | 有时以保存记忆提示可见 |
| `AgentSummary` | 后台 agent 进度摘要 | UI 可见 |
| `PromptSuggestion` | 生成建议 prompt | UI 可见或半隐式 |
| `speculation` | 投机执行建议输入 | 通常 UI 状态可见 |
| `autoDream` | 自动整合记忆 | 间接可见 |
| `sideQuestion` | `/btw` 旁路问答 | 直接可见 |

代码位置分别见 `src/services/compact/compact.ts:1188`、`src/services/SessionMemory/sessionMemory.ts:318`、`src/services/extractMemories/extractMemories.ts:415`、`src/services/AgentSummary/agentSummary.ts:109`、`src/services/PromptSuggestion/promptSuggestion.ts:319`、`src/services/PromptSuggestion/speculation.ts:457`、`src/services/autoDream/autoDream.ts:224` 和 `src/utils/sideQuestion.ts:80`。

这些服务的共同点是：它们通常不想污染主对话，但想复用主请求的 prompt cache。因此它们会保留工具列表、模型、thinking config 等 cache key 相关字段，再用 `canUseTool` 限制实际工具能力。

---

## 6. SkillTool 与 slash command 的 `context: "fork"`

除 AgentTool 外，技能系统也能开启额外对话。

### 6.1 SkillTool fork

如果某个 prompt command 配置了 `context === "fork"`，模型通过 `SkillTool` 调用它时，会进入 `executeForkedSkill()`。这会：

- 生成新的 `agentId`。
- 通过 `prepareForkedCommandContext()` 展开 skill prompt。
- 选择 command 指定的 agent 或 `general-purpose`。
- 调用 `runAgent()` 执行独立子对话。
- 把最终文本作为 SkillTool result 返回给父模型。

见 `src/tools/SkillTool/SkillTool.ts:118`、`src/tools/SkillTool/SkillTool.ts:205`、`src/tools/SkillTool/SkillTool.ts:223` 和 `src/tools/SkillTool/SkillTool.ts:621`。

### 6.2 slash command fork

用户直接输入 slash command 时，如果 command 的 `context === "fork"`，会走 `executeForkedSlashCommand()`。这同样调用 `runAgent()`，但结果作为 local command stdout 回到当前输入处理流程，不一定再触发主模型。见 `src/utils/processUserInput/processSlashCommand.tsx:59`、`src/utils/processUserInput/processSlashCommand.tsx:227` 和 `src/utils/processUserInput/processSlashCommand.tsx:280`。

在 KAIROS assistant mode 下，context:fork slash command 可以 fire-and-forget，在后台跑完后把结果重新塞回 command queue，作为隐藏 meta prompt 进入主 agent。见 `src/utils/processUserInput/processSlashCommand.tsx:90` 和 `src/utils/processUserInput/processSlashCommand.tsx:126`。

---

## 7. Swarm team 与 teammate

Swarm 是另一套多智能体系统。`TeamCreate` 只创建 team file、task list 和 leader 上下文，本身不启动新的 LLM 对话。见 `src/tools/TeamCreateTool/TeamCreateTool.ts:128`。

真正的新对话由 `AgentTool` 在检测到 `team_name + name` 时触发：它调用 `spawnTeammate()`。见 `src/tools/AgentTool/AgentTool.tsx:282` 和 `src/tools/shared/spawnMultiAgent.ts:1088`。

### 7.1 pane-based teammate

pane-based teammate 会在 tmux 或 iTerm2 中启动新的 Claude Code 进程：

- 构造 `--agent-id`、`--agent-name`、`--team-name`、`--parent-session-id` 等 CLI 参数。
- 通过 pane backend 发送启动命令。
- 把 teammate 写入 AppState 和 team file。
- 用 mailbox 发送初始 prompt。

见 `src/tools/shared/spawnMultiAgent.ts:399`、`src/tools/shared/spawnMultiAgent.ts:440`、`src/tools/shared/spawnMultiAgent.ts:488` 和 `src/tools/shared/spawnMultiAgent.ts:511`。

这种模式是最接近“真的开了另一个 Claude Code 对话窗口”的机制。

### 7.2 in-process teammate

in-process teammate 不启动新进程，而是在同一个 Node.js 进程中用 `AsyncLocalStorage` 隔离身份。`teammateContext.ts` 说明了三种身份来源：环境变量、dynamic team context、AsyncLocalStorage。见 `src/utils/teammateContext.ts:1`。

spawn 时会创建独立 `AbortController`、`TeammateContext` 和 `InProcessTeammateTaskState`。见 `src/utils/swarm/spawnInProcess.ts:104`。

执行时由 `startInProcessTeammate()` fire-and-forget，内部循环会不断等待新消息或 shutdown，并每轮调用 `runAgent()`。见 `src/utils/swarm/inProcessRunner.ts:1047`、`src/utils/swarm/inProcessRunner.ts:1160` 和 `src/utils/swarm/inProcessRunner.ts:1175`。

这类 teammate 有自己的 `allMessages` 历史，并在空闲后通过 mailbox 通知 leader。见 `src/utils/swarm/inProcessRunner.ts:1328`。

### 7.3 SendMessage 与 mailbox

`SendMessage` 是 swarm 和 background agent 的续聊/通信入口：

- 对 running local agent，写入 `pendingMessages`，等待下一次工具轮 drain。
- 对 stopped 或 evicted local agent，调用 `resumeAgentBackground()` 从 sidechain 恢复。
- 对 teammate，走 mailbox 或 broadcast。
- 对 bridge/UDS 地址，可以跨 session 发送纯文本。

见 `src/tools/SendMessageTool/SendMessageTool.ts:741`、`src/tools/SendMessageTool/SendMessageTool.ts:800` 和 `src/tools/SendMessageTool/SendMessageTool.ts:876`。

---

## 8. Coordinator mode

Coordinator mode 本身不是新的 agent 类型，而是把主 agent 的角色改成“调度者”，限制其工具池，并鼓励它通过 AgentTool 启动 worker。入口由 `CLAUDE_CODE_COORDINATOR_MODE` 和 `COORDINATOR_MODE` gate 控制，见 `src/coordinator/coordinatorMode.ts:36`。

Coordinator system prompt 明确要求：

- 使用 `Agent` 启动 worker。
- 使用 `SendMessage` 继续 worker。
- worker 结果以 `<task-notification>` 的 user-role message 到达。
- 并行启动独立 worker。

见 `src/coordinator/coordinatorMode.ts:116`、`src/coordinator/coordinatorMode.ts:130`、`src/coordinator/coordinatorMode.ts:142` 和 `src/coordinator/coordinatorMode.ts:211`。

在 AgentTool 中，coordinator mode 会让 agent spawn 走 async。见 `src/tools/AgentTool/AgentTool.tsx:551`。

同时 fork subagent 与 coordinator mode 互斥，因为 coordinator 已经拥有自己的 delegation model。见 `src/tools/AgentTool/forkSubagent.ts:29`。

---

## 9. Remote agent 与 remote-control bridge session

Claude Code 还有两类远端新对话。

### 9.1 AgentTool remote isolation

当 agent 使用 `isolation: "remote"` 时，AgentTool 会调用 `teleportToRemote()` 创建 CCR session，然后注册 `RemoteAgentTask`。见 `src/tools/AgentTool/AgentTool.tsx:430` 和 `src/tools/AgentTool/AgentTool.tsx:442`。

`teleportToRemote()` 通过 Sessions API 创建新 session，可以携带初始 user message、git source、bundle、environment、permission mode 等。见 `src/utils/teleport.tsx:730`、`src/utils/teleport.tsx:1095` 和 `src/utils/teleport.tsx:1140`。

本地只保留 task 状态和 remote metadata，并通过 polling 读取远端 session events。见 `src/tasks/RemoteAgentTask/RemoteAgentTask.tsx:386` 和 `src/tasks/RemoteAgentTask/RemoteAgentTask.tsx:538`。

这属于真正的新远端对话，不复用本地 `runAgent()`。

### 9.2 Remote Control bridge 多 session

Remote Control bridge 可以让 Web 或 Mobile 在当前项目里创建新的 session。`createBridgeSession()` 会调用 `/v1/sessions` 创建 bridge session，见 `src/bridge/createSession.ts:34`。

bridge 主循环 `runBridgeLoop()` 会从服务端 poll 到 `session` 类型 work，然后为每个 session spawn 一个本地 child Claude Code 进程。见 `src/bridge/bridgeMain.ts:852`、`src/bridge/bridgeMain.ts:898` 和 `src/bridge/bridgeMain.ts:1026`。

实际子进程由 `createSessionSpawner()` 启动，参数包括：

- `--print`
- `--sdk-url`
- `--session-id`
- `--input-format stream-json`
- `--output-format stream-json`
- `--replay-user-messages`

见 `src/bridge/sessionRunner.ts:248` 和 `src/bridge/sessionRunner.ts:287`。

bridge 支持 `single-session`、`same-dir`、`worktree` 三种 spawn mode。worktree 模式会为每个 session 创建隔离 worktree，见 `src/bridge/bridgeMain.ts:963`。默认模式选择见 `src/bridge/bridgeMain.ts:2278`。

这类机制更像“用户从远端 UI 在同一项目里新开一个会话”，不是 AgentTool 的子 agent，但按本文口径也算额外对话。

---

## 10. Hook agent

agent-based hook 也会启动额外 LLM 对话。`execAgentHook()` 会：

- 构造 hook prompt。
- 创建 `hook-agent-${uuid}` 作为 agentId。
- 构造单独的 `ToolUseContext`。
- 使用小模型和结构化输出工具。
- 直接调用 `query()`，而不是通过 `runAgent()`。

见 `src/utils/hooks/execAgentHook.ts:36`、`src/utils/hooks/execAgentHook.ts:121`、`src/utils/hooks/execAgentHook.ts:124` 和 `src/utils/hooks/execAgentHook.ts:166`。

它的定位不是协作型 subagent，而是“验证/拦截用的内部 agent”。但它确实是用户对话期间额外产生的模型对话。

---

## 11. 消息投递与结果回流

Claude Code 对额外对话的结果回流主要有四种方式：

1. **同步 tool result**：同步 subagent、SkillTool fork、slash fork 直接把最终文本作为 tool result 或 local command stdout 交还父流程。
2. **task notification**：async local agent、fork、remote agent 完成后把 `<task-notification>` 排入 queue。
3. **mailbox**：swarm teammate 用 team mailbox 收发消息和 idle notification。
4. **remote event polling**：remote agent 和 bridge session 从 CCR/Sessions API 取事件。

全局 command queue 是进程级单例。`query()` 在工具轮后会按 `agentId` 过滤队列：主线程只处理 `agentId === undefined`，subagent 只处理发给自己的 task notification。见 `src/query.ts:1560`。

这解释了为什么同进程多 agent 可以共用一个队列而不互相吞消息。

---

## 12. 判断一个机制是否“新对话”的快速标准

看代码时可以按以下信号判断：

1. 是否调用了 `runAgent()`、`runForkedAgent()` 或 `query()`。
2. 是否创建了新的 `agentId` 或 session ID。
3. 是否构造了新的 `messages[]`，例如 `promptMessages`、`forkContextMessages`、`allMessages`。
4. 是否写 `recordSidechainTranscript()`、agent metadata、remote metadata、team file 或 mailbox。
5. 是否通过 `TaskState`、`RemoteAgentTaskState`、`InProcessTeammateTaskState` 登记生命周期。

反过来，`TeamCreate`、`TaskCreate`、`TaskUpdate`、普通 tool call、普通 Bash background task 本身不算新 LLM 对话。它们可能支撑多智能体协作，但只有真正启动 agent loop 或 remote session 时才算。

---

## 13. 机制关系图

```text
用户主对话
  |
  |-- AgentTool
  |     |-- fresh subagent: runAgent -> query -> sidechain
  |     |-- fork subagent: parent prefix + runAgent -> query -> sidechain
  |     |-- async/background: LocalAgentTask + task-notification
  |     |-- worktree: local agent + isolated git worktree
  |     |-- remote: teleportToRemote -> CCR session -> RemoteAgentTask
  |     `-- teammate: spawnTeammate
  |           |-- tmux/iTerm2 process: new Claude Code process + mailbox
  |           `-- in-process: AsyncLocalStorage + runAgent loop + mailbox
  |
  |-- SkillTool / slash command with context:fork
  |     `-- runAgent -> query -> result returned to parent flow
  |
  |-- Hook system
  |     `-- execAgentHook -> query -> structured result
  |
  |-- Background services
  |     `-- runForkedAgent -> query
  |           |-- compact
  |           |-- memory extraction
  |           |-- prompt suggestion
  |           |-- agent summary
  |           `-- side question
  |
  `-- Remote Control bridge
        `-- Sessions API -> child Claude Code process per session
```

---

## 14. 关键源码索引

| 主题 | 关键文件 |
|---|---|
| AgentTool 入口 | `src/tools/AgentTool/AgentTool.tsx` |
| 子 Agent 编排 | `src/tools/AgentTool/runAgent.ts` |
| fork prompt/cache 逻辑 | `src/tools/AgentTool/forkSubagent.ts` |
| 通用 fork/context 工具 | `src/utils/forkedAgent.ts` |
| local agent task | `src/tasks/LocalAgentTask/LocalAgentTask.tsx` |
| SendMessage resume/通信 | `src/tools/SendMessageTool/SendMessageTool.ts` |
| SkillTool fork | `src/tools/SkillTool/SkillTool.ts` |
| slash command fork | `src/utils/processUserInput/processSlashCommand.tsx` |
| coordinator mode | `src/coordinator/coordinatorMode.ts` |
| teammate spawn | `src/tools/shared/spawnMultiAgent.ts` |
| in-process teammate | `src/utils/swarm/spawnInProcess.ts`、`src/utils/swarm/inProcessRunner.ts` |
| remote agent task | `src/tasks/RemoteAgentTask/RemoteAgentTask.tsx` |
| CCR session 创建 | `src/utils/teleport.tsx` |
| remote-control bridge session | `src/bridge/createSession.ts`、`src/bridge/bridgeMain.ts`、`src/bridge/sessionRunner.ts` |
| hook agent | `src/utils/hooks/execAgentHook.ts` |

