# Codex 多智能体 / 新对话机制调研

调研范围：只要 Codex 在和用户对话过程中额外创建了一个新的 Codex thread/session/conversation，就纳入本文；无论它是完整 fork、部分上下文 fork、空上下文新会话，还是隐藏的内部子会话。仅在同一个 thread 内切换处理流程、没有创建新 Codex 会话的机制，会单独列为“相关但不计入”。

调研对象：`/nfsdat2/home/bcchenslm/Projects/codex`。

## 总览结论

Codex 当前存在多套“额外开启新对话”的机制，核心可以分为四类：

1. 用户对话中的协作子智能体：由 `spawn_agent` / `multi_agent_v1.spawn_agent` 等工具触发，经 `AgentControl` 创建新 thread，可选择 fork 父上下文或从新上下文开始。
2. app-server 级 thread fork：由 `thread/fork`、detached review 等 API 直接调用 `ThreadManager`，创建新的用户可见 thread。
3. 内部 delegated Codex：inline review、guardian review、CSV 批量 worker、memory consolidation 等会创建隐藏或半隐藏子会话，用来完成专门任务。
4. 相关但不算新对话：Realtime 的 `background_agent` 工具名像子智能体，但实际把语音/实时输入路由回同一个 session 的普通 turn，没有新 thread。

最重要的控制面在 `codex-rs/core/src/agent/control.rs`。每棵 agent 树共享一个 `AgentControl`，它负责创建子 agent、记录 agent registry、发送跨 agent 消息、等待/关闭/恢复 agent，并把子 agent 完成状态通知回父 agent。

## 核心数据模型

### Thread / Session / Conversation

Codex 的“新对话”在代码里通常体现为一个新的 `ThreadId` 和新的 `Codex` session。创建入口最终会走到 `ThreadManager` 或 `Codex::spawn`：

- `ThreadManager::start_thread_with_options(...)`：从选项创建新 thread。
- `ThreadManager::fork_thread_with_source(...)`：基于已有 history fork 新 thread。
- `run_codex_thread_interactive(...)`：直接 `Codex::spawn` 一个 delegated 子 Codex，并返回输入/事件通道。

相关实现：

- `codex-rs/core/src/thread_manager.rs`
- `codex-rs/core/src/codex_delegate.rs`
- `codex-rs/core/src/agent/control.rs`

### InitialHistory

新 thread 的历史来源主要有三种：

- `InitialHistory::New`：空历史新会话。
- `InitialHistory::Forked`：从父 thread 的 history fork 出来。
- `InitialHistory::Resumed`：从已持久化 rollout 恢复。

子智能体 full fork 会先 flush 父 rollout，再读取父历史，必要时截断最后 N 个 user turn，然后作为 fork history 创建子 thread。

### SessionSource / ThreadSource / SubAgentSource

这些字段用于标识新会话来源，影响持久化、header、事件、列表展示和内部处理：

- `SessionSource::SubAgent(SubAgentSource::ThreadSpawn { ... })`：工具创建的协作子智能体。
- `SubAgentSource::Review`：inline review delegated Codex。
- `SubAgentSource::Other("guardian")`：guardian review 子会话。
- `SubAgentSource::Other("agent_job:<id>")`：CSV agent job worker。
- `SessionSource::Internal(InternalSessionSource::MemoryConsolidation)`：记忆整理内部会话。
- `ThreadSource::Subagent`、`ThreadSource::MemoryConsolidation` 等用于 thread 层面标记来源。

定义位置：`codex-rs/protocol/src/protocol.rs`。

## 机制一：协作子智能体（AgentControl + spawn_agent）

这是用户对话中最直接的多智能体机制。

### 入口

有两套工具表面：

- V1：`multi_agent_v1.spawn_agent`、`send_input`、`wait_agent`、`resume_agent`、`close_agent`
- V2：`spawn_agent`、`send_message`、`followup_task`、`wait_agent`、`list_agents`、`close_agent`

V1 实现位置：

- `codex-rs/core/src/tools/handlers/multi_agents/spawn.rs`
- `codex-rs/core/src/tools/handlers/multi_agents/send_input.rs`
- `codex-rs/core/src/tools/handlers/multi_agents/wait.rs`
- `codex-rs/core/src/tools/handlers/multi_agents/resume_agent.rs`
- `codex-rs/core/src/tools/handlers/multi_agents/close_agent.rs`

