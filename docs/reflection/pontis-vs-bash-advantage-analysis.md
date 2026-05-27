# Pontis vs Bash Agent Advantage Analysis

Compared runs:

- Pontis: `workspace/baselines/pontis/runtime_logs/bird_dev_bird_dev_full_noglobal_reflection_20260522`
- Bash agent: `workspace/baselines/bash_agent/runtime_logs/bird_dev_bash_agent_bird_dev_full`

Pontis run has 1532 completed query logs. `european_football_2/q1137` and `q1148` were missing because the run was stopped while two queries were stuck.

## Overall Result

On the 1532 common completed questions:

| Set | Count |
|---|---:|
| Both correct | 865 |
| Both wrong | 467 |
| Pontis correct, bash wrong | 145 |
| Bash correct, Pontis wrong | 55 |

Pontis net gain over bash on the common set: **+90 questions**.

Full parsed correctness:

| Method | Parsed questions | Correct |
|---|---:|---:|
| Bash agent | 1534 | 920 |
| Pontis | 1532 | 1010 |

## Per-Database Delta

| Database | Common Q | Pontis Only | Bash Only | Both Correct | Both Wrong | Pontis Correct | Bash Correct |
|---|---:|---:|---:|---:|---:|---:|---:|
| california_schools | 89 | 14 | 8 | 40 | 27 | 54 | 48 |
| card_games | 191 | 15 | 2 | 101 | 73 | 116 | 103 |
| codebase_community | 186 | 14 | 4 | 115 | 53 | 129 | 119 |
| debit_card_specializing | 64 | 8 | 0 | 39 | 17 | 47 | 39 |
| european_football_2 | 127 | 5 | 3 | 83 | 36 | 88 | 86 |
| financial | 106 | 7 | 1 | 64 | 34 | 71 | 65 |
| formula_1 | 174 | 27 | 6 | 80 | 61 | 107 | 86 |
| student_club | 158 | 10 | 12 | 107 | 29 | 117 | 119 |
| superhero | 129 | 9 | 4 | 100 | 16 | 109 | 104 |
| thrombosis_prediction | 163 | 17 | 7 | 65 | 74 | 82 | 72 |
| toxicology | 145 | 19 | 8 | 71 | 47 | 90 | 79 |

Pontis advantage is strongest in:

1. `formula_1`: +21 net
2. `toxicology`: +11 net
3. `card_games`: +13 net
4. `thrombosis_prediction`: +10 net
5. `codebase_community`: +10 net
6. `debit_card_specializing`: +8 net

`student_club` is the only database where bash is slightly better on this run.

## Pontis-Only Correct: Bash Failure Modes

For the 145 questions where Pontis is correct and bash is wrong:

| Bash failure pattern | Approx. count |
|---|---:|
| Schema/formula choice error | 49 |
| Extra output columns / wrong output shape | 30 |
| Bash parse/final-answer error | 23 |
| Formatting/rounding/printf mismatch | 17 |
| Extra `ORDER BY` / `LIMIT` / aggregation presentation | 13 |
| Wrong `DISTINCT` / row-vs-entity grain | 9 |
| Wrong join type | 2 |
| `LIKE` vs exact value matching | 2 |

These are heuristic counts based on SQL/log patterns, not a perfect taxonomy. They are still useful for identifying where Pontis is winning.

## Main Sources of Pontis Advantage

### 1. Column Briefs and Disambiguation Prevent Wrong Same-Name Field Choices

Pontis has extracted column summaries and disambiguation entities. Bash sees raw SQLite schema and samples, but often picks the most obvious column by name.

Example: `california_schools/q20`

Question asks Low Grade = 9 and High Grade = 12. Bash used:

```sql
SELECT COUNT(*) FROM schools WHERE County = 'Amador' AND GSoffered = '9-12'
```

Pontis used:

```sql
SELECT COUNT(*)
FROM schools s
JOIN frpm f ON s.CDSCode = f.CDSCode
WHERE s.County = 'Amador'
  AND f."Low Grade" = '9'
  AND f."High Grade" = '12'
```

Pontis log explicitly retrieved:

