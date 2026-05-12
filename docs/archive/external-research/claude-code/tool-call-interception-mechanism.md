# Claude Code 工具调用检查/干预机制 调研

## 概述

Claude Code 的工具调用干预由三层机制协同实现：**Hooks**（生命周期钩子）→ **Permissions**（规则引擎）→ **Interactive Handler**（交互式裁决）。核心流程：LLM 发出 `tool_use` → 输入校验 → PreToolUse Hooks → 规则检查 + AI 分类器 → 用户/远程审批 → 工具执行 → PostToolUse Hooks → 结果回传 LLM。

---

## 源码位置

### 核心文件

| 文件 | 职责 |
|------|------|
| `src/Tool.ts` (L362-695) | Tool 接口定义，含 `checkPermissions`、`validateInput`、`call` |
| `src/tools.ts` (L193-367) | 工具注册、按权限过滤、工具池组装 |
| `src/services/tools/toolExecution.ts` (L599-1745) | **工具执行总编排器** `checkPermissionsAndCallTool()` |
| `src/services/tools/toolHooks.ts` (L1-651) | Pre/Post ToolUse Hook 包装器 |
| `src/services/tools/toolOrchestration.ts` | 并发/串行工具调度 |
| `src/services/tools/StreamingToolExecutor.ts` | 流式工具执行器 |

### Hook 系统

| 文件 | 职责 |
|------|------|
| `src/schemas/hooks.ts` (L1-222) | Hook Zod Schema（command/prompt/agent/http 四种类型） |
| `src/types/hooks.ts` (L1-291) | HookResult、AggregatedHookResult、同步/异步响应 Schema |
| `src/utils/hooks.ts` (~5000行) | **Hook 执行引擎**，`executeHooks()` 核心调度 |
| `src/utils/hooks/hooksConfigManager.ts` | Hook 事件元数据、matcher 字段定义 |
| `src/utils/hooks/hooksConfigSnapshot.ts` | Hook 配置快照，安全策略门控 |
| `src/utils/hooks/execPromptHook.ts` | LLM prompt hook 执行器 |
| `src/utils/hooks/execAgentHook.ts` | 多轮 Agent hook 执行器 |
| `src/utils/hooks/execHttpHook.ts` | HTTP POST hook 执行器 |
| `src/utils/hooks/AsyncHookRegistry.ts` | 后台异步 hook 管理 |
| `src/utils/hooks/sessionHooks.ts` | 内存中的临时 session hook |
| `src/utils/hooks/fileChangedWatcher.ts` | FileChanged 事件文件监控 |

### 权限系统

| 文件 | 职责 |
|------|------|
| `src/types/permissions.ts` (L1-442) | 权限类型定义：Mode、Rule、Decision、Result |
| `src/utils/permissions/permissions.ts` | `hasPermissionsToUseTool()` 规则检查主函数 |
| `src/utils/permissions/bashClassifier.ts` | Bash 命令安全分类器 |
| `src/utils/permissions/yoloClassifier.ts` | Auto 模式 AI 分类器 |
| `src/utils/permissions/denialTracking.ts` | 拒绝次数追踪（连续3次/总计20次回退） |
| `src/hooks/useCanUseTool.tsx` | React hook，权限门控主入口 |
| `src/hooks/toolPermission/PermissionContext.ts` | 权限上下文工厂 |
| `src/hooks/toolPermission/handlers/interactiveHandler.ts` | 交互式权限对话框（多路竞速） |
| `src/hooks/toolPermission/handlers/coordinatorHandler.ts` | 协调器 Worker 权限处理 |
| `src/hooks/toolPermission/handlers/swarmWorkerHandler.ts` | Swarm Worker 权限处理 |

### 配置与设置

| 文件 | 职责 |
|------|------|
| `src/utils/settings/types.ts` (L255-1073) | SettingsSchema 完整定义 |
| `src/utils/settings/constants.ts` | 设置来源优先级定义 |
| `src/utils/settings/settings.ts` | 设置加载、合并、持久化 |
| `src/entrypoints/sdk/coreSchemas.ts` (L355-383) | `HOOK_EVENTS` 常量定义 |

---

## 核心接口

### Tool 接口 (`src/Tool.ts`)