V2 实现位置：

- `codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs`
- `codex-rs/core/src/tools/handlers/multi_agents_v2/message_tool.rs`
- `codex-rs/core/src/tools/handlers/multi_agents_v2/wait.rs`
- `codex-rs/core/src/tools/handlers/multi_agents_v2/list_agents.rs`
- `codex-rs/core/src/tools/handlers/multi_agents_v2/close_agent.rs`

共享工具逻辑：

- `codex-rs/core/src/tools/handlers/multi_agents_common.rs`
- `codex-rs/core/src/tools/handlers/multi_agents_spec.rs`

### 创建流程

通用路径：

1. 工具 handler 解析参数和消息。
2. `build_agent_spawn_config(...)` 从父 turn config 克隆运行配置，包括模型、provider、reasoning、sandbox、approval policy、cwd、base/developer instructions、profile 等。
3. 根据工具参数应用 override。
4. 生成 `SessionSource::SubAgent(SubAgentSource::ThreadSpawn { parent_thread_id, depth, agent_path, agent_nickname, agent_role })`。
5. 调用 `AgentControl::spawn_agent_with_metadata(...)`。
6. `AgentControl` 预留 registry slot，决定是 full/partial fork 还是新 history。
7. 创建 thread 后注册 live agent，发送 `CollabAgentSpawnBegin/End` 事件，并把 initial input 提交给子 agent。

核心实现：`codex-rs/core/src/agent/control.rs`。

### V1 行为

V1 `spawn_agent` 支持：

- `message` 或 `items` 作为初始输入。
- `agent_type`、`model`、`reasoning_effort`、`service_tier` override。
- `fork_context: true`，表示完整 fork 父上下文。

关键限制：

- `fork_context: true` 时会使用 full history fork。
- full fork 不允许同时使用 role/model/reasoning 等 override，因为它要保持与父上下文一致。
- V1 通过返回的 `agent_id` / `nickname` 定位 agent。
- `wait_agent` 等待目标 agent 到达 final status 或 timeout。
- `resume_agent` 可以从已关闭 agent 的 rollout 恢复一个 live 子 agent。

### V2 行为

V2 是更结构化的多智能体接口：

- `spawn_agent` 使用 `task_name` 作为 agent path 的组成部分。
- 默认 `fork_turns = "all"`，即完整 fork 父上下文。
- `fork_turns = "none"` 时从新上下文开始。
- `fork_turns = "<正整数>"` 时只 fork 最近 N 个 user turn。
- full fork 同样不允许 role/model/reasoning override。
- 子 agent 可以继续 spawn subagent，形成 `/root/task/subtask` 这样的路径树。

V2 通信方式也不同：

- `send_message`：发消息到目标 agent mailbox，但不触发新 turn。
- `followup_task`：发消息并触发目标 agent 新 turn。
- `wait_agent`：等待当前 session 的 mailbox 出现新消息，而不是像 V1 那样只等待某个 agent 结束。
- `list_agents`：列出 root 和 live agents，包括 path/status/last task。

V2 的 `spawn_agent` 如果发现目标 canonical path 已存在，并且输入是纯文本，会把初始输入转为 `Op::InterAgentCommunication`，以跨 agent 消息形式唤醒已有 agent。

### 上下文继承

无论 V1 还是 V2，子 agent 都继承父 agent 的大量运行环境：

- turn config 和 model/provider/reasoning 基础配置。
- sandbox / approval / cwd / profile。
- developer instructions 和 compact prompt。
- shell snapshot。
- 对 `ThreadSpawn` 类型子 agent，可能继承父 agent 的 exec policy。

fork 的 history 处理在 `AgentControl::spawn_forked_thread(...)` 中完成：

- 先 flush 父 rollout，避免 fork 到不完整历史。
- 读取父 conversation history。
- full fork 保留完整 history，但会过滤/整理不适合传给子 agent 的 item。
- partial fork 会截取最后 N 个 user turn。
- 通过 `ThreadManager::fork_thread_with_source(...)` 创建新 thread。

### 生命周期与事件