- `frpm.Low Grade`: 学校提供的最低年级
- `frpm.High Grade`: 学校提供的最高年级
- `grade_column_choice:disambig`: frpm年级 vs schools年级跨度字段选择

This is exactly the kind of advantage a graph knowledge layer should provide: resolving competing schema candidates before SQL generation.

### 2. Table-Level Entity Semantics Help in Complex Schemas

In databases like `formula_1`, many tables contain similar identifiers and time/rank fields. Pontis table summaries make row grain explicit.

Example: `formula_1/q860`

Question asks Q2 time in qualifying race 355. Pontis inspected `qualifying` metadata:

- one row per driver per qualifying race
- `q1/q2/q3` store qualifying stage lap times as text `M:SS.mmm`
- `driverId` FK to `drivers`

Pontis then wrote:

```sql
SELECT d.nationality
FROM qualifying q
JOIN drivers d ON q.driverId = d.driverId
WHERE q.raceId = 355 AND q.q2 LIKE '1:40%'
```

Bash produced `PARSE_ERROR` on this question. More broadly, many Pontis-only wins in `formula_1` come from finding the right table among `results`, `qualifying`, `lapTimes`, `pitStops`, `driverStandings`, and `constructorStandings`.

### 3. Pontis Better Preserves BIRD Output Contract

Bash often returns extra explanatory columns because they are useful to a human, but BIRD wants exact output shape.

Examples:

- `card_games/q353`: bash returned set code, set name, total size; Pontis returned only name and total size.
- `card_games/q390`: bash returned card id, colors, format; Pontis returned colors and format.
- `superhero/q763`: bash returned attribute name and value; Pontis returned only `attribute_value`.
- `student_club/q1383`: bash concatenated full name; Pontis returned `first_name, last_name`, matching golden.

This indicates Pontis benefits from the injected BIRD README and the graph context that makes output columns easier to identify.

### 4. Pontis Avoids Some Over-Formatting

Bash frequently uses `ROUND(...)` or `printf(...)` for percentages when the golden SQL expects a numeric value with SQLite's normal result type.

Examples:

- `toxicology/q226`: bash used `printf('%.5f', ...)`, outputting text; Pontis used `ROUND(..., 5)`.
- `toxicology/q227`: bash used `printf('%.3f', ...)`; Pontis used `ROUND(..., 3)`.
- `financial/q126`, `financial/q155`, `card_games/q401`, `superhero/q818`: bash rounded to two decimals; Pontis matched the unrounded or differently rounded golden expression more often.

This is not purely graph understanding; it is mostly BIRD style adherence. But Pontis currently has the BIRD README injected, so this counts as a system advantage in the current setup.

### 5. Pontis Handles Relationship/Bridge Tables Better in Some Domains

`toxicology` shows this clearly. Bash often expands a relationship into a more human-readable pair or explanation, while Pontis more often matches the benchmark's target output.

Examples:

- `toxicology/q243`: bash selected `bond_id, bond_type`; Pontis selected only `bond_id`.
- `toxicology/q253/q268`: bash returned two endpoint element columns; golden/Pontis returned the benchmark's single-column element output.
- `toxicology/q303`: bash used `LEFT JOIN` and kept unmatched rows; Pontis used the inner-join semantics matching golden.

The graph helps because bond/connected/atom/molecule roles are summarized and easier to retrieve as a connected schema path.

### 6. Pontis Does More Runtime Exploration

Pontis-only correct questions were usually harder and required more exploration.

Pontis average metrics:

| Set | Rounds/Q | Runtime Input/Q | Query Tool Calls/Q |
|---|---:|---:|---:|
| Pontis-only correct | 12.38 | 40,021 | 6.94 |
| Both correct | 9.08 | 12,598 | 3.98 |
| Bash-only correct | 10.65 | 25,262 | 5.82 |
| Both wrong | 12.62 | 44,911 | 7.37 |

Pontis-only correct questions also mention more graph markers:

| Set | Disambig markers/Q | rel/fk/overlap markers/Q |
|---|---:|---:|
| Pontis-only correct | 5.93 | 1.12 |
| Both correct | 4.35 | 0.38 |
| Bash-only correct | 3.27 | 0.78 |

