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
from agent.prompt._effort import get_effort_prompt, VALID_EFFORTS
from agent.prompt._sql import get_sql_rules
from agent.prompt._bird import get_bird_sql_prompt
from agent.prompt._guardrail import get_guardrail_guidance
from agent.prompt._project import build_project_context
from agent.prompt._README import build_readme_context


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

    # Cache-friendly order:
    # 1-5 are stable for the same prompt/tool/guardrail profile and should stay
    # before dynamic project-specific sections.
    # 6-7 vary by project and are intentionally appended last.

    # 1. 基础系统身份与图模型
    if "base" in spec.prompts:
        _append_part(parts, get_base_prompt())

    # 2. 工具使用方式
    if "tool" in spec.prompts:
        _append_part(parts, get_tool_prompt(spec))

    # 3. ontology / 图拓扑说明
    if "ontology" in spec.prompts:
        _append_part(parts, get_ontology_prompt())

    # 4. SQL 任务通用规则
    if "sql" in spec.prompts:
        _append_part(parts, get_sql_rules())

    # 5. effort 约束
    if "effort" in spec.prompts:
        _append_part(parts, get_effort_prompt(spec.effort))

    # 6. guardrail 约束
    if "guardrail" in spec.prompts:
        guardrail = get_guardrail_guidance(spec)
        if guardrail:
            _append_part(parts, guardrail)

    # 7. BIRD benchmark 专用 SQL 风格；只由 BIRD runner 显式加载
    if "bird" in spec.prompts:
        _append_part(parts, get_bird_sql_prompt())

    # 8. 当前项目上下文
    if "project" in spec.prompts:
        project = build_project_context(spec.project_path, spec=spec)
        if project:
            _append_part(parts, project)

    # 9. 当前项目 README
    if "readme" in spec.prompts:
        readme = build_readme_context(spec.project_path, spec=spec)
        if readme:
            _append_part(parts, readme)

    return parts


def _append_part(parts: list[str], text: str) -> None:
    if text and text.strip():
        parts.append(text)


def build_prompt_messages(spec) -> list[str]:
    """返回 system message 列表。

    每一段单独作为一个 system message。通用、可缓存的段保持在前；
    project/README 等动态段保持在后，以便批量任务最大化共享前缀。
    """
    return build_prompt_parts(spec)


def build_prompt(spec) -> str:
    """兼容旧接口：把所有 prompt 段拼成一个字符串。"""
    return "\n\n".join(build_prompt_parts(spec))