`AgentControl` 配合 `AgentRegistry` 管理 agent 树：

- 限制最大 live thread 数。
- 记录 agent path、nickname、role、depth。
- 跟踪 `AgentStatus`。
- 子 agent 完成后通知父 agent。

子 agent 结束时：

- V2 会向父 agent 发送 `InterAgentCommunication`。
- 非 V2 路径会向父 agent 注入一条 user message，但不自动开始新 turn。

相关文件：

- `codex-rs/core/src/agent/registry.rs`
- `codex-rs/core/src/agent/status.rs`
- `codex-rs/protocol/src/protocol.rs`

## 机制二：app-server `thread/fork`

`thread/fork` 是 app-server 暴露的通用 fork API，会基于已有 thread 的持久化 history 创建新的用户可见 thread。

入口：

- `codex-rs/app-server/src/request_processors/thread_processor.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/thread.rs`
- `codex-rs/app-server/README.md`

参数能力包括：

- 指定源 `thread_id`。
- 覆盖 model/provider/service tier/cwd/sandbox/approval/config/instructions。
- 设置 `ephemeral`。
- 设置 `thread_source`。
- 使用 `exclude_turns` 排除尾部若干 turn。

实现路径：

1. 读取源 thread 的 stored rollout / history。
2. 应用 fork override。
3. 调用 `ThreadManager::fork_thread_from_history(...)`。
4. 创建 listener。
5. 返回新 thread 信息，并 emit `thread/started`。

这个机制不是通过 `AgentControl` 的 agent registry 管理，因此它是“新 thread/fork”，但不是协作子 agent 树的一员。

## 机制三：app-server detached review

`review/start` 有 inline 和 detached 两种模式。detached review 会创建一个新的 review thread，因此计入新对话机制。

入口：

- `codex-rs/app-server/src/request_processors/turn_processor.rs`
- `codex-rs/app-server/README.md`

流程：

1. 对父 thread flush rollout。
2. 读取父 history。
3. 使用 review model 或当前 model。
4. 通过 `ThreadManager::fork_thread_from_history(...)` 创建 review thread。
5. emit `thread/started`。
6. 向新 thread 提交 `Op::Review`。

它与 inline review 的区别是：detached review 用户可见，并有独立 `reviewThreadId`；inline review 在原 thread 内触发一个隐藏 delegated Codex。

## 机制四：inline review delegated Codex

inline `/review` 或同类 review task 不创建用户可见 thread，但会启动一个新的子 Codex 会话，因此计入“额外开启新对话”。

入口：

- `codex-rs/core/src/tasks/review.rs`
- `codex-rs/core/src/codex_delegate.rs`

流程：

1. 构造 review 专用 config。
2. 禁用部分能力，例如 web search、CSV spawn、多智能体工具等。
3. 设置 review prompt、approval never、review model。
4. 调用 `run_codex_thread_one_shot(..., SubAgentSource::Review)`。
5. 该函数内部通过 `run_codex_thread_interactive(...)` 调用 `Codex::spawn` 创建 delegated 子 Codex。
6. 向子 Codex 提交初始 `Op::UserInput`，等待其完成后 shutdown。

该子会话的事件会被过滤和转发：`TokenCount`、`SessionConfigured` 等不会原样冒出；权限请求等会路由回父 agent 或 guardian。

## 机制五：guardian review session

Guardian 是另一个内部 review 子会话机制，用于在高风险操作前做自动审查。它不是普通用户手动 spawn 的协作 agent，但会创建新的 Codex session。

入口：

- `codex-rs/core/src/guardian/mod.rs`
- `codex-rs/core/src/guardian/review.rs`
- `codex-rs/core/src/guardian/review_session.rs`
- `codex-rs/core/src/codex_delegate.rs`

特点：

- 使用 locked-down 的 guardian config。
- 维护一个 reusable trunk review session。
- 当 trunk 忙时可创建 ephemeral fork/review session。
- session source 使用 `SubAgentSource::Other("guardian")`。
- 通过 `run_codex_thread_interactive(...)` 创建 Codex 子会话。

这类会话主要用于内部审批/审查流程，用户通常不会把它当作一个协作 agent 操作，但从实现上它确实是额外的 Codex 对话。

## 机制六：CSV agent jobs

