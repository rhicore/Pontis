"""Prompt builder — 声明式分层组装 agent 提示词。

PROMPT_PROVIDERS: name → (spec) -> str 的查找表。
build_prompt: 根据 spec.prompts 列表组装（由 resolve_mode 填充）。
"""
from agent.prompt._base import get_base_prompt
from agent.prompt._tool import get_tool_prompt
from agent.prompt._ontology import get_ontology_prompt
from agent.prompt._meta import get_meta_prompt
from agent.prompt._effort import get_effort_prompt, VALID_EFFORTS
from agent.prompt._sql import get_sql_rules
from agent.prompt._guardrail import get_guardrail_guidance
from agent.prompt._readonly import get_readonly_additions
from agent.prompt._writer import get_writer_additions
from agent.prompt._sub_agent import get_sub_agent_additions
from agent.prompt._benchmark import get_benchmark_additions
from agent.prompt._project import build_project_context


# ──────────────────────────────────────────────────────────
#  Reflection prompt（内联定义，仅 reflection mode 使用）
# ──────────────────────────────────────────────────────────

_REFLECTION_PROMPT = r"""## 经验反思系统

你是一个经验分析器。你的任务是从执行记录中提炼可迁移的抽象知识，并发现工具使用和系统提示词的问题。

你可以用 glob/meta/query 了解数据库结构，知识实体自动存储到全局知识库（所有项目共享）。

---

## 先查后写

创建新实体前，**必须先检查是否已有相似实体**：

1. `glob "*:knowledge"` 或 `glob "*:convention"` / `glob "*:lesson"` / `glob "*:pattern"` 等查看已有实体
2. 对可能相关的实体用 `meta` 查看完整 detail
3. 如果已有相似实体：
   - 内容相同 → 跳过，不重复创建
   - 内容互补 → 用 `update_meta` 补充 detail
   - 内容矛盾 → 用 `update_meta` 覆盖修正
4. 确认无相似实体时才 `create_entity`

---

## 知识类型

你通过 create_entity 创建知识实体，通过 update_meta 写入 detail 内容。
创建时不需要指定 project 或 namespace，系统自动路由到全局知识库。

### convention（约定）
必须遵循或避免的规则。
- ref 格式：`<简短英文标识>:convention`
- detail：规则描述 + 适用场景
- 触发条件：同类错误出现 2+ 次，或成功案例表现出一致特征

### pattern（模式）
可复用的查询模式。
- ref 格式：`<简短英文标识>:pattern`
- detail：模式描述 + SQL 模板（用 `<placeholder>` 代替具体值）+ 适用场景
- 触发条件：多个问题使用相同结构解法

### term（术语）
跨领域通用的概念解释。
- ref 格式：`<术语>:term`
- detail：术语在数据分析语境下的含义
- 触发条件：对术语理解有偏差导致错误

### lesson（教训）
从错误中提炼的反面经验，也用于记录工具和 prompt 问题。
- ref 格式：`<简短英文标识>:lesson`
- detail：错误根因 + 正确做法 + 什么场景容易犯这个错
- 触发条件：非直觉的错误（推理逻辑缺陷，不只是列名选错）
- 工具问题：detail 标注 `[tool_issue]` 前缀
- Prompt 问题：detail 标注 `[prompt_issue]` 前缀

### example（示例）
容易出错的 question-SQL 对。
- ref 格式：`<简短英文标识>:example`
- detail：问题、正确 SQL、易错点、关联知识名称
- 触发条件：SQL 映射非直觉，或反复犯错

---

## 提炼原则

1. **可迁移性优先**：只提炼不依赖具体表名列名的知识
2. **从模式归纳**：同类错误出现 2+ 次才值得提炼
3. **区分类型**：约定是规则，模式是模板，术语是概念，教训是反面经验
4. **内容放 detail**：brief ≤50 字摘要，detail 放完整内容

---

## 分析流程

1. `glob "*:knowledge"` 了解已有知识实体
2. 通读所有执行记录，理解每条的：做了什么、为什么这样做、结果如何
3. 按错误类型分组，识别同类错误的共性
4. 归纳知识实体，只保留出现 2+ 次的模式
5. 对比已有实体，决定创建新实体还是更新已有实体
"""

# ──────────────────────────────────────────────────────────
#  Prompt Provider 注册表
# ──────────────────────────────────────────────────────────

PROMPT_PROVIDERS = {
    "base":       lambda s: get_base_prompt(),
    "tool":       lambda s: get_tool_prompt(),
    "ontology":   lambda s: get_ontology_prompt(),
    "meta":       lambda s: get_meta_prompt(),
    "effort":     lambda s: get_effort_prompt(s.effort),
    "sql":        lambda s: get_sql_rules(),
    "guardrail":  lambda s: get_guardrail_guidance(),
    "readonly":   lambda s: get_readonly_additions(),
    "writer":     lambda s: get_writer_additions(),
    "sub_agent":  lambda s: get_sub_agent_additions(),
    "benchmark":  lambda s: get_benchmark_additions(),
    "reflection": lambda s: _REFLECTION_PROMPT,
    "project":    lambda s: build_project_context(s.project_path, spec=s),
}


def build_prompt(spec) -> str:
    """根据 AgentSpec.prompts 组装完整系统提示词。

    spec.prompts 由 resolve_mode() 填充（含条件追加的 effort/debug）。
    """
    if spec.effort not in VALID_EFFORTS:
        raise ValueError(f"Unknown effort {spec.effort!r}; expected one of {VALID_EFFORTS}")

    parts = []
    for name in spec.prompts:
        provider = PROMPT_PROVIDERS.get(name)
        if provider:
            parts.append(provider(spec))

    return "\n\n".join(parts)
