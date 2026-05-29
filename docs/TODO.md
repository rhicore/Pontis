# Pontis TODO

## Reduce extract-time local tool failures

Context: full BIRD dev extract run `20260529_163106_bird_dev_full_extract_20260529`
shows no job-level crash, but several local agent/tool failures waste LLM rounds and
can leave some optional metadata incomplete. These should be optimized before the
next full extract/benchmark cycle.

### 1. Make `update_meta.fields` tolerant to common hint inputs

Observed examples:

- `Tool error (update_meta): AttributeError: 'list' object has no attribute 'keys'`
- `Tool error (update_meta): AttributeError: 'str' object has no attribute 'keys'`

Cause:

- `tool/update_meta/tool.py` assumes `fields` is always a dict and calls
  `fields.keys()`.
- Agents often express "write these hints" as a list or string, which is a
  natural shape for the `hints` field.

Optimization:

- In `agent/tools.py::_exec_update_meta` or `tool/update_meta/tool.py`, normalize:
  - `fields: list[str]` -> `{"hints": fields}`
  - `fields: str` -> `{"hints": [fields]}`
- For other invalid types, return a clean tool error instead of exposing Python
  internals.
- Add regression tests in `scripts/tool/test_tools.py`.

Expected benefit:

- Eliminates repeated tool-repair turns in `entity_hints`.
- Makes hints updates more robust without changing graph semantics.

### 2. Improve ref resolution and error recovery for project/file path variants

Observed examples:

- `未找到匹配的实体: debit_card_specializing:file:db`
- `未找到匹配的实体: superhero.sqlite/file:publisher:table`
- `未找到匹配的实体: european_football_2.sqlite:file/Country.csv:file:csv/*:col`
- `匹配到多个实体: formula_1.sqlite:file:db/constructorStandings:table/*:col`
- Historical example: `california_schools::california_schools.sqlite:db/frpm:table/[Academic Year]:col`

Cause:

- Agents mix Pontis path refs with label-style refs, for example `file:db` inside
  the path instead of using `*.sqlite` or `<db>/<table>`.
- Some prompts still show several acceptable-looking ref styles, so agents
  splice them together.
- Multi-match errors do not suggest the next safe step.
- Agents sometimes quote special column names as `[Column Name]`, which is natural
  from SQL Server / general SQL habits. Pontis currently treats `[]` as wildcard
  syntax, so the resolver switches to pattern matching instead of exact column
  matching.

Optimization:

- Tighten writer prompts to prefer only these writable refs:
  - table: `<db.sqlite>/<table>`
  - column: `<db.sqlite>/<table>/<column>`
  - file: `<file name>` or exact file path shown by `find`
- In resolver errors, include a short recovery hint:
  - for no match: "use `find` first and copy one result path"
  - for multi-match: "use `find` to list candidates, then call `meta` on one exact
    path"
- Consider a narrow compatibility shim for common wrong forms:
  - `<db>.sqlite/file:<table>:table` -> `<db>.sqlite/<table>`
  - `<project>:file:db` -> project db file when there is exactly one db file
- Strip outer `[]` from a path segment when it is clearly a quoted column name,
  or return an actionable error explaining that refs should use
  `<db.sqlite>/<table>/Academic Year` while SQL should use `"Academic Year"`.
- Avoid resolving broad wildcard refs for write tools.

Expected benefit:

- Reduces `未找到匹配的实体` and `匹配到多个实体` retries.
- Prevents agents from spending turns inventing path syntax.

### 3. Make `meta`/`find` outputs easier to use safely

Observed examples:

- `meta({"ref": "debit_card_specializing::debit_card_specializing.sqlite:db/customers:table/*:col"})`
- Followed by `Error: 匹配到多个实体`

Cause:

- `meta` is intentionally a single-entity reader, while `find` is the multi-entity
  listing tool.
- Agents naturally try `meta(*:col)` when they want "metadata for all columns in
  this table".
- `find` output is path-oriented and not always a stable writable canonical ref,
  especially for `fk`/`rel`/`overlap`; agents then copy or splice unstable refs.

Optimization:

- Keep `meta` single-entity, but make multi-match errors prescriptive:
  "meta requires one entity; use find first, then meta one exact returned ref."
- Consider `meta_many` or a bounded `meta` multi-result mode for small result sets.
- In `find` output or `meta` Related blocks, show a stable writable ref for
  relation entities in addition to the path display.
- Document that the first `find` column is a traversal/display ref, not always the
  best write ref.

Expected benefit:

- Reduces failed `meta` turns and relation-ref copy mistakes without changing the
  core path traversal model.

### 4. Add preflight validation for `create_entity(edges=...)`

Observed examples:

- `Tool error (create_entity): KeyError: 'ref'`
- `Tool argument parse error ... invalid JSON arguments`

Cause:

- `create_entity` expects edge objects with `a`/`b`, but agents sometimes produce
  malformed edge records or very long JSON with quoting issues.
- Long `detail` plus many `edges` in a single call increases malformed JSON risk.

Optimization:

- In `_exec_create_entity` / `create_entity_command`, validate each edge:
  - reject or skip non-dict edges
  - accept `source`/`target` aliases only if unambiguous, or return a clear error
  - never raise `KeyError`
- Update prompts for long hint/disambig writes:
  - create short entity first
  - then `update_meta` long `detail`
  - then add edges in a separate call if needed
