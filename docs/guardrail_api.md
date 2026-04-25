# Guardrail API

## 处理流程

```
LLM 返回 → guardrail 层接管 → 裁决矩阵 → per-call 聚合 → 执行/拦截 → post_check → warn
```

guardrail 层包裹除 LLM 调用外的所有逻辑。每个 guardrail 独立对所有 pending calls 出裁决，框架按 call 粒度聚合。

## CallVerdict

单个 guardrail 对单个调用的裁决。

| 字段 | 类型 | 说明 |
|---|---|---|
| `action` | `str` | `"allow"` / `"block"` / `"warn"` |
| `message` | `str` | block 或 warn 时的说明文本 |
| `modified_args` | `dict \| None` | 替换该调用的参数 |

| action | 效果 |
|---|---|
| `allow` | 放行 |
| `block` | 拦截，不执行，message 作为 tool_result 返回 LLM |
| `warn` | 放行但追加 message 到对话 |

## GuardrailContext

guardrail 可访问的只读上下文。

| 接口 | 返回 | 说明 |
|---|---|---|
| `ctx.is_tool_call()` | `bool` | LLM 返回了 tool_calls |
| `ctx.is_text_response()` | `bool` | LLM 返回了文本 |
| `ctx.last_response` | `str \| None` | 上一条 LLM 文本内容 |
| `ctx.pending_calls` | `[(name, args)]` | 当前 LLM 输出的 tool calls |
| `ctx.messages` | `[dict]` | 完整对话历史 |
| `ctx.tool_history` | `[(name, args, result)]` | 历史工具调用 |
| `ctx.rounds` | `int` | 累计工具调用轮次 |
| `ctx.store` | `object` | 领域数据 |

## Guardrail 基类

```python
class Guardrail(ABC):
    def check(self, ctx) -> dict:
        # 返回 {call_index: CallVerdict, "text": CallVerdict, ...}
        # key = int → 针对第 i 个 tool call
        # key = "text" → 针对文本响应
        # {} → 全部放行
        return {}

    def post_check(self, ctx, call_index, name, args, result) -> str | None:
        # 工具执行后，返回修改后的 result 或 None
        return None
```

## 聚合规则

多个 guardrail 对同一个调用返回 verdict 时，框架独立聚合：

- **优先级**：block > warn > allow
- **block**：拦截，聚合所有 block 消息（`[GuardrailName] reason` 格式合并）
- **warn**：执行，聚合所有 warn 消息注入对话
- **modified_args**：按注册顺序 merge，后注册的覆盖同名 key；block 时忽略
- **post_check**：pipeline，每个 guardrail 依次处理前一个的输出

## 事件流

| type | 触发时机 | 关键字段 |
|---|---|---|
| `blocked` | tool call / text 被拦截 | `guardrail`, `content` |
| `tool_call` | 即将执行工具 | `name`, `arguments`, `id` |
| `tool_result` | 工具执行完成 | `name`, `result`, `id` |
| `warning` | 执行后追加警告 | `guardrail`, `content` |
| `done` | 文本响应返回 | `content` |

## 编写新 Guardrail

1. 在 `agent/guardrail/` 下新建 `.py` 文件
2. 继承 `Guardrail`，实现 `check(ctx) → dict`
3. 在 `agent/guardrail/__init__.py` 的 `build_guardrails()` 中注册

```python
from agent.guardrail_api import Guardrail, GuardrailContext, CallVerdict

class MyGuardrail(Guardrail):
    def check(self, ctx: GuardrailContext) -> dict:
        result = {}
        for i, (name, args) in enumerate(ctx.pending_calls):
            if name == "query" and ...:
                result[i] = CallVerdict("block", "原因")
        return result
```