`spawn_agents_on_csv` 是批量多智能体机制：读取 CSV 后，为每一行创建一个 worker 子 agent，并要求 worker 调用 `report_agent_job_result` 回报结果。

入口：

- `codex-rs/core/src/tools/handlers/agent_jobs_spec.rs`
- `codex-rs/core/src/tools/handlers/agent_jobs.rs`

流程：

1. 工具读取 CSV job 配置。
2. 根据并发限制逐行启动 worker。
3. 每个 worker 通过 `AgentControl::spawn_agent_with_metadata(...)` 创建。
4. source 使用 `SessionSource::SubAgent(SubAgentSource::Other("agent_job:<job_id>"))`。
5. worker 完成或上报结果后，job runner 更新该行状态。

注意：它使用 `AgentControl` 创建新 thread，但 source 不是 `ThreadSpawn`，因此不完全纳入协作子 agent path 树，也不走普通 `task_name` / agent path 语义。

## 机制七：memory consolidation 内部会话

记忆整理会启动内部 Codex 会话来合并/写入 memory。

入口：

- `codex-rs/memories/write/src/runtime.rs`
- `codex-rs/core/src/client.rs`

流程：

1. `spawn_consolidation_agent(...)` 调用 `ThreadManager::start_thread_with_options(...)`。
2. 使用 `InitialHistory::New`。
3. 设置 `SessionSource::Internal(InternalSessionSource::MemoryConsolidation)`。
4. 设置 `ThreadSource::MemoryConsolidation`。
5. 提交 consolidation prompt。

这是内部后台 thread，不属于用户显式可操作的 agent 树，但符合“额外开启新对话”的定义。

## 相关但不计入：Realtime `background_agent`

Realtime WebSocket V2 暴露了名为 `background_agent` 的 function tool，并把模型调用解析为 `RealtimeEvent::HandoffRequested`。

相关文件：

- `codex-rs/codex-api/src/endpoint/realtime_websocket/protocol_v2.rs`
- `codex-rs/codex-api/src/endpoint/realtime_websocket/methods_v2.rs`
- `codex-rs/core/src/realtime_conversation.rs`
- `codex-rs/core/src/session/mod.rs`

但它不是新的 Codex thread。实际流程是：

1. realtime 收到 handoff 文本。
2. 调用 `Session::route_realtime_text_input(text)`。
3. 在同一个 session 内提交普通 `Op::UserInput` turn。
4. 输出通过 realtime handoff 通道镜像回实时会话。

因此它是“把实时输入交给当前 Codex session 处理”，不是 fork 或子智能体。

## 横向对比

| 机制 | 触发入口 | 是否新 thread/session | history 来源 | 是否用户可见 | 管理者 |
| --- | --- | --- | --- | --- | --- |
| V1 `multi_agent_v1.spawn_agent` | 工具调用 | 是 | New 或 Full fork | 是，作为协作子 agent | `AgentControl` |
| V2 `spawn_agent` | 工具调用 | 是 | 默认 Full fork，也可 None/Last N | 是，作为 task path agent | `AgentControl` |
| V1 `resume_agent` | 工具调用 | 是/恢复 live session | Resumed rollout | 是 | `AgentControl` |
| app-server `thread/fork` | RPC | 是 | Forked stored history | 是 | `ThreadManager` |
| detached review | `review/start` detached | 是 | Forked parent history | 是 | `ThreadManager` |
| inline review | `/review` / `Op::Review` inline | 是 | delegated child input | 通常隐藏 | `codex_delegate` |
| guardian review | guardian 审查流程 | 是 | guardian managed history | 隐藏 | `codex_delegate` / guardian |
| CSV agent jobs | `spawn_agents_on_csv` | 是 | worker 初始任务 | 半隐藏/工具管理 | `AgentControl` |
| memory consolidation | memory runtime | 是 | New | 隐藏 | `ThreadManager` |
| realtime `background_agent` | Realtime function call | 否 | 同 session turn | 用户可见为 realtime handoff | 当前 `Session` |

## V1 与 V2 协作子智能体的关键差异

V1 更像“按 thread id 操作的并行 agent”：

- 返回 `agent_id` 和 nickname。
- `send_input` 直接给目标 thread 发新输入。
- `wait_agent` 等待目标 agent 完成。
- 支持 `resume_agent`。