```typescript
type Tool<Input, Output, P> = {
  name: string
  aliases?: string[]

  // 工具执行（通过权限检查后调用）
  call(args, context, canUseTool, parentMessage, onProgress?): Promise<ToolResult<Output>>

  // 权限检查（每个工具自定义逻辑）
  checkPermissions(input, context): Promise<PermissionResult>

  // 输入校验（Zod 之后、权限之前）
  validateInput?(input, context): Promise<ValidationResult>

  // 判断该调用是否只读（决定是否可并发）
  isReadOnly(input): boolean

  // 判断该调用是否可并发安全执行
  isConcurrencySafe(input): boolean

  // 结果映射回 API 格式
  mapToolResultToToolResultBlockParam(content, toolUseID): ToolResultBlockParam

  // 描述（注入 system prompt）
  description(input, options): Promise<string>

  // 是否启用
  isEnabled(): boolean
}
```

### ToolPermissionContext (`src/types/permissions.ts:427-441`)

```typescript
type ToolPermissionContext = {
  readonly mode: PermissionMode                           // 当前权限模式
  readonly additionalWorkingDirectories: ReadonlyMap<...>  // 额外工作目录
  readonly alwaysAllowRules: ToolPermissionRulesBySource   // 按来源分组的允许规则
  readonly alwaysDenyRules: ToolPermissionRulesBySource    // 按来源分组的拒绝规则
  readonly alwaysAskRules: ToolPermissionRulesBySource     // 按来源分组的询问规则
  readonly isBypassPermissionsModeAvailable: boolean       // 是否可跳过权限
  readonly shouldAvoidPermissionPrompts?: boolean          // 后台 Agent：避免弹窗
  readonly awaitAutomatedChecksBeforeDialog?: boolean      // 协调器：先等自动化检查
}
```

### PermissionResult (`src/types/permissions.ts:251-266`)

```typescript
type PermissionBehavior = 'allow' | 'deny' | 'ask'

type PermissionResult<Input> =
  | { behavior: 'allow', updatedInput?, decisionReason? }   // 允许（可修改输入）
  | { behavior: 'deny',  message, decisionReason }          // 拒绝
  | { behavior: 'ask',   message, suggestions?, pendingClassifierCheck? }  // 需要用户确认
  | { behavior: 'passthrough', message, ... }               // 未决定，传递给下一层
```

### PermissionDecisionReason (`src/types/permissions.ts:271-324`)

```typescript
type PermissionDecisionReason =
  | { type: 'rule',     rule: PermissionRule }         // 来自 settings.json 规则
  | { type: 'mode',     mode: PermissionMode }         // 来自权限模式
  | { type: 'hook',     hookName, hookSource?, reason? } // 来自 Hook 决定
  | { type: 'classifier', classifier, reason }         // 来自 AI 分类器
  | { type: 'safetyCheck', reason, classifierApprovable } // 安全检查
  | { type: 'asyncAgent', reason }                     // 异步 Agent 决定
  | { type: 'sandboxOverride', reason }                // 沙箱覆盖
  | { type: 'workingDir', reason }                     // 工作目录相关
  | { type: 'subcommandResults', reasons: Map<...> }   // 子命令聚合
```

### Hook Schema (`src/schemas/hooks.ts`)

```typescript
// 四种 Hook 类型（通过 type 字段区分的联合类型）
type HookCommand =
  | { type: 'command', command: string, if?: string, shell?, timeout?, async?, once? }
  | { type: 'prompt',  prompt: string, if?: string, timeout?, model?, once? }
  | { type: 'agent',   prompt: string, if?: string, timeout?, model?, once? }
  | { type: 'http',    url: string,    if?: string, timeout?, headers?, once? }

// Matcher: 按工具名匹配
type HookMatcher = {
  matcher?: string           // e.g. "Bash", "Write"
  hooks: HookCommand[]
}

// 顶层配置: 事件 -> Matcher 数组
type HooksSettings = Partial<Record<HookEvent, HookMatcher[]>>
```

### Hook 同步响应 Schema (`src/types/hooks.ts:49-166`)

```typescript
type SyncHookResponse = {
  continue?: boolean            // 默认 true；false 则阻止模型继续
  suppressOutput?: boolean      // 隐藏 stdout
  stopReason?: string           // continue=false 时的理由
  decision?: 'approve' | 'block'
  reason?: string
  systemMessage?: string        // 显示给用户的警告
  hookSpecificOutput?: {
    // PreToolUse:
    { hookEventName: 'PreToolUse',
      permissionDecision?: 'allow'|'deny'|'ask',
      updatedInput?: Record<string, unknown>,
      additionalContext?: string }
    // PostToolUse:
    | { hookEventName: 'PostToolUse',
        additionalContext?, updatedMCPToolOutput? }
    // PermissionRequest:
    | { hookEventName: 'PermissionRequest',
        decision: { behavior: 'allow'|'deny', ... } }
    // ... 每种事件有不同字段
  }
}
```

