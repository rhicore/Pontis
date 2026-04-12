# src/tools 工具概览

本文档介绍 Claude Code 源码中 `src/tools/` 下每个工具模块的作用。

> 工具名称（如 `Bash`、`Read`）是 Claude 模型实际调用时使用的名称，目录名则是源码中的模块组织名。

---

## 文件操作类

| 工具名 | 目录 | 说明 |
|--------|------|------|
| `Read` | FileReadTool | 读取文件内容，支持文本、图片（PNG/JPG）、PDF、Jupyter Notebook（.ipynb） |
| `Write` | FileWriteTool | 创建或覆盖文件，将内容写入指定路径 |
| `Edit` | FileEditTool | 精确替换文件中的字符串内容，支持单处和全局替换（replace_all） |
| `NotebookEdit` | NotebookEditTool | 编辑 Jupyter Notebook（.ipynb）的单元格，支持替换、插入、删除操作 |
| `Glob` | GlobTool | 按文件名模式（glob pattern）快速查找文件，如 `**/*.ts` |
| `Grep` | GrepTool | 基于 ripgrep 的文件内容搜索，支持正则表达式、多种输出模式 |

## 命令执行类

| 工具名 | 目录 | 说明 |
|--------|------|------|
| `Bash` | BashTool | 在 bash shell 中执行命令，包含安全检查（沙箱、破坏性命令警告等） |
| `PowerShell` | PowerShellTool | Windows 环境下在 PowerShell 中执行命令，功能与 BashTool 对称 |
| `LSP` | LSPTool | 语言服务器协议集成，提供代码智能功能：跳转定义、查找引用、符号搜索、悬停信息 |

## 代理与多智能体类

| 工具名 | 目录 | 说明 |
|--------|------|------|
| `Agent` | AgentTool | 启动子代理处理复杂任务，支持多种内置代理类型（Explore、Plan、general-purpose 等），可并行执行 |
| `SendMessage` | SendMessageTool | 在 swarm 多代理模式下，向队友代理发送消息 |
| `TeamCreate` | TeamCreateTool | 创建多代理协作团队（swarm），指定 lead agent 和成员 |
| `TeamDelete` | TeamDeleteTool | 解散一个 swarm 团队并清理相关资源 |

## 计划与工作树类

| 工具名 | 目录 | 说明 |
|--------|------|------|
| `EnterPlanMode` | EnterPlanModeTool | 进入计划模式，在编码前先设计实现方案 |
| `ExitPlanMode` | ExitPlanModeTool | 退出计划模式，展示计划供用户审批后开始编码 |
| `EnterWorktree` | EnterWorktreeTool | 创建隔离的 git worktree 并切换到其中工作 |
| `ExitWorktree` | ExitWorktreeTool | 退出 worktree 会话，返回原始工作目录 |

## 任务管理类

| 工具名 | 目录 | 说明 |
|--------|------|------|
| `TaskCreate` | TaskCreateTool | 在任务列表中创建新任务 |
| `TaskGet` | TaskGetTool | 按 ID 获取单个任务的详细信息 |
| `TaskList` | TaskListTool | 列出所有任务及其状态 |
| `TaskUpdate` | TaskUpdateTool | 更新任务的状态、描述、依赖关系等 |
| `TaskOutput` | TaskOutputTool | 获取后台任务的输出结果 |
| `TaskStop` | TaskStopTool | 终止正在运行的后台任务 |
| `TodoWrite` | TodoWriteTool | 管理会话中的待办事项清单 |

## 交互与配置类

| 工具名 | 目录 | 说明 |
|--------|------|------|
| `AskUserQuestion` | AskUserQuestionTool | 向用户提问，收集偏好或确认决策，支持多选和预览 |
| `Config` | ConfigTool | 获取或设置 Claude Code 配置（主题、模型等） |
| `Skill` | SkillTool | 调用 slash-command 技能（如 `/commit`、`/review-pr`） |
| `SendUserMessage` | BriefTool | 向用户发送消息（包括附件上传），用于展示中间结果或请求反馈 |

## 网络类

| 工具名 | 目录 | 说明 |
|--------|------|------|
| `WebFetch` | WebFetchTool | 获取并提取指定 URL 的网页内容 |
| `WebSearch` | WebSearchTool | 搜索互联网获取最新信息，返回搜索结果 |

## MCP（Model Context Protocol）类

| 工具名 | 目录 | 说明 |
|--------|------|------|
| `mcp` | MCPTool | 调用已连接的 MCP 服务器提供的工具，支持动态加载外部工具 |
| `McpAuthTool` | McpAuthTool | 处理 MCP 服务器的认证流程 |
| `ListMcpResourcesTool` | ListMcpResourcesTool | 列出已连接 MCP 服务器提供的资源列表 |
| `ReadMcpResourceTool` | ReadMcpResourceTool | 按 URI 读取特定 MCP 资源的内容 |

## 定时与调度类

| 工具名 | 目录 | 说明 |
|--------|------|------|
| `CronCreate` | ScheduleCronTool | 创建定时任务，支持一次性提醒和周期性调度 |
| `CronDelete` | ScheduleCronTool | 取消已调度的定时任务 |
| `CronList` | ScheduleCronTool | 列出所有活跃的定时任务 |
| `RemoteTrigger` | RemoteTriggerTool | 管理远程代理触发器，用于远程调度执行 |

## 其他

| 工具名 | 目录 | 说明 |
|--------|------|------|
| `REPL` | REPLTool | 交互式 REPL 环境，在子代理中提供受限的工具子集（Read、Write、Edit、Glob、Grep、Bash、NotebookEdit） |
| `Sleep` | SleepTool | 等待指定时间，用于需要延迟的场景 |
| `ToolSearch` | ToolSearchTool | 搜索和发现可用工具，帮助模型在工具较多时找到合适的工具 |
| `StructuredOutput` | SyntheticOutputTool | 以结构化 JSON 格式返回最终响应 |

## 共享模块

| 路径 | 说明 |
|------|------|
| `shared/spawnMultiAgent.ts` | 多代理创建与管理的共享逻辑，供 TeamCreate 和 Agent 工具复用 |
| `utils.ts` | 工具通用辅助函数 |

---

## 工具总览图

```
src/tools/
├── 文件操作: FileReadTool / FileWriteTool / FileEditTool / GlobTool / GrepTool / NotebookEditTool
├── 命令执行: BashTool / PowerShellTool / LSPTool
├── 代理系统: AgentTool / SendMessageTool / TeamCreateTool / TeamDeleteTool
├── 任务管理: TaskCreateTool / TaskGetTool / TaskListTool / TaskUpdateTool / TaskOutputTool / TaskStopTool
├── 计划模式: EnterPlanModeTool / ExitPlanModeTool
├── 工作树:   EnterWorktreeTool / ExitWorktreeTool
├── 交互配置: AskUserQuestionTool / ConfigTool / SkillTool / BriefTool
├── 网络访问: WebFetchTool / WebSearchTool
├── MCP:      MCPTool / McpAuthTool / ListMcpResourcesTool / ReadMcpResourceTool
├── 定时调度: ScheduleCronTool (CronCreate/CronDelete/CronList) / RemoteTriggerTool
├── 其他:     REPLTool / SleepTool / ToolSearchTool / SyntheticOutputTool / TodoWriteTool
└── 共享:     shared/ / utils.ts
```
