"""Agent Value-Domain Review — validate domains and derive rel/disambig entities."""
from __future__ import annotations

import logging

from storage.workspace import Workspace

from explorer.utils.value_domain_candidates import (
    MAX_DOMAINS_PER_AGENT,
    ValueDomainCandidate,
    build_value_domain_candidates,
    candidate_batches,
)

logger = logging.getLogger(__name__)
MAX_COMPLETION_PASSES = 2


PROMPT = """\
你是 Pontis 的 value-domain review agent。静态 extractor 已把存在实际共同值的 standalone `col` 和 `logical_col` 聚成候选 `value_domain`。你的任务是逐域审核其语义边界，并从中发现可靠的 `rel` 或必要的 `disambig`。

`value_domain` 只表示成员值集合存在交集，不表示任意两个成员都能直接 JOIN，也不表示所有成员属于同一种业务对象。不要把一个多成员域展开成两两完全连接的 `rel`。

## 每个域必须完成的判断

1. `accepted`：所有成员使用同一个可复用编码体系或实体标识空间，例如机场代码、客户 ID、专利 ID。用 `update_meta` 设置 `review_status=accepted`，并写清域的对象、格式、覆盖范围和例外。
2. `needs_split`：域中存在两个或更多不同语义子域。设置 `review_status=needs_split`；detail 明确列出每个建议子域包含哪些成员，以及误并原因。不要删除 extractor 产生的边。
3. `rejected`：共同值只是低基数枚举、小整数、日期、布尔值、统计量或偶然碰撞，整个域不构成可复用值域。设置 `review_status=rejected`，写清证据。

每个候选域都必须调用一次 `update_meta`，不能只创建 `rel/disambig` 而不更新域状态。

## `rel` 与 `disambig`

- 只有特定成员之间存在稳定行级匹配时才创建 `rel`。证据包括主外键语义、唯一业务标识、稳定编码映射、连接覆盖率与基数。共享值域本身不够。
- `rel` 只连接被证明具有该关系的成员子集；域里其他成员不要顺带连接。
- 字段名字或格式相似、容易被错选，但实际代表不同对象、不同粒度或不同编码体系时，创建 `disambig`。
- 同一编码域中的主键、外键或别名列可以建立 `rel`；同域中的来源/目的、起点/终点、当前/历史等角色列通常需要 `disambig`，是否建立 `rel` 取决于行级匹配证据。
- `logical_col` 是表组同一列角色的联合值集合。审核它时查看其物理成员，但关系实体优先连接 `logical_col`，不要为每个分片重复创建相同关系。

## 证据检查

- 优先读 `official_column_description`、`official_value_description`、类型、cardinality、sample、topk 和 extractor evidence。
- 必要时用 `meta` 展开成员，用 `find` 查看相邻表、逻辑列或已有关系，用 `query` 查询少量 distinct 值、JOIN 覆盖率、唯一性和连接基数。
- overlap/min 高只能说明较小集合被较大集合覆盖；要区分主外键、同编码不同角色、低基数类别和偶然包含。
- 数值 ID 尤其容易因共享小整数误并。表名、列语义和行粒度不同且没有稳定映射时应拆分或拒绝。
- extractor evidence 是候选生成证据，不是最终语义结论。

## 写入格式

审核域：
`update_meta({"ref":"<value_domain_ref>","fields":{"review_status":"accepted|needs_split|rejected","brief":"...","detail":"..."}})`

创建关系：
`create_entity({"ref":"stable_identifier_join:rel","meta":{"brief":"...","detail":"..."},"edges":[{"ref":"<成员1>"},{"ref":"<成员2>"}]})`

创建消歧义：
`create_entity({"ref":"identifier_role_choice:disambig","meta":{"brief":"...","detail":"..."},"edges":[{"ref":"<成员1>"},{"ref":"<成员2>"}]})`

brief/detail 使用中文；实体 ref 使用简短 snake_case。完成本批全部域后回复 `DONE`。
"""


CANDIDATE_PROMPT_HEADER = """\
## 待审核 value domains

逐域检查全部成员。每个域都必须更新 review_status；只为有明确证据的成员子集创建 rel/disambig。
"""


