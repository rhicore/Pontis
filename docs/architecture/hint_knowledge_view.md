# Hint Knowledge View

This document defines the currently supported hint storage and display contract.

## Two Storage Forms

Pontis supports:

1. an entity-local `hints` property for short facts attached to one table,
   column, or other entity;
2. a neighboring `hint` entity for a fact whose meaning depends on multiple
   entities.

`meta(entity)` combines both forms into one `Hints` section. Callers should not
need to know which storage form supplied a line.

## Local Hints

Use the local property when the fact belongs to one entity and should appear
whenever that entity is read. Examples include:

- table row grain;
- a column's unit, enum meaning, date format, or null convention;
- a warning that one identifier is not unique at the table's grain.

`update_meta` accepts `hints` as a list or text and stores a normalized list. It
replaces the complete local hint list, so writers must retain existing correct
items when adding new ones.

```text
update_meta({
  "ref":"<entity ref>",
  "fields":{"hints":["One row represents a monthly account snapshot."]}
})
```

## Hint Entities

Use a `hint` entity only when the fact inherently compares several entities or
a path, for example:

- two similar fields have different business roles;
- a join expands rows or removes unmatched entities;
- a bridge table represents endpoint pairs rather than independent entities;
- a measure belongs to one of several related tables.

Connect the hint to every entity needed to discover it. A disconnected knowledge
node is not retrievable from normal graph navigation and must not be created.

Do not duplicate ownership metadata such as table or schema names when graph
edges already express it. Put the short conclusion in `brief`, evidence and
boundaries in `detail`, and keep membership in edges.

## Current Producers

The active BIRD pipeline uses `explorer/bird_profile.py` to write local hints.
Generic historical `entity_hints` explorers are archived under
`explorer/useless/` and are not part of the preprocessing registry.

No automatic hint-specific sidechain is currently enabled. Hints become visible
when the Agent reads an entity with `meta`; normal prompt and guardrail logic may
then use them. A future sidechain must be documented separately when it is
actually registered.

## Content Boundary

Hints may state database facts and their structural consequences:

- row grain and key uniqueness;
- field-role and value-format boundaries;
- join coverage, fanout, and unmatched-row behavior;
- bridge, snapshot, event, mapping, or dimension-table shape.

Do not write hidden-answer knowledge, sample-specific Golden SQL, or an
unverifiable business preference as a generic database hint. Dataset-specific
preferences must be clearly attributed to the relevant benchmark workflow.

## Visibility Invariants

- A local hint is visible from its owning entity.
- A cross-entity hint must connect to every relevant entity.
- The same detailed fact has one source of truth; short local restatements are
  allowed only when they materially improve retrieval.
- `disambig` records semantic boundaries between confusing entities; `hint`
  records other reusable decision facts. Do not create both for the same fact
  without a clear reason.