---

## 与 Agent 主循环的集成

### 完整执行流程

```
┌─────────────────────────────────────────────────────────────────┐
│                    LLM API (Streaming Response)                  │
│                     提取 tool_use blocks                         │
└───────────────────────┬─────────────────────────────────────────┘
                        │
          ┌─────────────┴──────────────┐
          │                            │
   [流式模式]                    [批量模式]
 StreamingToolExecutor          runTools()
   .addTool()                 等所有 blocks 到齐
          │                            │
          └─────────────┬──────────────┘
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│  Phase 1: toolExecution.ts:runToolUse()                          │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ 1. findToolByName(tools, toolName) + alias fallback       │  │
│  │ 2. 未知工具 → 返回 is_error: true 的 tool_result          │  │
│  │ 3. AbortController 检查                                   │  │
│  └────────────────────────────────────────────────────────────┘  │
                        │
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│  Phase 2: checkPermissionsAndCallTool()                          │
│                                                                  │
│  Step 1: Zod Schema 验证 (inputSchema.safeParse)                 │
│          ↓ 失败 → InputValidationError tool_result               │
│                                                                  │
│  Step 2: tool.validateInput() — 工具特定校验                      │
│          ↓ 失败 → 错误 tool_result                               │
│                                                                  │
│  Step 3: 启动 Bash 分类器（并行，与 hooks 同时）                    │
│                                                                  │
│  Step 4: runPreToolUseHooks() ← 【PreToolUse Hooks】             │
│          ├─ hookPermissionResult: allow/deny/ask                 │
│          ├─ hookUpdatedInput: 修改工具输入（不改权限）              │
│          ├─ preventContinuation: 阻止继续                        │
│          └─ additionalContext: 注入上下文                         │
│                                                                  │
│  Step 5: resolveHookPermissionDecision()                         │
│          ├─ Hook deny  → 最终拒绝（不可覆盖）                      │
│          ├─ Hook allow → 仍需检查 settings.json deny/ask 规则     │
│          └─ Hook ask/无 → 传递给 canUseTool()                    │
│                                                                  │
│  Step 6: canUseTool() → hasPermissionsToUseTool()                │
│          ├─ 1a. Deny rules  → 直接拒绝                           │
│          ├─ 1b. Ask rules   → 强制询问                           │
│          ├─ 1c. tool.checkPermissions() → 工具自定逻辑            │
│          ├─ 1d. 工具拒绝    → 拒绝                               │
│          ├─ 1e. 需用户交互  → 询问                               │
│          ├─ 1f. 内容级 ask  → 询问                               │
│          ├─ 1g. 安全检查    → .git/.claude/shell configs          │
│          ├─ 2a. bypass 模式 → 允许（跳过以上所有）                 │
│          ├─ 2b. Always-allowed rules → 前缀匹配允许               │
│          └─ 3.  默认 → ask                                       │
│                                                                  │
│  Step 6b: 后处理                                                 │
│          ├─ Auto 模式 → AI 分类器（2 阶段：fast + thinking）      │
│          ├─ dontAsk 模式 → ask 自动转 deny                       │
│          └─ Headless Agent → 自动 deny                          │
│                                                                  │
│  Step 7: Interactive Handler（behavior='ask' 时）                 │
│          四路竞速:                                                │
│          ├─ 本地用户对话框 (React queue)                          │
│          ├─ Bridge (claude.ai CCR 远程审批)                      │
│          ├─ Channel relay (Telegram/iMessage)                    │
│          └─ PermissionRequest Hooks + Bash 分类器                │
│          使用 createResolveOnce() 原子化：先到先得                 │
│                                                                  │
│  Step 8: 权限被拒 → 返回 is_error: true + PermissionDenied Hooks  │
│                                                                  │
│  Step 9: tool.call(input, context, canUseTool, ...) ← 实际执行   │
│                                                                  │
│  Step 10: runPostToolUseHooks() ← 【PostToolUse Hooks】          │
│           ├─ additionalContext: 注入上下文                       │
│           ├─ updatedMCPToolOutput: 修改 MCP 工具输出              │
│           └─ preventContinuation: 阻止继续                      │
│                                                                  │
│  Step 11: 工具错误时 → runPostToolUseFailureHooks()               │
│                                                                  │
│  Step 12: tool.mapToolResultToToolResultBlockParam()              │
│           → 创建 UserMessage(type: 'tool_result')                │
└──────────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│  query.ts: 收集所有 tool_result → 回传 LLM → 下一轮对话           │
└──────────────────────────────────────────────────────────────────┘
```

