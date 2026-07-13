# Spider2 Tidy Data and Refinement Problem

## Problem

Spider2-Snow contains many schemas that are not tidy data in the statistical
sense:

- one variable per column
- one observation per row
- one observation unit per table

Several Spider2 schemas expose physical warehouse layouts instead of clean
analysis-facing relations. Common cases include partition tables, wide
statistical cubes, and semi-structured event records.

## Examples

### Date Values Encoded As Columns

`COVID19_JHU_WORLD_BANK.COVID19_JHU_CSSE.CONFIRMED_CASES`,
`DEATHS`, and `RECOVERED_CASES` contain columns such as:

```text
_3_18_20, _12_3_22, _5_12_20, _7_18_20, ...
```

The date is a value, not a variable name. A tidier representation would expose
`date` as a column and store the case count in a measure column.

### Statistical Dimensions Encoded As Columns

`FEC.CENSUS_BUREAU_ACS.ZIP_CODES_2017_5YR` contains hundreds of columns such
as:

```text
male_5_to_9
female_45_to_49
income_100000_124999
commute_90_more_mins
rent_10_to_15_percent
```

These columns often encode metric, bucket, sex, age range, income range, or
commute range in the column name. A tidy/refined representation would split
these into dimension fields and a value field.

### One Observation Unit Split Across Many Tables

FEC tables such as:

```text
INDIV00, INDIV02, ..., INDIV20
CM00, CM02, ..., CM20
CCL00, CCL02, ..., CCL20
```

represent the same logical observation unit split by election cycle. GA4 tables
such as `EVENTS_20201101`, `EVENTS_20201102`, ... similarly represent date
partitions of one event relation.

### Nested Observation Units

GA4 event tables include `VARIANT` columns such as:

```text
EVENT_PARAMS
ITEMS
USER_PROPERTIES
DEVICE
GEO
TRAFFIC_SOURCE
```

These fields can contain nested sub-observations. They are not equivalent to
ordinary scalar columns.

## Relation To Column Groups

Current static column-group extraction is only a candidate-indexing step. It
can group columns that share obvious naming patterns, but it does not prove a
valid tidy transformation.

For example:

```text
income_10000_14999
income_15000_19999
income_20000_24999
```

A column group can identify an `income_*` family. A refinement needs additional
semantics:

- identifier columns
- dimension name, such as `income_bucket`
- measure name, such as `population` or `household_count`
- unpivot SQL template
- confidence and limitations

## Automation Boundary

Deterministic scripts are suitable for detecting structural candidates:

- partition table families
- date-like columns
- numeric bucket columns
- repeated prefix/suffix column families
- obvious wide-measure groups

Scripts should not directly rewrite the schema or create canonical refined
views without review. They can identify shape, but they cannot reliably decide
business semantics.

Agent or human review is needed for:

- naming dimensions and measures
- deciding whether several patterns belong to one refinement or multiple
  refinements
- distinguishing count, percentage, denominator, and derived metrics
- handling nested `VARIANT` structures
- deciding whether a refinement should actually be exposed to downstream agents

## Recommended Pontis Design

Keep physical schema extraction separate from semantic refinement:

1. Extract official tables, columns, table groups, and column groups.
2. Add deterministic `untidy/refinement_candidate` metadata when a pattern is
   clear.
3. Let an explorer agent review candidates and write a refinement detail.
4. Represent accepted refinements as KG entities connected to source tables and
   source columns.
5. Generate virtual SQL/view templates only from accepted refinements.

The physical schema should remain intact. Refinement should be an additional
semantic access layer, not a destructive schema rewrite.