- Add a concise tool error when JSON arguments are malformed, suggesting shorter
  calls.

Expected benefit:

- Avoids losing a whole long generated hint because one edge/detail JSON chunk is
  malformed.

### 5. Improve overlap/fk update behavior

Observed examples:

- `failed to update entity: california_schools::frpm.District Name->schools.District:overlap`
- `failed to update entity: toxicology::atom.molecule_id->bond.molecule_id:overlap`

Cause:

- Relation entities are named with dotted `table.column->table.column` names, but
  write paths sometimes include project prefixes or endpoint path variants that
  do not resolve to the actual relation node.
- Some overlap entities may be generated with a canonical `name` but no stable
  `_ref` that the agent can copy reliably.

Optimization:

- When `update_meta` fails for a relation-like ref containing `->`, retry by
  normalized relation name inside the active project.
- Expose a canonical writable ref for `fk`/`rel`/`overlap` in `find` output or in
  `meta` Related blocks.
- Add tests for relation refs with:
  - project prefix
  - spaces in column names
  - dotted endpoint names

Expected benefit:

- Makes relation metadata updates deterministic and reduces silent optional
  overlap write failures.

### 6. Give `query` SQLite-specific repair hints

Observed examples:

- `OperationalError: near "OFFSET": syntax error`
- `OperationalError: near "order": syntax error`
- `OperationalError: no such column: CreationDate`
- `TimeoutError: SQL query timed out after 30s`
- Agent sometimes calls `query` with a table ref, for example
  `query({"ref": "financial.sqlite/account", "sql": "SELECT ... FROM account"})`.
  The current tool expects the database object ref, not a table object ref.

Cause:

- These are normal exploratory SQL mistakes: reserved words need quoting, actual
  BIRD column names may contain typos such as `CreaionDate`, and broad queries can
  time out.
- LLMs naturally bind "query this table" to the table entity they are inspecting,
  even though SQL execution must open the owning SQLite database file.

Optimization:

- Support table/column refs in `query` by resolving them to the owning database
  file before execution:
  - table ref `<db.sqlite>/<table>` -> execute against `<db.sqlite>`
  - column ref `<db.sqlite>/<table>/<column>` -> execute against `<db.sqlite>`
  - if the owner database is ambiguous, return a clear error with candidate db refs
- Preserve current database-ref behavior as the canonical path.
- Add tests covering query refs for database, table, and column objects.
- Keep the original SQLite error, but append a short targeted hint:
  - `near "order"` -> quote reserved table/column names with double quotes
  - `OFFSET` -> SQLite requires `LIMIT ... OFFSET ...`
  - `no such column` -> run `PRAGMA table_info("<table>")` or inspect `meta`
  - timeout -> add `LIMIT`, narrower predicates, or aggregate first
- Do not treat exploratory SQL errors as extract failures unless the phase cannot
  complete.

Expected benefit:

- Makes `query` match the model's natural "query this table" workflow while still
  executing on the correct database file.
- Fewer follow-up turns spent rediscovering SQLite syntax rules.

### 7. Detect optional local failures separately from framework failures

Observed in current run:

- No `Traceback`
- No `RUN FAILED`
- No remote embedding / OpenAI embedding usage
- Local tool/SQL errors are present but several databases still finish with
  `Semantic embedding done` and `=== <db> done ===`

Optimization:

- Add an extract post-check script/report that separates:
  - framework fatal failures
  - unfinished databases
  - local exploratory SQL errors
  - tool schema/ref errors
  - optional relation/hint write failures
- Emit counts into `extract_summary.json` so future monitoring can avoid treating
  harmless data values such as `429` as rate-limit errors.

Expected benefit:

- Cleaner monitoring signal and less manual log inspection.

## Design notes migrated from `tool-call-error-analysis.md`

The removed analysis doc concluded that not all local failures are "agent
randomness". Several are tool-interface issues that violate common LLM/human
intuition:

| Failure type | Tool-intuition issue? | Notes |
|---|---:|---|
| `update_meta.fields` list/string raises internal exception | Yes | High-frequency hints updates naturally look like string/list inputs. |
| Ref segment written as `[Column Name]` fails | Yes | SQL quoting habit conflicts with Pontis wildcard syntax. |
| `meta(*:col)` multi-match error | Partial | Single-entity design is fine, but the error should guide the next tool call. |
| `find` display refs copied as canonical write refs | Partial | Path model is coherent, but writable refs need to be surfaced clearly. |
| Long `create_entity` JSON parse errors | Partial | Model issue amplified by a heavy create+meta+edges call shape. |
| Exploratory SQL syntax/column errors | No | Expected during exploration; query tool can still help repair faster. |

Guiding principle for the fixes above: preserve Pontis graph semantics, but make
tool APIs tolerant of common natural inputs and make failures actionable. The goal
is to spend extract tokens on database understanding, not on repairing tool-call
syntax.

## Current priority order

1. Normalize `update_meta.fields` list/string inputs.
2. Add clearer ref-resolution recovery hints and canonical relation refs.
3. Make `meta`/`find` safer around multi-match and writable refs.
4. Harden `create_entity` edge validation and long-detail workflow.
5. Add query repair hints for common SQLite errors.
6. Add extract post-check failure classification to `extract_summary.json`.