### 拦截消息如何反馈给 LLM

1. **权限拒绝**：返回 `tool_result` content block，`is_error: true`，message 中包含拒绝原因
2. **Hook 阻止**（exit code 2）：stderr 内容作为 `tool_result` 发送给 LLM，LLM 可据此调整策略
3. **Hook allow 但 settings deny**：Hook 的 allow 不覆盖 settings.json 中的 deny/ask 规则
4. **additionalContext**：PostToolUse hook 可注入额外上下文到对话中，LLM 在后续推理中可见
5. **preventContinuation**：设置 `stopReason`，阻止 LLM 继续本轮推理

---

## Hook 事件清单（27 个）

| 事件 | Matcher 字段 | 说明 |
|------|-------------|------|
| `PreToolUse` | `tool_name` | 工具执行前。可 allow/deny/ask/修改输入 |
| `PostToolUse` | `tool_name` | 工具执行成功后。可修改 MCP 输出、注入上下文 |
| `PostToolUseFailure` | `tool_name` | 工具执行失败后 |
| `PermissionRequest` | `tool_name` | 权限对话框弹出时。可程序化 approve/deny |
| `PermissionDenied` | `tool_name` | Auto 模式分类器拒绝后。可标记 retry |
| `UserPromptSubmit` | — | 用户提交 prompt 时 |
| `SessionStart` | `source` | 会话开始 |
| `SessionEnd` | `reason` | 会话结束 |
| `Stop` | — | 模型停止生成时 |
| `StopFailure` | `error` | 模型生成出错 |
| `SubagentStart` | `agent_type` | 子 Agent 启动 |
| `SubagentStop` | `agent_type` | 子 Agent 停止 |
| `PreCompact` | `trigger` | 上下文压缩前 |
| `PostCompact` | `trigger` | 上下文压缩后 |
| `Notification` | `notification_type` | 通知事件 |
| `Setup` | `trigger` | 初始化/维护 |
| `ConfigChange` | `source` | 配置变更 |
| `FileChanged` | 文件名 | 文件内容变更 |
| `CwdChanged` | — | 工作目录切换 |
| `Elicitation` | `mcp_server_name` | MCP 交互请求 |
| `ElicitationResult` | — | MCP 交互结果 |
| `TaskCreated` | — | 任务创建 |
| `TaskCompleted` | — | 任务完成 |
| `TeammateIdle` | — | 协作成员空闲 |
| `WorktreeCreate` | — | 工作树创建 |
| `WorktreeRemove` | — | 工作树移除 |
| `InstructionsLoaded` | `load_reason` | 指令加载完成 |

---

## 用户扩展方式

### 1. settings.json 配置 Hooks

**配置文件位置**（优先级从低到高）：

| 来源 | 路径 |
|------|------|
| 用户全局 | `~/.claude/settings.json` |
| 项目共享 | `<project>/.claude/settings.json` |
| 项目本地 | `<project>/.claude/settings.local.json` |
| CLI 标志 | `--settings <path>` |
| 企业策略 | managed-settings.json / MDM / HKCU |

**配置示例**：

```jsonc
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "echo 'Bash command about to run' && exit 0",
            "timeout": 5
          }
        ]
      },
      {
        "matcher": "Write",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/lint-checker.sh",
            "if": "Write(*.ts)"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "http",
            "url": "https://audit.internal/tool-usage",
            "headers": { "Authorization": "Bearer $AUDIT_TOKEN" },
            "allowedEnvVars": ["AUDIT_TOKEN"]
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "prompt",
            "prompt": "Review this user prompt for safety: $ARGUMENTS. Respond with {\"ok\": true} or {\"ok\": false, \"reason\": \"...\"}",
            "model": "claude-haiku-4-5"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "agent",
            "prompt": "Verify that all unit tests passed. $ARGUMENTS",
            "once": true
          }
        ]
      }
    ]
  },
  "permissions": {
    "allow": ["Bash(git:*)", "Read", "Glob", "Grep"],
    "deny": ["Bash(rm -rf:*)"]
  }
}
```

### 2. 权限规则语法

```
<ToolName>          — 匹配该工具的所有调用
<ToolName>(pattern) — 匹配该工具的特定内容模式
Bash(git:*)         — 匹配所有 git 开头的 Bash 命令
Write(*.ts)         — 匹配写 .ts 文件的 Write 调用
```

### 3. 权限模式

