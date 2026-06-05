# Agent Context And Multi-Agent Reference

This document keeps only the implementation lessons that are useful for Pontis.
The original Claude Code and Codex research notes were removed from this docs
tree because they were one-off external source audits.

## Context Layout

Keep long-lived instructions in stable leading sections:

- system prompt
- tool definitions
- static benchmark or dataset rules, when enabled

Put per-query content in user messages:

- question and evidence
- retrieved metadata
- tool results
- generated SQL reports
- reviewer feedback

This layout is friendlier to provider prefix caching and prevents dynamic query
content from moving ahead of reusable instructions.

## Context Compression

A useful compression step preserves system messages and rewrites only the
dynamic conversation history into a compact user-level summary. The summary
should keep:

- current task
- important evidence and tool observations
- SQL reports already produced
- unresolved decisions
- previous reviewer or judge decisions

It should not rewrite the core system prompt or silently change tool
permissions.

## Multi-Agent Control

For Pontis SQL generation, main agent, challenger agents, and judge agents
should have the same database-reading and SQL-execution capability unless an
experiment explicitly tests a restricted role. Otherwise judge decisions may be
biased by weaker evidence access.

The practical division of labor is:

- Main agent writes the first SQL report from its explored path.
- Challenger agents start from compressed context plus prior reports and search
  for materially different schema-linking or SQL-organization paths.
- Judge compares all reports and may use tools to verify database facts before
  selecting the final SQL.

The controller should stay dataset-neutral. Dataset-specific rules belong in
dataset README or equivalent rule sources, not in the generic controller.

## Guardrail Interaction

Guardrails can trigger context rewrite or side conversations, but their outputs
should be explicit messages in the agent loop. A guardrail that changes context
should state what it preserved and what it replaced, so later logs remain
auditable.

Avoid hidden hard-coded corrections. If a rule is dataset-specific, store it in
that dataset's rule document and retrieve it normally.
