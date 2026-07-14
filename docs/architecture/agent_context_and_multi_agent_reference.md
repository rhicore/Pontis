# Agent Context and Forked Workers

This document describes the current Pontis context and child-agent contract.

## Context Layout

Stable leading context contains the rendered system prompt and tool definitions.
Per-task content belongs in messages:

- the user instruction and benchmark evidence;
- retrieved graph metadata;
- query results;
- prior worker reports and unresolved decisions.

Keeping static instructions stable improves provider prefix caching and prevents
dynamic evidence from silently changing the tool or ontology contract.

## Fork Semantics

`agent/fork.py` creates a new `PontusAgent` that inherits:

- the parent's rendered system prompt;
- a legalized snapshot of the parent's messages;
- the exact tool pool and model configuration;
- the same active storage projects.

The fork has isolated message history, tool history, round accounting, and
guardrail instances. It does not mutate the parent's conversation or graph
unless its inherited tools explicitly permit graph writes.

If a fork is started while the parent has an incomplete tool-call batch, Pontis
adds inert placeholder results only for missing calls so the copied history is a
valid chat sequence. Orphan tool messages are dropped.

## Worker Contract

Every worker receives a scoped directive and must return:

```text
Scope: <one sentence>
Result: <key findings>
Relevant context: <refs, hints, and decisions, or none>
Recommended checks: <what the main agent should verify>
```

Workers must not return final SQL for the main task. They gather bounded evidence
for the main agent, which remains responsible for validation and final output.

Fork recursion is blocked: an Agent already inside a fork cannot call the
`agent` tool to create another worker.

## Scoping Guidance

Use a fork only when the task can be bounded by explicit graph refs or a concrete
verification question. Good scopes include:

- inspect the columns of one large standalone table;
- compare two candidate join paths and report row-grain consequences;
- identify the relevant member of one table group;
- inspect one topic's table groups and standalone tables.

Do not send an unconstrained full-database exploration task to a worker. Pass the
original instruction, relevant external-knowledge excerpt, selected refs, and an
explicit report schema.

## Guardrails

Fork guardrails are rebuilt from the parent's registered guardrail builder names
with a fork-specific round limit. Runtime-only guardrails without a registered
builder name are not copied automatically. A recursion-block guardrail is always
added.

Guardrail output must remain visible in the event stream. Context replacement,
tool-phase finalization, appended messages, and trace-only events must not be
hidden from logs.

## Boundaries

- A worker report is evidence, not an accepted database fact.
- The main agent must verify claims that can change physical table, column, join,
  filter, or aggregation choices.
- Dataset-specific rules stay in dataset prompts and workflows; the generic fork
  runner is dataset-neutral.
- Multi-candidate challenger/judge orchestration is not part of the current fork
  runtime. Add a separate contract if such a controller is implemented.
