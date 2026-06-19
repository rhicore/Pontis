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


TEXT_NORMALIZATION_RULES = (
    (("数据范围不值域重叠",), "数据范围不同"),
    (("不值域重叠",), "不一致"),
    (("值域高度值域重叠",), "值域高度重叠"),
    (("<->", "↔"), " 与 "),
    (("->",), " 到 "),
    (("含义相同", "描述相同"), "字段边界接近但需区分"),
    (("内容一致", "值一致", "值域完全相同", "值域完全一致", "完全一致", "基本一致", "高度一致"), "值域高度重叠"),
    (("相同值",), "重叠值"),
    (("等价", "同义"), "需要按字段边界区分"),
    (("可替代", "可互换", "互换"), "需要按字段边界区分"),
    (("一一对应", "对应关系"), "稳定映射"),
    (("选任一字段均可", "选任一均可", "任一字段均可", "任一均可", "选任一"), "需要按输出和过滤语境选择字段"),
    (("无差异", "不影响"), "差异较小但仍需按字段角色确认"),
    (("可安全", "可无条件"), "经验证后"),
    (("替代 JOIN", "绕过主键"), "候选连接"),
)

REVIEW_TEXT_MARKERS = tuple(marker for markers, _ in TEXT_NORMALIZATION_RULES for marker in markers)


PROMPT = """\
你的任务是把 overlap 候选整理成可复用的字段关系知识。常规产物是 `disambig`；候选列本身构成独立、稳定的行级连接键时写 `rel`。

`overlap` 是候选证据，不是结论。字段名、official 描述和值域证据共同决定候选应成为字段选择边界、行级连接关系，还是保留为普通 overlap 线索。

## 判断原则

- `disambig` 记录字段事实差异，帮助下游按自然语言语境选择正确字段。
- `rel` 记录候选列自身提供的行级连接关系，适用于主键、外键、唯一标识或稳定业务编号。
- 属性列、低基数枚举、bool、分类文本、描述文本、短代码和统计值重叠，优先作为字段选择边界审查。
- 已有主键、外键或更强连接能解释两表行级关系时，普通属性列 overlap 进入 `disambig` 审查。
- 多个字段属于同一选择维度时，维护一个 group `disambig`，连接该维度下全部相关字段；独立二元选择使用 pair `disambig`。
- 同结构槽位字段属于同一字段族，完整 group 包含全部槽位；低质量、空值多或官方标注不可用的槽位也进入 group，并在字段边界中写明使用风险。
- 候选列的 `linked_disambig` 表示当前实际边覆盖；detail 文字只解释字段边界，覆盖范围由连接列决定。
- 候选组出现“部分字段已连接到同一 disambig、其余字段未连接”的覆盖缺口时，优先整理成完整 group。

## 审查口径

- 优先使用 `official_column_description` 和 `official_value_description`，再参考 agent 写入的 `brief/detail`。
- 字段差异包括来源表、行粒度、覆盖范围、编码体系、值域、空值、行过滤、输出角色、连接后果和统计口径。
- 代码列、名称列、类型列、状态列、范围端点列、同结构槽位列、跨表同名/近名列，按字段选择维度审查。
- 数值范围偶然相交且自然语言入口、输出角色、过滤角色和连接后果均不相交的候选，保留为 overlap 线索。
- `rel` 需要元数据或查询结果证明等值连接能保持正确行级基数，并且连接语义独立于已有更强键。

## 写入格式

`disambig` detail 写成固定结构：选择维度、字段边界、选择规则、错误后果、值域证据。

brief/detail 只写数据库事实：每个字段是什么、差异维度是什么、什么语境选哪个字段、错误选择会造成什么 SQL 后果。

已有 `disambig` 能承载当前选择维度时，整理成完整 group；连接列不完整时删除旧实体并用 `create_entity.edges` 重建。创建实体时只连接涉及字段，edge ref 使用列路径。

实体 ref 使用简短 snake_case 名称加标签，例如 `school_name_choice:disambig`、`stable_identifier_join:rel`。中文写 brief/detail，措辞强调边界和差异，使下游按事实选择字段。
"""


CANDIDATE_PROMPT_HEADER = (
    "## 待审查 relation/disambiguation 候选\n\n"
    "逐组判断候选应写为完整 group disambig、pair disambig、rel，或保留为 overlap 线索。\n"
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
        _normalize_review_entities(workspace)
        _add_metrics(total_metrics, _preprocess_metrics(batch_agent))
    _normalize_review_entities(workspace)
    logger.info("=== Agent Relation/Disambiguation Review done ===")
    return total_metrics


def _normalize_review_entities(workspace: Workspace) -> None:
    """Keep rel/disambig writeback safe for downstream schema linking."""
    _delete_misleading_rel_entities(workspace)
    _rewrite_misleading_disambig_text(workspace)


def _delete_misleading_rel_entities(workspace: Workspace) -> None:
    workspace.cypher(
        """
        MATCH (n)
        WHERE 'rel' IN coalesce(n.labels, [])
          AND any(marker IN $markers WHERE
            coalesce(n.name, '') CONTAINS marker
            OR coalesce(n.brief, '') CONTAINS marker
            OR coalesce(n.detail, '') CONTAINS marker
        )
        DETACH DELETE n
        """,
        params={"markers": list(REVIEW_TEXT_MARKERS)},
    )


def _rewrite_misleading_disambig_text(workspace: Workspace) -> None:
    rows = workspace.cypher(
        """
        MATCH (n)
        WHERE 'disambig' IN coalesce(n.labels, [])
        RETURN id(n) AS id, n.brief AS brief, n.detail AS detail
        """
    )
    for row in rows:
        fields = {}
        brief = _rewrite_text(str(row.get("brief") or ""))
        detail = _rewrite_text(str(row.get("detail") or ""))
        if brief != str(row.get("brief") or ""):
            fields["brief"] = brief
        if detail != str(row.get("detail") or ""):
            fields["detail"] = detail
        if not fields:
            continue
        workspace.cypher(
            """
            MATCH (n)
            WHERE id(n) = $id
            SET n += $fields
            """,
            params={"id": row["id"], "fields": fields},
        )


def _rewrite_text(text: str) -> str:
    for markers, replacement in TEXT_NORMALIZATION_RULES:
        for marker in markers:
            text = text.replace(marker, replacement)
    return text


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