This supports the interpretation that Pontis's gains come from heavier schema/context retrieval, not just chance.

## Representative Pontis-Only Wins

### `formula_1/q846`: Text Time Ordering

Bash:

```sql
ORDER BY CAST(q.q1 AS REAL) DESC LIMIT 5
```

Pontis:

```sql
ORDER BY q.q1 DESC LIMIT 5
```

Golden uses text ordering. Pontis avoided over-casting the lap-time string.

### `codebase_community/q540`: Owner User Join

Bash used `posts.OwnerDisplayName = 'csgillespie'`.

Pontis joined `posts.OwnerUserId = users.Id` and filtered `users.DisplayName`.

This is a schema-linking win: Pontis chose the normalized relation rather than a denormalized/display fallback.

### `debit_card_specializing/q1470`: Product vs Station Segment

Bash joined transaction product IDs and guessed Premium gas through a product relation.

Pontis used:

```sql
SELECT COUNT(*) FROM gasstations WHERE Country = 'CZE' AND Segment = 'Premium'
```

Golden uses the gas station segment. This suggests Pontis's table/column descriptions helped identify that "Premium gas" was a station segment in this database.

### `thrombosis_prediction/q1157`: Diagnosis Source

Bash used `Examination.Diagnosis`; Pontis used `Patient.Diagnosis`, matching golden.

This is a classic same-name/similar-concept field selection advantage.

### `student_club/q1376`: Do Not Aggregate When Golden Uses Row-Level Ratio

Bash grouped by event and used `SUM(spent)/SUM(amount)`.

Pontis used row-level:

```sql
ORDER BY b.spent * 1.0 / b.amount DESC LIMIT 1
```

Golden also uses row-level ratio. This is style plus row-grain matching.

## What This Says About Pontis's Current Advantages

Pontis is not simply "bash plus more tools." Its current advantage appears to come from four concrete mechanisms:

1. **Pre-extracted semantic metadata**: table briefs, column briefs, row grain, and field role descriptions reduce schema-linking errors.
2. **Disambiguation entities**: when the model actually retrieves them, they fix field-source choices that raw schema cannot make obvious.
3. **BIRD README injection**: helps avoid extra columns, extra formatting, unnecessary `DISTINCT`, and benchmark-unfriendly presentation.
4. **More deliberate exploration**: Pontis spends more LLM rounds and query calls on hard cases, which improves difficult schemas but increases cost.

The strongest evidence for graph value is in questions where bash had access to the same SQLite database but chose the wrong schema object:

- `california_schools/q20`: `schools.GSoffered` vs `frpm.Low Grade`/`High Grade`
- `codebase_community/q540`: `OwnerDisplayName` vs `users.DisplayName` through `OwnerUserId`
- `thrombosis_prediction/q1157`: `Examination.Diagnosis` vs `Patient.Diagnosis`
- `debit_card_specializing/q1470`: transaction/product path vs gas station `Segment`
- many `formula_1` cases: choosing among similarly named event/standing/timing tables

## Weaknesses Still Visible

Pontis's advantage is not uniform.

- `student_club` is slightly worse than bash on this run.
- Pontis-only wins often cost many more rounds; `formula_1/q860` took 37 LLM rounds.
- Some Pontis gains are BIRD style/readme wins rather than graph-understanding wins.
- Both systems still fail many `thrombosis_prediction`, `card_games`, and `formula_1` questions, so current graph knowledge is helpful but incomplete.

## Suggested Ablations

To isolate Pontis's true system advantage, run at least these ablations:

1. Pontis without BIRD README injection.
2. Pontis with table/column summaries but without disambig entities.
3. Pontis with disambig retrieval forced on candidate ambiguous fields.
4. Bash agent with the same BIRD README injected.
5. Pontis with strict round budget equal to bash average rounds.

Expected result:

- If README explains most gains, bash+README should close the gap on output-shape and formatting errors.
- If graph metadata explains most gains, Pontis should remain better on same-name field/table selection and complex join-path cases.
- If exploration budget explains most gains, equal-round Pontis will lose a nontrivial fraction of Pontis-only correct cases.
