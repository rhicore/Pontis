"""Agent Column-Domain Review — validate domains and derive rel/disambig entities."""
from __future__ import annotations

import logging

from storage.workspace import Workspace

from explorer.utils.column_domain_candidates import (
    MAX_DOMAINS_PER_AGENT,
    ColumnDomainCandidate,
    build_column_domain_candidates,
    candidate_batches,
)

logger = logging.getLogger(__name__)
MAX_COMPLETION_PASSES = 2


PROMPT = """\
你是 Pontis 的 column-domain 审核员。静态 extractor 已把具有共同值证据的列组织成 `column_domain`。你要逐域确认它代表的业务标识空间，并把审核结论转成主 agent 可直接使用的关系知识。

## 实体职责

- `column_domain` 保存机器候选、证据和审核状态。
- `fk` 保存数据库 schema 已声明的列连接。
- `rel` 保存 schema 未声明、但业务语义和行级数据共同支持的稳定连接。
- `disambig` 保存相似候选之间会影响字段选择的边界。

一个稳定关系由 `fk` 或 `rel` 中的一种表达。多成员 domain 按实际关系连接成员子集；`logical_col` 代表一组分表中的同一列角色，关系优先连接 logical_col。

多成员 domain 按“尚未表达的连接”拆分产物。例如 A-B 已有 fk，而 C-A 经验证可稳定连接，则新 rel 的边只连接 C 和 A；B-A 继续由原 fk 表达。domain 本身保留 A、B、C 的共享编码空间结论。

## Domain 结论

- `accepted`：成员共享可复用的编码体系或实体标识空间。
- `needs_split`：共同值证据合并了两个或更多业务子域；detail 写明建议子域及形成误并的原因。
- `rejected`：交集来自普通枚举、小整数、日期、布尔、统计量或偶然碰撞，不形成可复用业务值域。

## 审核流程

1. 读取成员的 official description、类型、cardinality、sample、topk、extractor evidence，以及邻接的 `fk/rel/disambig`。
2. 结合对象含义、格式、行粒度和 overlap 证据确定 domain 状态。标记为 approximate 的 topk 使用其误差界理解频次。
3. 证据仍不足时，用 query 核验唯一性、匹配覆盖率或连接基数；每个 domain 最多执行 3 次针对性查询。
4. 每个候选 domain 调用一次 update_meta，写入 review_status、brief 和 detail。
5. 已有 fk/rel 完整承载连接时沿用该实体；发现稳定的非 schema 连接时创建 rel；发现真实字段选择边界时创建 disambig。

## 关系知识写作

关系实体的 metadata 是业务摘要，图边和成员自身 metadata 提供结构明细：

- `rel.brief` 是不超过 50 字的业务关系名词短语，例如“交易与客户的标识关联”。
- `rel.detail` 说明该连接支持什么业务导航、证据质量和已知例外，例如“用于从交易定位客户信息；当前交易记录均可稳定匹配”。该摘要以业务角色表达，Related 区域负责显示具体端点。
- `disambig.brief` 命名共同的混淆主题；`disambig.detail` 说明混淆触发词和选择规则。
- 端点身份、成员清单、主外键角色和各列 cardinality 从 Related 边及成员 metadata 读取。

## 写入格式

审核域：
`update_meta({"ref":"<column_domain_ref>","fields":{"review_status":"accepted|needs_split|rejected","brief":"...","detail":"..."}})`

创建关系：
`create_entity({"ref":"stable_identifier_join:rel","meta":{"brief":"...","detail":"..."},"edges":[{"ref":"<成员1>"},{"ref":"<成员2>"}]})`

创建消歧义：
`create_entity({"ref":"identifier_role_choice:disambig","meta":{"brief":"...","detail":"..."},"edges":[{"ref":"<成员1>"},{"ref":"<成员2>"}]})`

brief/detail 使用中文；实体 ref 使用简短 snake_case。完成本批全部域后回复 `DONE`。
"""


CANDIDATE_PROMPT_HEADER = """\
## 待审核 column domains

逐域检查全部成员。每个域都必须更新 review_status；只为有明确证据的成员子集创建 rel/disambig。
"""


def render_candidate_prompt(
    candidates: list[ColumnDomainCandidate],
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
            f"- extraction strategy: `{candidate.extraction_strategy or 'unknown'}`",
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
    """Review pending column domains and derive rel/disambig knowledge."""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping column-domain review")
        return {}

    candidates = build_column_domain_candidates(workspace)
    if not candidates:
        logger.info("No pending multi-member column domains to review")
        return {}

    spec = explorer_writer_spec(
        workspace,
        tools=["find", "meta", "query", "create_entity", "update_meta", "add_edge"],
        include_readme=False,
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
        pending = build_column_domain_candidates(workspace)
        if not pending:
            logger.info("Column-domain review completeness check passed")
            logger.info("=== Agent Value-Domain Review done ===")
            return total_metrics
        logger.warning(
            "Column-domain review left %d pending domains; completion pass %d/%d",
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

    pending = build_column_domain_candidates(workspace)
    if pending:
        sample = "\n".join(f"- {candidate.ref}" for candidate in pending[:40])
        raise RuntimeError(
            f"Column-domain review 未完成；仍有 {len(pending)} 个 pending_review 域。\n{sample}"
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
