"""Agent Relation/Disambiguation Review — route overlap candidates.

This explorer reviews the same overlap candidate once and decides whether it
should become a rel, a disambig, both, or neither.
"""
import logging

from storage.workspace import Workspace

from explorer.utils.overlap_candidates import (
    MAX_CANDIDATES_PER_AGENT,
    CandidateGroup,
    build_candidate_groups,
    candidate_batches,
)

logger = logging.getLogger(__name__)


PROMPT = """\
你是新来的数据分析师。当前 Pontis 图谱里已经有 `overlap` 候选，它们表示一些列的取值有交集。
你的任务是逐组审查这些候选，并在需要时创建或更新 `disambig` / `rel` 实体。

- `disambig` 用来记录容易混淆的表或列之间有什么不同。
- `rel` 用来记录两个列可以稳定匹配同一批行或同一类对象。
- 如果 overlap 只能说明值有交集，不能说明字段含义相近或行能稳定匹配，就保留为普通 overlap 线索。

## 判断原则

- `disambig` 记录相似字段的区别：来源表、每行代表什么、覆盖哪些行、编码和值格式、空值和值域。
- `rel` 记录候选列自身提供的行级匹配关系，常见证据包括主键、外键、唯一标识或稳定业务编号。
- 属性列、低基数枚举、bool、分类文本、描述文本、短代码和统计值重叠时，先比较这些字段各自是什么。
- 已有主键、外键或更强连接能解释两表行级关系时，普通属性列 overlap 进入 `disambig` 审查。
- 多个字段属于同一组容易混淆字段时，维护一个 group `disambig`，连接这一组里的全部相关字段；独立二元选择使用 pair `disambig`。
- 同结构槽位字段属于同一字段族，完整 group 包含全部槽位；低质量、空值多或官方标注不可用的槽位也进入 group，并写明空值率、覆盖范围和官方可用性。
- 候选列的 `linked_disambig` 表示当前实际边覆盖；detail 文字只解释已连接字段，覆盖范围由连接列决定。
- 候选组出现“部分字段已连接到同一 disambig、其余字段未连接”的覆盖缺口时，优先整理成完整 group。

## 要比较的内容

- 字段说明来源包括 `official_column_description`、`official_value_description`、brief/detail、样例值和统计事实。
- 字段差异包括来源表、行粒度、覆盖范围、编码体系、值域、空值、行过滤、存储类别、连接后的行数变化和统计含义。
- 代码列、名称列、类型列、状态列、范围端点列、同结构槽位列、跨表同名/近名列，按它们在数据库里的实际含义比较。
- 数值范围偶然相交且来源表、行粒度、覆盖范围、存储类别和值域事实均不相交的候选，保留为 overlap 线索。
- `rel` 需要元数据或查询结果证明候选列之间存在稳定行级匹配，并且这个匹配关系不是已有主键/外键的重复说明。
- 写入内容聚焦数据库对象本身：字段含义、值域证据、来源表、覆盖行数、空值、行粒度、稳定匹配关系或外键引用。
- 两个字段值集相同或高度重叠时，仍按来源表、存储类别、覆盖行集合、空值和连接基数分别记录。

## 写入格式

`disambig` detail 写成固定结构：比较主题、各字段区别、混用会改变哪些行或值、值域证据。

brief/detail 写清每个字段是什么、它们在哪些地方相似、主要区别是什么、各自覆盖哪些行和值、连接后行数是否会变化。

多个字段共享值域或业务维度时，写成“同一主题下的不同字段”，并列出每个字段的来源、覆盖范围和值域证据。`rel` 写成行级匹配关系，说明匹配列、覆盖范围、唯一性和基数证据。

已有 `disambig` 能说明当前这组容易混淆字段时，整理成完整 group；连接列不完整时删除旧实体并用 `create_entity.edges` 重建。创建实体时只连接涉及字段，edge ref 使用列路径。

实体 ref 使用简短 snake_case 名称加标签，例如 `school_name_boundary:disambig`、`stable_identifier_join:rel`。中文写 brief/detail，措辞强调边界和差异。
"""


CANDIDATE_PROMPT_HEADER = (
    "## 待审查 relation/disambiguation 候选\n\n"
    "逐组整理候选事实，决定写为完整 group disambig、pair disambig、rel，或保留为 overlap 线索。\n"
    "`linked_disambig` 展示当前实际边覆盖；候选字段缺少对应覆盖时，整理或重建相关 disambig。\n"
)