| 模式 | 行为 |
|------|------|
| `default` | 每次新操作询问用户 |
| `auto` | AI 分类器自动决定（2 阶段：fast + thinking） |
| `acceptEdits` | 自动允许文件编辑，其他询问 |
| `bypassPermissions` | 跳过所有权限检查 |
| `dontAsk` | 询问自动转拒绝 |
| `plan` | 仅允许只读操作 + 计划工具 |

### 4. Hook 的 `if` 条件

使用权限规则语法过滤，只有匹配时 hook 才执行：

```jsonc
{ "type": "command", "command": "check.sh", "if": "Bash(npm:*)" }
// 只在 npm 开头的 Bash 命令时触发
```

### 5. 异步 Hook

```jsonc
{ "type": "command", "command": "notify.sh", "async": true }
// 后台运行，不阻塞工具执行

{ "type": "command", "command": "watch.sh", "asyncRewake": true }
// 后台运行，exit code 2 时唤醒模型（blocking error）
```

---

## 关键设计决策

### 1. Hook allow 不覆盖 settings deny（安全分层）

```
Hook say allow → 仍检查 settings.json deny/ask 规则
Hook say deny  → 最终拒绝，不可覆盖
```

**设计意图**：企业策略（policySettings）的 deny 规则是硬约束，即使用户在 hook 中写了 "allow" 也无法绕过。这防止了 hook 被用于绕过安全管理。

**文件**：`src/services/tools/toolHooks.ts:332-433` `resolveHookPermissionDecision()`

### 2. 四路竞速权限裁决

交互式权限处理同时启动 4 个路径（`src/hooks/toolPermission/handlers/interactiveHandler.ts:57-531`）：
1. 本地用户对话框
2. claude.ai 远程审批（Bridge/CCR）
3. 外部通道中继（Telegram/iMessage）
4. PermissionRequest Hooks + Bash 分类器

使用原子化 `createResolveOnce().claim()` 确保"先到先得"——任何一路先响应就生效。

### 3. AI 分类器作为权限裁决者

Auto 模式使用 2 阶段分类器（`src/utils/permissions/yoloClassifier.ts`）：
- **Stage 1 (fast)**：快速判断，低 token 消耗
- **Stage 2 (thinking)**：深度推理，处理 stage 1 不确定的情况

有拒绝次数追踪：连续 3 次或总计 20 次拒绝后回退到用户提示模式。

### 4. 工具执行前后的 Hook 输入输出模型

**输入**：Hook 收到的 JSON 包含完整上下文（tool_name、tool_input、session_id、conversation 信息等）

**输出**：结构化 JSON，通过 `hookSpecificOutput` 字段区分事件类型，不同事件有不同的输出能力：
- PreToolUse：`permissionDecision`、`updatedInput`、`additionalContext`
- PostToolUse：`updatedMCPToolOutput`、`additionalContext`
- PermissionRequest：`decision: { behavior, ... }`

**Exit code 语义**：
- 0：成功
- 2：blocking error（stderr 展示给模型，工具调用被阻止）
- 其他：non-blocking error（stderr 仅展示给用户）

### 5. 与常见 Guardrail 模式的异同

| 特性 | Claude Code | 典型 Guardrail 框架 |
|------|-------------|-------------------|
| 拦截点 | PreToolUse + PostToolUse | 通常只有 LLM 输入/输出层 |
| 决策者 | 规则 + AI 分类器 + 用户 + 远程 + Hook | 通常只有规则/分类器 |
| 修改能力 | Hook 可修改工具输入 | 通常只 allow/deny |
| 异步能力 | 支持 async/asyncRewake | 通常同步 |
| 安全分层 | Hook allow < settings deny < policy deny | 通常扁平 |
| 执行器类型 | Shell/LLM/Agent/HTTP/Callback | 通常只有 Shell |
| MCP 集成 | PostToolUse 可修改 MCP 输出 | 不涉及 |

### 6. 并发安全模型

工具分为 concurrency-safe（如 Glob、Grep、Read 等只读工具）和非安全两类。安全工具可并发执行（上限 10），非安全工具串行。流式模式下，安全工具到达即执行。

**文件**：`src/services/tools/toolOrchestration.ts`

### 7. 企业管控能力

- `disableAllHooks`：策略级 kill switch
- `allowManagedHooksOnly`：只允许企业管理的 hook
- `allowManagedPermissionRulesOnly`：只允许企业管理的权限规则
- `strictPluginOnlyCustomization`：限制插件自定义范围
- 工作区信任门控：未信任工作区所有 hook 被禁用
