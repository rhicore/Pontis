# Prompt 拼装示例

本文展示当前 `agent/prompt/__init__.py` 中 `build_prompt_parts(spec)`、`build_prompt_messages(spec)` 与 `build_prompt(spec)` 的实际输出效果。

选择了两个实际 project 组合作为样例：

- `["california_schools", "bird"]`
- `["formula_1", "bird"]`

运行配置：

- BIRD script explicit config: `readonly` base mode with custom tools/prompts/guardrails
- `project_path=/nfsdat2/home/bcchenslm/Projects/Pontis`

## 当前显式拼装顺序

`build_prompt_parts(spec)` 里的顺序现在是：

1. `base`
2. `tool`
3. `ontology`
4. `meta`
5. `sql`
6. `reflection`
7. `readonly / writer / sub_agent`
8. `effort`
9. `guardrail`
10. `project`
11. `readme`

对于 BIRD 脚本显式配置，这次实际命中的段是：

1. `base`
2. `tool`
3. `ontology`
4. `meta`
5. `sql`
6. `guardrail`
7. `project`
8. `readme`

## 样例一：`california_schools + bird`

统计信息：

- `parts`: `8`
- `messages`: `8`
- `build_prompt(spec)` 最终字符串长度：`16894`

各段长度：

1. `base` → `1286`
2. `tool` → `463`
3. `ontology` → `4014`
4. `meta` → `1061`
5. `sql` → `1247`
6. `guardrail` → `413`
7. `project` → `267`
8. `readme` → `8129`

原样输出文件：

- 最终完整 prompt：[california_bird.md](/nfsdat2/home/bcchenslm/Projects/Pontis/docs/agent/prompt_examples/california_bird.md:1)

## 样例二：`formula_1 + bird`

统计信息：

- `parts`: `8`
- `messages`: `8`
- `build_prompt(spec)` 最终字符串长度：`17673`

各段长度：

1. `base` → `1286`
2. `tool` → `463`
3. `ontology` → `4014`
4. `meta` → `1061`
5. `sql` → `1247`
6. `guardrail` → `413`
7. `project` → `249`
8. `readme` → `8926`

原样输出文件：

- 最终完整 prompt：[formula1_bird.md](/nfsdat2/home/bcchenslm/Projects/Pontis/docs/agent/prompt_examples/formula1_bird.md:1)

## 你现在最该关注什么

看这些文件时，建议重点看三件事：

1. `readme` 段明显最长
   - 目前最终字符串的大头是项目 README，不是 base/tool/sql
2. `project` 和 `readme` 是最直接的项目级动态部分
   - 换项目时，主要波动集中在这两段
3. 当前示例文件只展示最终完整 prompt
   - 也就是 `build_prompt(spec)` 最后拼出来的字符串

## 如果你只是想直接看最终字符串

最直接点这两个文件：

- [california_bird.md](/nfsdat2/home/bcchenslm/Projects/Pontis/docs/agent/prompt_examples/california_bird.md:1)
- [formula1_bird.md](/nfsdat2/home/bcchenslm/Projects/Pontis/docs/agent/prompt_examples/formula1_bird.md:1)

它们就是 `build_prompt(spec)` 最终拼出来的结果，只是在最前面额外补了一小段样例元信息，方便识别案例。