def _render_candidate_prompt(
    groups: list[CandidateGroup],
    *,
    batch_index: int,
    batch_count: int,
    start_index: int,
    total_count: int,
) -> str:
    if not groups:
        return ""

    lines = [CANDIDATE_PROMPT_HEADER]
    lines.append(f"本批次：{batch_index}/{batch_count}；候选范围：{start_index}-{start_index + len(groups) - 1} / {total_count}。")
    lines.append("")
    for offset, group in enumerate(groups):
        idx = start_index + offset
        lines.append(f"### 候选 {idx}: {group.title}")
        if group.relation_ref:
            lines.append(f"- 候选来源实体名：`{group.relation_ref}`")
        if group.note:
            lines.append(f"- 线索：{group.note}")
        coverage_note = _coverage_gap_note(group)
        if coverage_note:
            lines.append(f"- 覆盖状态：{coverage_note}")
        lines.append("- 实体：")
        for col in group.columns:
            table_part = f" [{col.table}]" if col.table else ""
            parts = []
            if col.official_column_description:
                parts.append(f"official_column={col.official_column_description}")
            if col.official_value_description:
                parts.append(f"official_value={col.official_value_description}")
            if col.brief:
                parts.append(f"brief={col.brief}")
            if col.disambig_links:
                parts.append(f"linked_disambig={' | '.join(col.disambig_links)}")
            suffix = f" — {'；'.join(parts)}" if parts else ""
            lines.append(f"  - `{col.ref}`{table_part}{suffix}")
        lines.append("")
    return "\n".join(lines)


def _coverage_gap_note(group: CandidateGroup) -> str:
    linked_by_entity: dict[str, list[str]] = {}
    for col in group.columns:
        for link in col.disambig_links:
            name = link.split("[", 1)[0].strip()
            if name:
                linked_by_entity.setdefault(name, []).append(_display_col(col))

    notes = []
    all_cols = {_display_col(col) for col in group.columns}
    for name, linked_cols in sorted(linked_by_entity.items()):
        linked_set = set(linked_cols)
        missing = sorted(all_cols - linked_set)
        if linked_set and missing:
            notes.append(
                f"{name} 已连接 {', '.join(sorted(linked_set))}；候选内未连接 {', '.join(missing)}"
            )
    return " | ".join(notes)[:700]


def _display_col(col) -> str:
    return f"{col.table}.{col.name}" if col.table else col.name


def generate(workspace: Workspace) -> dict:
    """Review overlap candidates and write rel/disambig entities."""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping relation/disambiguation review")
        return {}

    logger.info("=== Agent Relation/Disambiguation Review ===")

    spec = explorer_writer_spec(
        workspace,
        tools=[
            "find", "meta", "query",
            "create_entity", "update_meta", "delete",
        ],
        include_readme=True,
    )
    candidate_groups = build_candidate_groups(workspace)
    if not candidate_groups:
        agent = create_agent(workspace.project_path, spec)
        agent.chat(PROMPT)
        logger.info("=== Agent Relation/Disambiguation Review done ===")
        return _preprocess_metrics(agent)

    batches = candidate_batches(candidate_groups, MAX_CANDIDATES_PER_AGENT)
    logger.info(
        "Generated relation/disambiguation candidate list: %d candidates in %d batches",
        len(candidate_groups),
        len(batches),
    )
    total_metrics: dict[str, int] = {}
    for batch_index, batch in enumerate(batches, start=1):
        batch_agent = create_agent(workspace.project_path, spec)
        refreshed_groups = build_candidate_groups(workspace)
        start = (batch_index - 1) * MAX_CANDIDATES_PER_AGENT
        batch = refreshed_groups[start:start + MAX_CANDIDATES_PER_AGENT]
        candidate_prompt = _render_candidate_prompt(
            batch,
            batch_index=batch_index,
            batch_count=len(batches),
            start_index=start + 1,
            total_count=len(candidate_groups),
        )
        logger.info(
            "Running relation/disambiguation batch %d/%d (%d candidates)",
            batch_index,
            len(batches),
            len(batch),
        )
        batch_agent.chat(f"{PROMPT}\n\n{candidate_prompt}")
        _add_metrics(total_metrics, _preprocess_metrics(batch_agent))
    logger.info("=== Agent Relation/Disambiguation Review done ===")
    return total_metrics


def _preprocess_metrics(agent) -> dict:
    if not hasattr(agent, "llm_metrics"):
        return {}
    metrics = agent.llm_metrics()
    return {
        "preprocess_llm_calls": int(metrics.get("llm_rounds", 0) or 0),
        "preprocess_llm_input_tokens": int(metrics.get("input_tokens", 0) or 0),
        "preprocess_llm_cached_input_tokens": int(metrics.get("cached_input_tokens", 0) or 0),
        "preprocess_llm_uncached_input_tokens": int(metrics.get("uncached_input_tokens", 0) or 0),
        "preprocess_llm_output_tokens": int(metrics.get("output_tokens", 0) or 0),
        "preprocess_llm_total_tokens": int(metrics.get("total_tokens", 0) or 0),
    }


def _add_metrics(total: dict[str, int], metrics: dict) -> None:
    for key, value in metrics.items():
        total[key] = int(total.get(key, 0) or 0) + int(value or 0)
