"""Prompt builder — 显式按顺序组装系统提示词。

阅读这个文件时，不需要理解注册表、scope 或隐式分发。
直接看下面几个函数即可：

- build_prompt_parts(spec): 返回最终 prompt 段列表，顺序就是发送顺序
- build_prompt_messages(spec): 返回 system message 列表
- build_prompt(spec): 返回兼容旧接口的单字符串 system prompt
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
from agent.prompt._project import build_project_context
from agent.prompt._README import build_readme_context
from agent.prompt._reflection import get_reflection_prompt


def _validate_spec(spec) -> None:
    if spec.effort not in VALID_EFFORTS:
        raise ValueError(f"Unknown effort {spec.effort!r}; expected one of {VALID_EFFORTS}")


def build_prompt_parts(spec) -> list[str]:
    """显式返回完整 prompt 段列表。

    这里就是整个系统提示词的真实组装顺序。
    如果要调整顺序、增删某段、插入约束，直接改这里。
    """
    _validate_spec(spec)

    parts: list[str] = []

    # 1. 基础系统身份与图模型
    if "base" in spec.prompts:
        parts.append(get_base_prompt())

    # 2. 工具使用方式
    if "tool" in spec.prompts:
        parts.append(get_tool_prompt(spec))

    # 3. ontology / 图拓扑说明
    if "ontology" in spec.prompts:
        parts.append(get_ontology_prompt())

    # 4. 实体 meta 字段说明
    if "meta" in spec.prompts:
        parts.append(get_meta_prompt())

    # 5. SQL 任务通用规则
    if "sql" in spec.prompts:
        parts.append(get_sql_rules())

    # 6. reflection 模式专用规则
    if "reflection" in spec.prompts:
        parts.append(get_reflection_prompt())

    # 7. readonly / writer / sub_agent 模式补充约束
    if "readonly" in spec.prompts:
        parts.append(get_readonly_additions())
    if "writer" in spec.prompts:
        parts.append(get_writer_additions())
    if "sub_agent" in spec.prompts:
        parts.append(get_sub_agent_additions())

    # 8. effort 约束
    if "effort" in spec.prompts:
        parts.append(get_effort_prompt(spec.effort))

    # 9. guardrail 约束
    if "guardrail" in spec.prompts:
        guardrail = get_guardrail_guidance(spec)
        if guardrail:
            parts.append(guardrail)

    # 10. 当前项目上下文
    if "project" in spec.prompts:
        project = build_project_context(spec.project_path, spec=spec)
        if project:
            parts.append(project)

    # 11. 当前项目 README
    if "readme" in spec.prompts:
        readme = build_readme_context(spec.project_path, spec=spec)
        if readme:
            parts.append(readme)

    return parts


def build_prompt_messages(spec) -> list[str]:
    """返回 system message 列表。

    目前策略很简单：
    - 先按 build_prompt_parts(spec) 得到完整列表
    - 每一段单独作为一个 system message

    这样顺序最显式，调试最直接。
    """
    return build_prompt_parts(spec)


def build_prompt(spec) -> str:
    """兼容旧接口：把所有 prompt 段拼成一个字符串。"""
    return "\n\n".join(build_prompt_parts(spec))