def render_candidate_prompt(
    candidates: list[ValueDomainCandidate],
    *,
    batch_index: int,
    batch_count: int,
    start_index: int,
    total_count: int,
) -> str:
    if not candidates:
        return ""
    lines = [CANDIDATE_PROMPT_HEADER]
    lines.append(
        f"本批次：{batch_index}/{batch_count}；域范围："
        f"{start_index}-{start_index + len(candidates) - 1} / {total_count}。"
    )
    lines.append("")
    for offset, candidate in enumerate(candidates):
        index = start_index + offset
        lines.extend([
            f"### 域 {index}: {candidate.name}",
            f"- ref: `{candidate.ref}`",
            f"- schema: `{candidate.schema or 'unknown'}`; status: `{candidate.review_status}`",
            f"- union cardinality: {candidate.union_cardinality}; members: {len(candidate.members)}",
            f"- metric: `{candidate.overlap_metric}`; threshold: {candidate.overlap_threshold}; "
            f"anchor support: {candidate.min_anchor_support}",
        ])
        if candidate.semantic_roles:
            lines.append(f"- semantic roles: {candidate.semantic_roles}")
        if candidate.extraction_evidence:
            lines.append(f"- extractor evidence: {candidate.extraction_evidence}")
        lines.append("- members:")
        for member in candidate.members:
            facts = [f"kind={member.kind}"]
            if member.table:
                facts.append(f"table={member.table}")
            if member.data_type:
                facts.append(f"type={member.data_type}")
            if member.role:
                facts.append(f"role={member.role}")
            if member.cardinality is not None:
                facts.append(f"cardinality={member.cardinality}")
            if member.kind == "logical_col":
                facts.append(f"physical_members={member.member_count}")
            lines.append(f"  - `{member.ref}` ({'; '.join(facts)})")
            for label, value in (
                ("official_column", member.official_column_description),
                ("official_value", member.official_value_description),
                ("brief", member.brief),
                ("sample", member.sample),
                ("topk", member.topk),
            ):
                if value:
                    lines.append(f"    - {label}: {value}")
            if member.physical_members:
                preview = ", ".join(f"`{ref}`" for ref in member.physical_members[:12])
                suffix = " ..." if len(member.physical_members) > 12 else ""
                lines.append(f"    - physical member refs: {preview}{suffix}")
        lines.append("")
    return "\n".join(lines)


def generate(workspace: Workspace) -> dict:
    """Review pending value domains and derive rel/disambig knowledge."""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping value-domain review")
        return {}

    candidates = build_value_domain_candidates(workspace)
    if not candidates:
        logger.info("No pending multi-member value domains to review")
        return {}

    spec = explorer_writer_spec(
        workspace,
        tools=["find", "meta", "query", "create_entity", "update_meta", "add_edge"],
        include_readme=True,
    )
    spec.meta_write_fields = ["brief", "detail", "review_status"]
    batches = candidate_batches(candidates, MAX_DOMAINS_PER_AGENT)
    logger.info(
        "=== Agent Value-Domain Review: %d domains in %d batches ===",
        len(candidates),
        len(batches),
    )
    total_metrics: dict[str, int] = {}
    for batch_index, batch in enumerate(batches, start=1):
        start = (batch_index - 1) * MAX_DOMAINS_PER_AGENT
        prompt = render_candidate_prompt(
            batch,
            batch_index=batch_index,
            batch_count=len(batches),
            start_index=start + 1,
            total_count=len(candidates),
        )
        agent = create_agent(workspace.project_path, spec)
        agent.chat(f"{PROMPT}\n\n{prompt}")
        _add_metrics(total_metrics, _preprocess_metrics(agent))

    for pass_index in range(1, MAX_COMPLETION_PASSES + 1):
        pending = build_value_domain_candidates(workspace)
        if not pending:
            logger.info("Value-domain review completeness check passed")
            logger.info("=== Agent Value-Domain Review done ===")
            return total_metrics
        logger.warning(
            "Value-domain review left %d pending domains; completion pass %d/%d",
            len(pending),
            pass_index,
            MAX_COMPLETION_PASSES,
        )
        pending_batches = candidate_batches(pending, MAX_DOMAINS_PER_AGENT)
        for retry_index, batch in enumerate(pending_batches, start=1):
            prompt = render_candidate_prompt(
                batch,
                batch_index=retry_index,
                batch_count=len(pending_batches),
                start_index=(retry_index - 1) * MAX_DOMAINS_PER_AGENT + 1,
                total_count=len(pending),
            )
            agent = create_agent(workspace.project_path, spec)
            agent.chat(
                f"{PROMPT}\n\n上一轮未完成以下域。必须逐个调用 update_meta 更新 review_status。\n\n{prompt}"
            )
            _add_metrics(total_metrics, _preprocess_metrics(agent))

    pending = build_value_domain_candidates(workspace)
    if pending:
        sample = "\n".join(f"- {candidate.ref}" for candidate in pending[:40])
        raise RuntimeError(
            f"Value-domain review 未完成；仍有 {len(pending)} 个 pending_review 域。\n{sample}"
        )
    logger.info("=== Agent Value-Domain Review done ===")
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