V2 更像“任务树 + mailbox”：

- 用 `task_name` 形成 canonical path。
- 默认 fork 全部上下文。
- 支持 `fork_turns = none/all/N`。
- `send_message` 和 `followup_task` 区分“留言”和“触发任务”。
- `wait_agent` 等待 mailbox 消息，而不是单纯等待 target 结束。
- 可以 `list_agents` 查看 root 和 live task agents。

## 事件与通信模型

### 协作事件

协议里定义了一组 collab event，用于 UI 或上层系统感知 agent 行为：

- `CollabAgentSpawnBegin`
- `CollabAgentSpawnEnd`
- `CollabAgentInteractionBegin`
- `CollabAgentInteractionEnd`
- `CollabAgentWaitingBegin`
- `CollabAgentWaitingEnd`
- `CollabAgentCloseBegin`
- `CollabAgentCloseEnd`
- `CollabAgentStatusEntry`

这些定义在 `codex-rs/protocol/src/protocol.rs`。

### 跨 agent 消息

`Op::InterAgentCommunication` 承载 agent 间消息：

- `author`
- `recipient`
- `other_recipients`
- `content`
- `trigger_turn`

V2 的 `send_message` / `followup_task` 主要基于这个结构；子 agent 完成时，父 agent 也会收到类似 inter-agent notification。

## 实现边界和注意点

1. `AgentControl` agent 树限制主要约束工具 spawn 的协作子 agent；app-server `thread/fork` 是 thread 层 API，不属于同一个 registry 限制面。
2. full fork 会尽量保持父上下文一致，因此禁止 role/model/reasoning override；partial/new history 才允许这些 override。
3. fork 前通常要 flush 父 rollout，否则可能 fork 到不完整 history。
4. delegated 子 Codex 会过滤和转发事件，尤其是权限请求会回到父 agent 或 guardian，而不是完全独立处理。
5. `SubAgentSource::Compact` 在协议枚举中存在，但本次调查没有找到当前 compaction 流程主动创建新 Codex 子会话的路径；它更像历史/兼容或特定 header/source 标记。
6. Realtime `background_agent` 名称容易误导，但代码路径显示它不创建新会话。
7. CSV agent jobs 使用 `SubAgentSource::Other("agent_job:<id>")`，因此它是多 worker 新 thread 机制，但不是普通 ThreadSpawn agent path 树。
8. memory consolidation 是后台内部 thread，通常不应和用户可操作 agent 混为一谈，但从资源和会话生命周期看需要纳入“新对话”盘点。

## 代码索引

重点文件：

- `codex-rs/core/src/agent/control.rs`：协作子 agent 创建、fork、resume、通信、完成通知。
- `codex-rs/core/src/agent/registry.rs`：agent tree registry、live agent 限制、path/nickname 管理。
- `codex-rs/core/src/agent/status.rs`：agent event 到 status 的映射。
- `codex-rs/core/src/tools/handlers/multi_agents/`：V1 多 agent 工具。
- `codex-rs/core/src/tools/handlers/multi_agents_v2/`：V2 多 agent 工具。
- `codex-rs/core/src/tools/handlers/multi_agents_common.rs`：共享 spawn config、source、输入解析。
- `codex-rs/core/src/thread_manager.rs`：thread start/fork/resume 底层能力。
- `codex-rs/app-server/src/request_processors/thread_processor.rs`：`thread/fork`。
- `codex-rs/app-server/src/request_processors/turn_processor.rs`：detached review。
- `codex-rs/core/src/codex_delegate.rs`：delegated child Codex session。
- `codex-rs/core/src/tasks/review.rs`：inline review 子 Codex。
- `codex-rs/core/src/guardian/review_session.rs`：guardian review session。
- `codex-rs/core/src/tools/handlers/agent_jobs.rs`：CSV worker agents。
- `codex-rs/memories/write/src/runtime.rs`：memory consolidation 内部 thread。
- `codex-rs/core/src/session/mod.rs`：realtime handoff 路由到同 session turn。
- `codex-rs/protocol/src/protocol.rs`：`Op::InterAgentCommunication`、`SubAgentSource`、collab events 等协议类型。

