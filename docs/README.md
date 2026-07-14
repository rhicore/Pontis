# Pontis Documentation

`docs/` contains current contracts, operational guides, and dated research
records. Read current contracts first; dated reports describe one measured
state and are not automatically valid for the latest code.

## Current Contracts

| Area | Document | Purpose |
|---|---|---|
| Storage | [Storage Architecture](architecture/store_architecture.md) | Project routing, source modules, handles, and source-rooted refs |
| Graph modeling | [Derived Entity Modeling Principles](architecture/derived_entity_modeling_principles.md) | Keep ownership in edges and avoid duplicated metadata |
| Agent runtime | [Agent Context and Multi-Agent Reference](architecture/agent_context_and_multi_agent_reference.md) | Context layout, compression, and child-agent boundaries |
| Guardrails | [Guardrail API](agent/guardrail_api.md) | Verdict aggregation, tool-phase finalization, and post-tool hooks |
| Knowledge hints | [Hint Knowledge View](architecture/hint_knowledge_view.md) | Current `hints` and neighboring `hint` entity contract |
| Spider navigation | [Table Group KG Playbook](agent/table_group_kg_playbook.md) | How agents traverse large Spider2 graphs |
| Column relation discovery | [Column Domain Extraction and Review](explorer_and_extractors/column_relation_discovery.md) | Unified BIRD and Spider column-domain candidate paths |
| Spider2-Snow overview | [Spider2-Snow Dataset and Pontis Overview](spider_problem/spider2_snow_overview.md) | Dataset contract, scale, resources, joins, graph model, and current workflow |
| Wide schemas | [Spider2 Wide-Schema Refinement](spider_problem/spider2_wide_schema_refinement.md) | Implemented compression versus future within-table refinement |
| BIRD evaluator | [Business Correct Evaluator](bird/business-correct-evaluator.md) | Result-first BIRD business-correct semantics |
| Spider visibility | [Spider2-Snow Schema Visibility Policy](spider_problem/spider2_snow_schema_visibility_policy.md) | Which official resources are visible before solving |

## Operational Guides

- [BIRD Runtime Runbook](bird/runtime_runbook.md)
- [Development Evaluation Modes](architecture/dev_evaluation_modes.md)
- [Spider2 and BIRD Evaluation Semantics](spider_problem/spider2-bird-evaluation-semantics.md)

## Research Records

The following documents are evidence or design background, not live system
contracts:

- `reflection/`: benchmark and failure analyses tied to specific runs.
- `bird/*analysis*.md` and `bird/pontis-vs-*.md`: dated BIRD error studies.
- `spider_problem/*audit*.md` and `*_metrics.md`: measured Spider2 snapshots.
- [ReFoRCE Technical Summary](reference/reforce_technical_summary.md): external-method notes.
- [Relational Algebra and Query Trees](reference/relational_algebra_and_query_trees.md): conceptual background.
- [Pontis System Audit 2026-07-13](architecture/pontis-system-audit-20260713.md): maintained BIRD pipeline and runtime audit.

When a research record conflicts with code or a current contract, use this
order of authority:

1. executable code and tests;
2. current contract documents listed above;
3. dated audits and experiment reports;
4. external-method and conceptual notes.

## Maintenance Rules

- Put stable behavior and entity contracts in `architecture/`, `agent/`, or
  `explorer_and_extractors/`.
- Put benchmark-specific operating rules under `bird/` or `spider_problem/`.
- Include a date and measurement scope in experiment reports.
- Do not keep raw chat transcripts, completed TODO lists, or abandoned proposals
  in `docs/`.
- Do not describe an experimental module as the default pipeline. Link to the
  actual script that selects the pipeline.
