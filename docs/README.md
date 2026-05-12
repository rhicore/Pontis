# Docs Guide

This directory now keeps only the documents that still describe the current system shape or near-term refactors in the top-level structure.

## Active Docs

### Architecture

- [architecture/store_architecture.md](architecture/store_architecture.md): current storage-layer structure and boundaries.
- [architecture/storage_flat_source_modules_plan.md](architecture/storage_flat_source_modules_plan.md): current plan for flattening `storage/stores/*` modules.

### Agent

- [agent/guardrail_api.md](agent/guardrail_api.md): guardrail execution model and shared types.

### Extractors

- [extractors/DBT.md](extractors/DBT.md): dbt project extraction ideas.
- [extractors/ai_summary.md](extractors/ai_summary.md): AI summary targets by entity type.
- [extractors/experience_extraction_design.md](extractors/experience_extraction_design.md): benchmark experience extraction and transferable knowledge.
- [extractors/join_column_modules.md](extractors/join_column_modules.md): join-column detection module design.
- [extractors/join_column_search_strategy.md](extractors/join_column_search_strategy.md): background research for join-column search.
- [extractors/json_pattern_test.md](extractors/json_pattern_test.md): `json_pattern` behavior examples.
- [extractors/json的探查工具.md](extractors/json%E7%9A%84%E6%8E%A2%E6%9F%A5%E5%B7%A5%E5%85%B7.md): early notes for serialized-file exploration.

## Archive

Archived material lives under `docs/archive/` and is kept only for historical context:

- `archive/benchmark/bird/`: one-off benchmark error analyses and dataset notes.
- `archive/external-research/`: external system and paper research notes.
- `archive/superseded/`: earlier design drafts replaced by newer architecture plans.
- `archive/notes/`: legacy scratch notes and TODOs that are no longer the project source of truth.

## Rule Of Thumb

If a document still guides current implementation decisions, keep it in `architecture/`, `agent/`, or `extractors/`.
If it is benchmark-specific, external research, or replaced by a newer plan, move it to `archive/`.
