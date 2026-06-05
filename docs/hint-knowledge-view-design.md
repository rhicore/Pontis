# Hint Knowledge View Design

## Goal

Pontis needs a lightweight way to expose database decision knowledge during
normal `meta` exploration without adding many special-purpose entity types.
The minimum shared abstraction is a **hint**:

- an entity-local `hints` property for facts that belong to one entity; and
- a neighboring `hint` entity for knowledge that connects multiple entities.

`meta(entity)` presents both sources through one `Hints` view, so agents can
consume the knowledge without knowing how it is stored.

## Storage Rule

Use the entity's `hints` property when the statement describes the entity
itself and should be visible whenever that table or column is read.

Examples:

- A table's row grain.
- A column's date format, unit, enum meaning, casing behavior, or text role.
- A column-specific warning such as "this identifier is not a school-level
  join key".

Create a neighboring `hint` entity when the statement describes a decision
across two or more entities.

Examples:

- Which column should receive a natural-language predicate such as type,
  category, status, language, date, or amount.
- Which join path preserves the intended row grain.
- Which table owns a measure when several related tables have similar numeric
  fields.
- A coverage or expansion issue that only appears after joining tables.

For important cross-entity decisions, create the `hint` entity as the source of
truth and also add a short entity-local hint to the involved entities. This
keeps the knowledge visible even when the sidechain has not completed.

## Meta View

`meta(entity)` should display a single `Hints` section built from:

1. the entity's own `hints` property; and
2. neighboring entities whose label includes `hint`.

The regular `Related` section can still show graph neighbors, but agents should
treat `Hints` as the primary view for decision knowledge.

## Explorer Roles

`disambiguate.py` remains focused on semantic ambiguity between competing
entities. It may add short local hints to the involved entities, but it should
not become a general database-knowledge extractor.

`entity_hints.py` extracts non-ambiguity decision knowledge:

- row grain,
- predicate landing,
- value behavior, and
- join coverage / measure ownership.

It writes single-entity knowledge to `hints` and cross-entity knowledge to
neighboring `hint` entities.

## Decision Knowledge Categories

Use hints for database-verifiable decision facts, not benchmark-answer priors.
The main reusable categories are:

- `field_landing`: competing columns that can receive the same natural-language
  phrase, with each column's role and SQL consequence.
- `fact_table_ownership`: which table owns a measure, event, history, snapshot,
  or entity attribute when related tables have similar fields.
- `row_grain`: what one row represents and when grouping or distinct counting is
  required to preserve the target entity.
- `value_format`: enum aliases, text numbers, date strings, null/empty formats,
  and raw-vs-normalized values.
- `join_consequence`: whether a join only adds attributes, expands rows, drops
  unmatched records, or changes the target entity set.
- `relationship_shape`: whether a bridge or structure table row represents an
  endpoint pair, membership relation, event, or other multi-column fact.

Each hint should be short enough to appear in normal `meta` output. Longer
evidence belongs in `detail`, with a one-line local hint copied to each involved
table or column when visibility matters.

## Guardrail Consumption

The meta-triggered sidechain should react to both disambiguation and hint
context:

- `disambig` neighbors trigger ambiguity analysis.
- `hint` neighbors and entity-local `hints` trigger relevance analysis for the
  current SQL decision.

The sidechain should not solve the full task. It reports only the hint or
disambiguation facts that may change table, column, value, join, or aggregation
choices.
