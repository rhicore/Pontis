# Pontis Docs

This directory keeps only documents that describe the current system, active
research direction, or near-term implementation decisions.

## Current Docs

### Architecture

- [architecture/store_architecture.md](architecture/store_architecture.md): current storage and source-module architecture.
- [architecture/storage_performance_report.md](architecture/storage_performance_report.md): storage/tool latency findings and remaining bottlenecks.
- [architecture/derived_entity_modeling_principles.md](architecture/derived_entity_modeling_principles.md): graph modeling rules for derived entities.
- [architecture/dev_evaluation_modes.md](architecture/dev_evaluation_modes.md): independent vs continuous dev evaluation modes.

### Agent

- [agent/guardrail_api.md](agent/guardrail_api.md): guardrail execution model and shared types.

### BIRD Benchmark

- [benchmark_analysis.md](benchmark_analysis.md): current BIRD error analysis, schema-linking metric definition, and experiment implications.

### Extractors

- [extractors/ai_summary.md](extractors/ai_summary.md): AI summary targets by entity type.
- [extractors/experience_extraction_design.md](extractors/experience_extraction_design.md): transferable benchmark knowledge extraction.
- [extractors/join_column_modules.md](extractors/join_column_modules.md): current join-column relation extraction design.
- [extractors/local_database_knowledge_extraction_plan.md](extractors/local_database_knowledge_extraction_plan.md): local database knowledge extraction plan.

## Maintenance Rule

Keep current source-of-truth docs here. One-off benchmark notes, copied prompt
dumps, external research notes, and superseded plans should stay out of this
directory unless they are actively used by implementation or thesis writing.
