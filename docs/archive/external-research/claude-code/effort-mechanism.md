# Claude Code Effort 机制详解

> 源码位置：`src/utils/effort.ts`、`src/services/api/claude.ts`

## 一、概述

Effort 是 Claude Code 中控制**模型推理强度**的参数。它决定模型在回答时投入多少"思考力度"，直接影响响应速度与回答质量之间的平衡。

## 二、四个等级

| 等级 | 含义 | 适用场景 |
|------|------|----------|
| `low` | 快速、轻量实现，开销最小 | 简单问答、格式化、小修改 |
| `medium` | 平衡方式，标准实现和测试 | 日常编码任务（Opus 4.6 默认） |
| `high` | 全面实现，广泛测试和文档 | 复杂重构、架构设计 |
| `max` | 最大推理深度，最深思考 | 仅 Opus 4.6 可用，最难的问题 |

## 三、实现原理

### 3.1 API 传递方式

Effort 通过 Anthropic Messages API 的 `output_config.effort` 字段传递：

```
POST /v1/messages
{
  "model": "claude-opus-4-6",
  "output_config": {
    "effort": "medium"          // ← effort 值在这里
  },
  ...
}
```

同时需要 beta header：`effort-2025-11-24`（定义在 `src/constants/betas.ts`）。

### 3.2 核心调用链

```
用户选择 effort
  └→ resolveAppliedEffort()          // 按优先级解析最终值
       └→ configureEffortParams()    // 写入 output_config.effort
            └→ 添加 beta header      // "effort-2025-11-24"
                 └→ 发送到 API       // 模型据此调整推理深度
```

### 3.3 优先级链（谁说了算）

```
环境变量 CLAUDE_CODE_EFFORT_LEVEL  >  用户设置 effortValue  >  模型默认值
```

- 环境变量设为 `unset` 或 `auto` → 不发送 effort 参数（API 侧等价 `high`）
- 环境变量设为具体等级 → 强制使用该等级

### 3.4 Effort 与 Thinking 的关系

Effort 和 Thinking（extended thinking）是**两套独立但协作的机制**：

| 机制 | 控制对象 | API 字段 |
|------|----------|----------|
| Effort | 整体推理强度 | `output_config.effort` |
| Thinking | 思考 token 预算 | `thinking.budget_tokens` / `thinking.type: 'adaptive'` |

Claude 4.6 系列模型使用 **adaptive thinking**（`thinking.type: 'adaptive'`），模型自动决定思考深度。Effort 参数会间接影响 adaptive thinking 的内部调度——等级越高，模型倾向于思考越深入。

还有一个 **ultrathink** 关键词机制：在提示中输入 "ultrathink" 可触发高强度思考，同时将默认 effort 设为 `medium` 以平衡速度。

## 四、模型支持矩阵

| 模型 | 支持 Effort | 支持 `max` | 默认等级 |
|------|:-----------:|:----------:|----------|
| Opus 4.6 | ✅ | ✅ | `medium`（Pro/Max/Team） |
| Sonnet 4.6 | ✅ | ❌（降级为 `high`） | `undefined`（= `high`） |
| Haiku 4.5 | ❌ | ❌ | — |
| 旧版模型 | ❌ | ❌ | — |

## 五、内部用户的数值 Effort

Anthropic 内部用户（`USER_TYPE === 'ant'`）可使用数值 effort（0~100+ 的整数），通过 `anthropic_internal.effort_override` 传递，不走标准的 `output_config` 通道。

数值到等级映射：

```
≤ 50  →  low
≤ 85  →  medium
≤ 100 →  high
> 100 →  max
```

## 六、关键代码索引

| 文件 | 作用 |
|------|------|
| `src/utils/effort.ts` | Effort 全部核心逻辑：等级定义、解析、优先级、默认值 |
| `src/services/api/claude.ts:440-466` | `configureEffortParams()` — 将 effort 写入 API 请求 |
| `src/constants/betas.ts:15` | Beta header 定义 `effort-2025-11-24` |
| `src/utils/thinking.ts` | Thinking 相关逻辑，与 effort 协同工作 |
