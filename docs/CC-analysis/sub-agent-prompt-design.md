# Claude Code 子智能体提示词设计详解

> 本文档基于 Claude Code 源码，全面剖析其子智能体（Sub-agent）系统的提示词设计架构、各智能体的提示词策略、组装流水线及关键设计模式。

---

## 目录

1. [架构概览](#1-架构概览)
2. [类型系统](#2-类型系统)
3. [六种内置智能体提示词详解](#3-六种内置智能体提示词详解)
4. [Fork 子智能体系统](#4-fork-子智能体系统)
5. [提示词组装流水线](#5-提示词组装流水线)
6. [父模型可见的工具描述](#6-父模型可见的工具描述)
7. [自定义智能体加载](#7-自定义智能体加载)
8. [智能体注册与 Feature Gate](#8-智能体注册与-feature-gate)
9. [七大设计模式总结](#9-七大设计模式总结)

---

## 1. 架构概览

整个子智能体系统采用**分层架构**，核心代码位于 `src/tools/AgentTool/` 目录下：

```
src/tools/AgentTool/
├── built-in/
│   ├── generalPurposeAgent.ts   # 通用智能体
│   ├── exploreAgent.ts          # 搜索探索智能体
│   ├── planAgent.ts             # 规划智能体
│   ├── verificationAgent.ts     # 验证智能体
│   ├── claudeCodeGuideAgent.ts  # 文档查询智能体
│   └── statuslineSetup.ts       # 状态栏配置智能体
├── AgentTool.tsx                 # Agent 工具主入口（Schema、分发）
├── prompt.ts                     # 父模型可见的工具描述
├── runAgent.ts                   # 智能体编排与系统提示词组装
├── forkSubagent.ts               # Fork 子智能体系统
├── loadAgentsDir.ts              # 智能体定义类型系统与加载
├── builtInAgents.ts              # 内置智能体注册表（含 Feature Gate）
└── constants.ts                  # 工具名常量与一次性智能体类型
```

**辅助文件：**

- `src/constants/prompts.ts` — `DEFAULT_AGENT_PROMPT`、`enhanceSystemPromptWithEnvDetails` 环境信息增强
- `src/utils/model/agent.ts` — 智能体模型解析

---

## 2. 类型系统

### 2.1 联合类型定义

所有智能体都遵循 `AgentDefinition` 联合类型（`loadAgentsDir.ts:162`）：

```typescript
export type AgentDefinition =
  | BuiltInAgentDefinition    // 内置智能体
  | CustomAgentDefinition     // 用户自定义（.claude/agents/*.md）
  | PluginAgentDefinition     // 插件注册
```

### 2.2 基础字段 (`BaseAgentDefinition`)

```typescript
type BaseAgentDefinition = {
  agentType: string                    // 唯一标识名（如 "Explore", "verification"）
  whenToUse: string                    // 给父模型的描述，决定何时使用该智能体
  tools?: string[]                     // 工具白名单
  disallowedTools?: string[]           // 工具黑名单
  skills?: string[]                    // 启动时预加载的技能
  mcpServers?: AgentMcpServerSpec[]    // 智能体专属 MCP 服务器
  hooks?: HooksSettings                // 会话级钩子
  color?: AgentColorName               // UI 颜色标识
  model?: string                       // 模型覆盖（'haiku' | 'sonnet' | 'inherit' | 具体 ID）
  effort?: EffortValue                 // 推理努力级别
  permissionMode?: PermissionMode      // 权限模式覆盖
  maxTurns?: number                    // 最大 Agentic 轮数
  omitClaudeMd?: boolean               // 是否省略 CLAUDE.md 注入（节省 token）
  criticalSystemReminder_EXPERIMENTAL?: string  // 每轮重新注入的强化提示
  memory?: AgentMemoryScope            // 持久记忆范围
  background?: boolean                 // 是否始终后台运行
  isolation?: 'worktree' | 'remote'   // 隔离模式
  initialPrompt?: string               // 首轮用户消息前追加的内容
}
```

### 2.3 三种子类型的差异

| 类型 | 提示词来源 | 上下文注入 |
|------|-----------|-----------|
| `BuiltInAgentDefinition` | `getSystemPrompt({ toolUseContext })` 函数动态生成 | 可访问运行时上下文（技能、MCP、配置） |
| `CustomAgentDefinition` | Markdown 正文或 JSON `prompt` 字段，闭包持有 | 仅在启用 memory 时追加记忆提示 |
| `PluginAgentDefinition` | 同 Custom，通过闭包持有 | 同 Custom |

---

## 3. 六种内置智能体提示词详解

### 3.1 General-Purpose（通用智能体）

**文件**: `built-in/generalPurposeAgent.ts`

**定位**: 最精简的全能型智能体，几乎全权委托。

**系统提示词结构**:

```
SHARED_PREFIX: "You are an agent for Claude Code... Complete the task fully—
                don't gold-plate, but don't leave it half-done."

SHARED_GUIDELINES:
  - 搜索代码、配置和模式
  - 分析多文件理解架构
  - 调查需要探索多文件的复杂问题
  - 执行多步骤研究任务
  
  指导原则：
  - 搜索时先广后窄
  - 不要创建不必要的文件
  - 不要主动创建文档文件

尾部追加: 完成任务后返回简洁报告
```

**配置特点**:

| 字段 | 值 | 说明 |
|------|-----|------|
| `tools` | `['*']` | 所有工具 |
| `model` | 未指定 | 使用 `getDefaultSubagentModel()` 默认值 |
| `omitClaudeMd` | 未指定 | 保留 CLAUDE.md |

**设计哲学**: 信任模型能力，最少约束，最大灵活性。提示词仅说明"你是 Claude Code 的智能体"，然后列出核心指导原则，不做过多限制。

---

### 3.2 Explore（搜索探索智能体）

**文件**: `built-in/exploreAgent.ts`

**定位**: 只读文件搜索专家，强调速度。

**系统提示词关键段落**:

```
=== CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS ===
This is a READ-ONLY exploration task. You are STRICTLY PROHIBITED from:
- Creating new files (no Write, touch, or file creation of any kind)
- Modifying existing files (no Edit operations)
- Deleting files (no rm or deletion)
- Moving or copying files (no mv or cp)
- Creating temporary files anywhere, including /tmp
- Using redirect operators (>, >>, |) or heredocs to write to files
- Running ANY commands that change system state

Your role is EXCLUSIVELY to search and analyze existing code.
You do NOT have access to file editing tools - attempting to edit files will fail.
```

**速度优化提示**:

```
NOTE: You are meant to be a fast agent that returns output as quickly as possible.
- Make efficient use of the tools that you have at your disposal
- Wherever possible you should try to spawn multiple parallel tool calls
```

**配置特点**:

| 字段 | 值 | 说明 |
|------|-----|------|
| `disallowedTools` | Agent, ExitPlanMode, FileEdit, FileWrite, NotebookEdit | 严格只读 |
| `model` | `'haiku'`（外部） / `'inherit'`（Ant 内部） | 外部用 Haiku 提速 |
| `omitClaudeMd` | `true` | 省略 CLAUDE.md 节省 token |

**设计哲学**: 通过**双重约束**确保只读——既在提示词中明确禁止，又从工具列表中移除编辑能力。使用 `haiku` 模型追求速度。`omitClaudeMd: true` 因为 Explore 不需要 commit/PR/lint 规则，主智能体有完整 CLAUDE.md 并负责解释结果。

---

### 3.3 Plan（规划智能体）

**文件**: `built-in/planAgent.ts`

**定位**: 只读软件架构规划专家，输出结构化实施计划。

**系统提示词结构化流程**:

```
## Your Process

1. **Understand Requirements**: 关注需求，应用指定的设计视角
2. **Explore Thoroughly**:
   - 读取初始提示中的文件
   - 用 Glob/Grep/Read 查找现有模式和约定
   - 理解当前架构
   - 追踪相关代码路径
   - Bash 仅用于只读操作
3. **Design Solution**: 基于分配的视角创建实施方案
4. **Detail the Plan**: 分步实施策略，识别依赖关系和排序

## Required Output

### Critical Files for Implementation
- path/to/file1.ts
- path/to/file2.ts
- path/to/file3.ts
```

**配置特点**:

| 字段 | 值 | 说明 |
|------|-----|------|
| `disallowedTools` | 同 Explore | 只读 |
| `tools` | `EXPLORE_AGENT.tools` | 复用 Explore 的工具集 |
| `model` | `'inherit'` | 继承主模型，保持推理能力 |
| `omitClaudeMd` | `true` | 同 Explore |

**设计哲学**: 与 Explore 共享只读约束，但用 `inherit` 模型保留更强的推理能力。强制结构化输出格式（"Critical Files for Implementation"），确保返回的计划对后续实施有实际价值。

---

### 3.4 Verification（验证智能体）

**文件**: `built-in/verificationAgent.ts`

**定位**: **对抗性验证专家**——不是确认实现正确，而是试图打破它。这是提示词最长的内置智能体（约 130 行）。

**系统提示词核心设计**:

#### a) 开篇定调：两种已知的失败模式

```
You are a verification specialist. Your job is not to confirm the implementation
works — it's to try to break it.

You have two documented failure patterns:
1. Verification avoidance: 面对检查时找理由不执行——读代码、叙述你会测什么、
   写 "PASS"、然后跳过
2. Being seduced by the first 80%: 看到 polished UI 或通过的测试套件就倾向通过，
   没注意一半按钮没功能、状态刷新后消失、后端遇到错误输入崩溃
```

#### b) 按变更类型的验证策略矩阵

```
**Frontend changes**: 启动 dev server → 检查浏览器自动化工具 → 导航、截图、
  点击、读控制台 → curl 页面子资源 → 运行前端测试
**Backend/API changes**: 启动 server → curl/fetch 端点 → 验证响应形状
  → 测试错误处理 → 检查边界情况
**Bug fixes**: 重现原始 bug → 验证修复 → 回归测试 → 检查副作用
**Database migrations**: 运行 migration up → 验证 schema → 运行 migration down
  → 测试现有数据
...（覆盖 10+ 种变更类型）
```

#### c) "识别你自己的合理化借口"部分

```
=== RECOGNIZE YOUR OWN RATIONALIZATIONS ===
- "The code looks correct based on my reading" — 阅读不是验证。运行它。
- "The implementer's tests already pass" — 实现者是 LLM。独立验证。
- "This is probably fine" — "大概"不等于已验证。运行它。
- "I don't have a browser" — 你检查了 mcp__claude-in-chrome__* / mcp__playwright__* 吗？
- "This would take too long" — 这不是你能决定的。
```

#### d) 对抗性探测要求

```
- **Concurrency**: 并行请求到 create-if-not-exists 路径
- **Boundary values**: 0, -1, 空字符串, 超长字符串, unicode, MAX_INT
- **Idempotency**: 同一变更请求发两次
- **Orphan operations**: 删除/引用不存在的 ID
```

#### e) 强制结构化输出

```
### Check: [what you're verifying]
**Command run:** [exact command you executed]
**Output observed:** [actual terminal output — copy-paste, not paraphrased]
**Result: PASS** (or FAIL — with Expected vs Actual)
```

必须以以下之一结束（由调用方解析）：

```
VERDICT: PASS
VERDICT: FAIL
VERDICT: PARTIAL
```

**配置特点**:

| 字段 | 值 | 说明 |
|------|-----|------|
| `disallowedTools` | Agent, ExitPlanMode, FileEdit, FileWrite, NotebookEdit | 禁止修改项目 |
| `model` | `'inherit'` | 继承主模型 |
| `background` | `true` | 始终后台运行 |
| `color` | `'red'` | 红色标识 |
| `criticalSystemReminder_EXPERIMENTAL` | 见下方 | 每轮强化注入 |

**关键强化提醒**（每轮重新注入）:

```
CRITICAL: This is a VERIFICATION-ONLY task. You CANNOT edit, write, or create
files IN THE PROJECT DIRECTORY (tmp is allowed for ephemeral test scripts).
You MUST end with VERDICT: PASS, VERDICT: FAIL, or VERDICT: PARTIAL.
```

**设计哲学**: 这是提示词工程的杰作——通过**心理学对抗**设计（"识别你的借口"）、**明确的失败模式警告**、**强制性的结构化输出**和**每轮强化注入**四重手段，对抗 LLM 在验证场景中的天然倾向（回避验证、被表面现象欺骗）。

---

### 3.5 Claude Code Guide（文档查询智能体）

**文件**: `built-in/claudeCodeGuideAgent.ts`

**定位**: 文档查询专家，帮助用户理解和使用 Claude Code、Agent SDK 和 Claude API。

**系统提示词结构**:

```
**Your expertise spans three domains:**
1. **Claude Code** (the CLI tool): 安装、配置、hooks、skills、MCP...
2. **Claude Agent SDK**: 构建自定义 AI agent 的框架
3. **Claude API**: Messages API、tool use、vision 等

**Documentation sources:**
- Claude Code docs: https://code.claude.com/docs/en/claude_code_docs_map.md
- Claude Agent SDK docs: https://platform.claude.com/llms.txt
- Claude API docs: https://platform.claude.com/llms.txt

**Approach:**
1. 判断用户问题属于哪个领域
2. 用 WebFetch 获取对应文档地图
3. 从地图中识别相关文档 URL
4. 获取具体文档页面
5. 提供清晰、可操作的指导
```

**动态上下文注入**（最独特的设计）:

`getSystemPrompt` 函数在运行时动态读取用户当前配置，注入到提示词中：

```typescript
getSystemPrompt({ toolUseContext }) {
  const contextSections: string[] = []

  // 1. 用户自定义技能
  const customCommands = commands.filter(cmd => cmd.type === 'prompt')
  // → "Available custom skills in this project: ..."

  // 2. .claude/agents/ 中的自定义智能体
  const customAgents = agentDefinitions.filter(a => a.source !== 'built-in')
  // → "Available custom agents configured: ..."

  // 3. MCP 服务器
  const mcpClients = toolUseContext.options.mcpClients
  // → "Configured MCP servers: ..."

  // 4. 插件技能
  const pluginCommands = commands.filter(cmd => cmd.source === 'plugin')
  // → "Available plugin skills: ..."

  // 5. 用户 settings.json
  const settings = getSettings_DEPRECATED()
  // → "User's settings.json: { ... }"

  // 合并到提示词
  if (contextSections.length > 0) {
    return basePrompt + '\n---\n# User\'s Current Configuration\n...'
  }
}
```

**配置特点**:

| 字段 | 值 | 说明 |
|------|-----|------|
| `tools` | Glob, Grep, Read, WebFetch, WebSearch | 搜索 + 网络访问（无编辑） |
| `model` | `'haiku'` | 快速查询用轻量模型 |
| `permissionMode` | `'dontAsk'` | 不请求权限 |

**设计哲学**: 唯一的**动态提示词**智能体——运行时注入用户当前配置（技能、MCP、设置），使回答能主动推荐用户已有的功能。用 Haiku 模型降低查询成本。

---

### 3.6 Statusline Setup（状态栏配置智能体）

**文件**: `built-in/statuslineSetup.ts`

**定位**: 最窄范围的专用智能体，仅配置状态栏。

**系统提示词特点**:

本质是一个**参考文档**——完整描述了 `statusLine` JSON schema、PS1 转换规则、shell 命令模板。提示词本身就是实现规范的说明书。

**配置特点**:

| 字段 | 值 | 说明 |
|------|-----|------|
| `tools` | `['Read', 'Edit']` | 最少工具集 |
| `model` | `'sonnet'` | 中等模型 |
| `color` | `'orange'` | 橙色标识 |

---

## 4. Fork 子智能体系统

**文件**: `forkSubagent.ts`

Fork 是一种特殊的智能体派生路径——子智能体**继承父级完整对话上下文**，而非从零开始。

### 4.1 触发条件

当 `FORK_SUBAGENT` feature gate 启用时，调用 Agent 工具时省略 `subagent_type` 即触发 fork。

### 4.2 Fork 智能体定义

```typescript
export const FORK_AGENT = {
  agentType: 'fork',
  tools: ['*'],
  useExactTools: true,        // 使用父级完全相同的工具池
  model: 'inherit',           // 继承父模型（上下文长度一致）
  permissionMode: 'bubble',   // 权限提示冒泡到父终端
  getSystemPrompt: () => '',  // 不使用——直接继承父级已渲染的系统提示词
}
```

### 4.3 Fork 指令格式

子智能体收到的指令被包裹在 `<fork-boilerplate>` XML 标签中：

```xml
<fork-boilerplate>
STOP. READ THIS FIRST.

You are a forked worker process. You are NOT the main agent.

RULES (non-negotiable):
1. 不要再生成子智能体——直接执行
2. 不要对话、提问或建议下一步
3. 不要发表评论或添加元评论
4. 直接使用工具：Bash, Read, Write 等
5. 修改文件后提交更改，报告包含 commit hash
6. 工具调用间不要输出文本，最后报告一次
7. 严格保持在指令范围内
8. 报告控制在 500 字以内
9. 回复必须以 "Scope:" 开头
10. 报告结构化事实，然后停止

Output format:
  Scope: <一句话复述分配范围>
  Result: <答案或关键发现>
  Key files: <相关文件路径>
  Files changed: <修改列表及 commit hash>
  Issues: <需要标记的问题>
</fork-boilerplate>

<directive>具体任务指令</directive>
```

### 4.4 Prompt Cache 优化设计

Fork 系统的核心设计目标是**最大化 prompt cache 命中率**：

1. **继承父级系统提示词字节**：不重新生成，直接使用 `toolUseContext.renderedSystemPrompt`，避免 GrowthBook 冷→热切换导致的缓存失效
2. **所有 fork 子进程产生字节相同的 API 请求前缀**：只有最后的指令文本块不同
3. **构建方式**：

```
[...历史消息, assistant(所有 tool_use 块), user(占位符 tool_results..., 指令文本)]
```

所有子进程的占位符结果完全相同（`'Fork started — processing in background'`），只有最后的指令文本不同。

### 4.5 递归 Fork 防护

```typescript
export function isInForkChild(messages: MessageType[]): boolean {
  // 检查消息历史中是否存在 <fork-boilerplate> 标签
  // 存在则说明当前已经是 fork 子进程，禁止再次 fork
}
```

---

## 5. 提示词组装流水线

### 5.1 系统提示词组装

组装发生在 `runAgent.ts` 的 `getAgentSystemPrompt` 函数（第 906 行）：

```
步骤 1: agentDefinition.getSystemPrompt({ toolUseContext })
         → 获取智能体专属提示词

步骤 2: enhanceSystemPromptWithEnvDetails()
         → 追加以下内容：
           - "Notes:" 块（绝对路径、不要 emoji、不要冒号引导工具调用）
           - 环境信息（工作目录、OS、平台、模型名称、知识截止日期）
           - 可选的技能发现指导
```

`enhanceSystemPromptWithEnvDetails`（`prompts.ts:760`）追加的内容：

```typescript
const notes = `Notes:
- Agent threads always have their cwd reset between bash calls,
  please only use absolute file paths.
- In your final response, share file paths (always absolute, never relative)
- For clear communication the assistant MUST avoid using emojis.
- Do not use a colon before tool calls.`

const envInfo = await computeEnvInfo(model, additionalWorkingDirectories)
// → 工作目录、git 状态、平台、shell、OS 版本、模型信息
```

### 5.2 完整 API 请求组装

`runAgent` 函数组装完整的 API 请求：

```
┌─────────────────────────────────────────────────────────┐
│ agentSystemPrompt   — 组装后的系统提示词                    │
├─────────────────────────────────────────────────────────┤
│ resolvedUserContext — 用户上下文                          │
│   └─ Explore/Plan 省略 claudeMd（节省 5-15 Gtok/周）      │
├─────────────────────────────────────────────────────────┤
│ resolvedSystemContext — 系统上下文                        │
│   └─ Explore/Plan 省略 gitStatus（节省 1-3 Gtok/周）      │
├─────────────────────────────────────────────────────────┤
│ initialMessages     — 初始消息                            │
│   ├─ forkContextMessages（fork 场景）                     │
│   ├─ promptMessages（用户指令）                           │
│   ├─ skill preloads（智能体 frontmatter 中的 skills）      │
│   └─ hook contexts（SubagentStart 钩子额外上下文）         │
├─────────────────────────────────────────────────────────┤
│ resolvedTools       — 过滤后的工具池                      │
└─────────────────────────────────────────────────────────┘
```

### 5.3 上下文省略优化

只读智能体（Explore、Plan）的特殊优化：

```typescript
// 省略 CLAUDE.md（~5-15 Gtok/周，34M+ Explore 调用）
const shouldOmitClaudeMd =
  agentDefinition.omitClaudeMd &&
  !override?.userContext &&
  getFeatureValue_CACHED_MAY_BE_STALE('tengu_slim_subagent_claudemd', true)

// 省略 gitStatus（~1-3 Gtok/周）
const resolvedSystemContext =
  agentDefinition.agentType === 'Explore' ||
  agentDefinition.agentType === 'Plan'
    ? systemContextNoGit
    : baseSystemContext
```

---

## 6. 父模型可见的工具描述

**文件**: `prompt.ts`

`getPrompt()` 函数构建父模型看到的 Agent 工具描述，决定父模型何时/如何使用子智能体。

### 6.1 描述结构

```
┌──────────────────────────────────────────────────────────┐
│ 1. 核心说明：Agent 工具启动专业化智能体处理复杂任务            │
├──────────────────────────────────────────────────────────┤
│ 2. 可用智能体列表（按来源分组）                              │
│    - 内置、插件、用户、项目、策略                            │
│    - 格式: "- type: whenToUse (Tools: ...)"              │
├──────────────────────────────────────────────────────────┤
│ 3. When NOT to use（仅非 Fork 模式）                       │
│    - 读已知文件 → 用 Read                                   │
│    - 搜特定类定义 → 用 Glob/Grep                           │
├──────────────────────────────────────────────────────────┤
│ 4. Usage notes                                            │
│    - 包含简短描述                                          │
│    - 并行启动多个智能体                                     │
│    - 前台 vs 后台选择                                      │
│    - SendMessage 继续已有智能体                             │
│    - isolation: "worktree" 隔离                            │
├──────────────────────────────────────────────────────────┤
│ 5. When to fork（仅 Fork 模式）                            │
│    - 研究：分叉开放性问题                                   │
│    - 实现：超过几处编辑的实现工作                             │
│    - 不要偷看：不要读取 fork 的输出文件                      │
│    - 不要竞赛：不要在通知到达前编造结果                      │
├──────────────────────────────────────────────────────────┤
│ 6. Writing the prompt                                     │
│    - 像 briefing 一位刚走进房间的聪明同事                    │
│    - 不要委托理解                                          │
├──────────────────────────────────────────────────────────┤
│ 7. Examples                                               │
│    - Fork 模式：ship-readiness audit 示例                  │
│    - 标准模式：test-runner, greeting-responder 示例         │
└──────────────────────────────────────────────────────────┘
```

### 6.2 关键指导原则

**"Writing the prompt" 部分**是核心——教父模型如何写好子智能体指令：

```
Brief the agent like a smart colleague who just walked into the room —
it hasn't seen this conversation, doesn't know what you've tried,
doesn't understand why this task matters.

**Never delegate understanding.** Don't write "based on your findings, fix the bug"
or "based on the research, implement it." Those phrases push synthesis onto the
agent instead of doing it yourself. Write prompts that prove you understood:
include file paths, line numbers, what specifically to change.
```

---

## 7. 自定义智能体加载

### 7.1 Markdown 格式（`.claude/agents/*.md`）

```markdown
---
name: my-agent
description: "Description shown to parent model"
tools: [Bash, Read, Grep]
model: haiku
memory: user
---

System prompt content here...
```

解析流程（`parseAgentFromMarkdown`）：
1. 从 frontmatter 提取 `name`、`description`、`tools`、`model` 等
2. 正文 `content` 作为系统提示词
3. `getSystemPrompt` 创建闭包持有提示词字符串
4. 如启用 memory，追加 `loadAgentMemoryPrompt()` 的结果

### 7.2 JSON 格式

```json
{
  "my-agent": {
    "description": "Description",
    "prompt": "System prompt content",
    "tools": ["Bash", "Read"],
    "model": "haiku"
  }
}
```

解析流程（`parseAgentFromJson`）：同 Markdown，用 Zod schema 校验。

---

## 8. 智能体注册与 Feature Gate

**文件**: `builtInAgents.ts`

```typescript
export function getBuiltInAgents(): AgentDefinition[] {
  const agents: AgentDefinition[] = [
    GENERAL_PURPOSE_AGENT,      // 始终可用
    STATUSLINE_SETUP_AGENT,     // 始终可用
  ]

  // Explore/Plan 受 Feature Gate 控制
  if (areExplorePlanAgentsEnabled()) {
    agents.push(EXPLORE_AGENT, PLAN_AGENT)
  }

  // Guide 智能体仅非 SDK 入口可用
  if (isNonSdkEntrypoint) {
    agents.push(CLAUDE_CODE_GUIDE_AGENT)
  }

  // Verification 受独立 Feature Gate 控制
  if (feature('VERIFICATION_AGENT') &&
      getFeatureValue_CACHED_MAY_BE_STALE('tengu_hive_evidence', false)) {
    agents.push(VERIFICATION_AGENT)
  }

  return agents
}
```

**Feature Gate 映射表**:

| 智能体 | Feature Gate | 默认值 | 说明 |
|--------|-------------|--------|------|
| Explore + Plan | `BUILTIN_EXPLORE_PLAN_AGENTS` + `tengu_amber_stoat` | `true`（3P 默认开） | A/B 测试可关闭 |
| Verification | `VERIFICATION_AGENT` + `tengu_hive_evidence` | `false`（3P 默认关） | 仅 Ant 内部 A/B |
| Fork | `FORK_SUBAGENT` | 关 | 实验性功能 |
| Guide | 入口类型判断 | 非 SDK 入口可用 | SDK 不需要文档查询 |

### 一次性智能体

```typescript
// constants.ts
export const ONE_SHOT_BUILTIN_AGENT_TYPES: ReadonlySet<string> = new Set([
  'Explore',
  'Plan',
])
```

Explore 和 Plan 是一次性的——返回报告后不可通过 SendMessage 继续。跳过 agentId/SendMessage/usage 尾部信息，每次节省约 135 个字符 × 每周 3400 万次 Explore 调用。

---

## 9. 七大设计模式总结

### 模式 1：闭包式提示词生成

每个智能体存储 `getSystemPrompt` 函数而非静态字符串，支持：
- **延迟求值**：只在智能体被调用时生成提示词
- **动态上下文注入**：Guide 智能体运行时读取用户配置
- **条件分支**：根据 `hasEmbeddedSearchTools()` 等运行时条件调整提示词

### 模式 2：双重约束确保只读

Explore 和 Plan 智能体同时使用：
- **提示词约束**：在系统提示词中明确列出禁止操作
- **工具约束**：从 `disallowedTools` 中移除所有编辑工具

这种"defense in depth"设计防止 LLM 在长对话中遗忘约束。

### 模式 3：Prompt Cache 优化

三层优化策略：
1. **Fork 字节共享**：所有 fork 子进程产生字节相同的 API 请求前缀
2. **省略无关上下文**：只读智能体省略 CLAUDE.md 和 gitStatus
3. **附件分离**：智能体列表通过 `agent_listing_delta` 附件注入而非内联，避免 MCP/插件变化导致工具描述缓存失效

### 模式 4：关键系统提醒强化

Verification 智能体使用 `criticalSystemReminder_EXPERIMENTAL`，在**每轮对话**重新注入核心约束：

```
CRITICAL: This is a VERIFICATION-ONLY task. You CANNOT edit, write, or create
files IN THE PROJECT DIRECTORY. You MUST end with VERDICT: PASS/FAIL/PARTIAL.
```

对抗 LLM 在长对话中的**上下文稀释**效应。

### 模式 5：心理学对抗设计

Verification 智能体独特的提示词策略：
- **预判失败模式**：明确列出两种已知失败模式（验证回避、被前 80% 欺骗）
- **列出借口**："识别你自己的合理化借口"——预判 LLM 会说什么来跳过检查
- **反模式示例**：展示"坏"的验证报告（无命令执行的 PASS）vs "好"的报告

### 模式 6：一次性 vs 可继续

```
ONE_SHOT: Explore, Plan
  → 返回报告即结束，无 agentId，不可 SendMessage 继续
  → 节省 token

CONTINUABLE: General-Purpose, Verification, Guide, Statusline
  → 保留 agentId，可通过 SendMessage 继续对话
  → 支持多轮交互（如验证 → 修复 → 再次验证）
```

### 模式 7：分层提示词组装

```
智能体专属提示词 (getSystemPrompt)
       ↓
环境信息增强 (enhanceSystemPromptWithEnvDetails)
  - 绝对路径注意事项
  - emoji/格式约定
  - OS/平台/模型信息
       ↓
用户上下文 (userContext)
  - 可选省略 claudeMd
       ↓
系统上下文 (systemContext)
  - 可选省略 gitStatus
       ↓
初始消息 (initialMessages)
  - fork 上下文消息
  - 技能预加载
  - 钩子额外上下文
       ↓
工具池 (resolvedTools)
  - 根据智能体定义过滤
```

---

## 附录：智能体对比总览

| 智能体 | 模型 | 工具 | 只读 | 可继续 | 后台 | 提示词长度 | 核心策略 |
|--------|------|------|------|--------|------|-----------|---------|
| General-Purpose | 默认 | 全部 | 否 | 是 | 否 | 短 | 最少约束，最大灵活 |
| Explore | Haiku | 只读集 | 是 | 否 | 否 | 中 | 双重只读约束 + 速度优化 |
| Plan | Inherit | 只读集 | 是 | 否 | 否 | 中 | 结构化流程 + 强制输出格式 |
| Verification | Inherit | 只读集 | 是 | 是 | 是 | 长 | 心理学对抗 + 结构化验证 + 每轮强化 |
| Guide | Haiku | 搜索+网络 | 是 | 是 | 否 | 中 | 动态上下文注入（运行时读配置） |
| Statusline | Sonnet | Read+Edit | 否 | 是 | 否 | 中 | 参考文档式提示词 |
| Fork | Inherit | 全部（精确） | 否 | 否 | 是 | 短 | 继承上下文 + 结构化报告 |
