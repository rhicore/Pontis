# BIRD Runtime Runbook

Last verified: 2026-07-14.

Run commands from the `Pontis/` directory with the project environment:

```bash
uv run python -m <module> ...
```

The scripts add the repository `tools/` directory to `sys.path` where needed.
Do not use the removed `sql_writer_cli` or `sql_writer_agent` entrypoints.

## Neo4j

BIRD projects use configured local Neo4j instances. Start or inspect them with:

```bash
uv run python -m scripts.neo4j_instances status california_schools
uv run python -m scripts.neo4j_instances start california_schools
```

Load `.neo4j/neo4j.env` or export the configured password before running tools
that connect to Neo4j.

## Extract

Inspect extraction options without changing a graph:

```bash
uv run python -m scripts.BIRD.extract --help
```

Extract one dev database:

```bash
uv run python -m scripts.BIRD.extract california_schools --workers 1
```

Run only deterministic modules:

```bash
uv run python -m scripts.BIRD.extract california_schools --static-only
```

`--ai-only` runs the explorer agents followed by embedding. `--agent-only`
runs the explorers without embedding. These modes are mutually exclusive.

Run selected modules on the existing graph:

```bash
uv run python -m scripts.BIRD.extract california_schools \
  --modules bird_official_description_extract,semantic_embedding
```

`--force` clears the selected project's graph and preprocess log directory
before extraction. Do not use it for a smoke test. A normal full extraction
ends with a readiness check for schema entities, descriptions, official
metadata, README and current embeddings; benchmark startup repeats this check
and fails before deleting runtime logs when the graph is incomplete.

Entity `hints` are meta-only context. They are shown when an entity is read,
but are deliberately excluded from both semantic embedding text and BM25
fallback text.

## Benchmark

Run one question:

```bash
uv run python -m scripts.BIRD.run_bird_benchmark \
  --db california_schools \
  --qids 53 \
  --workers 24 \
  --db-workers 1
```

The runner currently requires `--workers` greater than 20. Use `--run-id` for a
stable output name and `--output-dir` only when the default workspace location
is unsuitable.

Inspect all benchmark options safely with:

```bash
uv run python -m scripts.BIRD.run_bird_benchmark --help
```

## Slurm

Local Neo4j ports require benchmark and extraction jobs to run on the same node
as the persistent Neo4j job. Use the wrapper:

```bash
uv run python -m scripts.BIRD.bird_slurm extract submit -- --workers 6
uv run python -m scripts.BIRD.bird_slurm benchmark submit -- \
  --workers 24 --db-workers 6
```

```bash
uv run python -m scripts.BIRD.bird_slurm --help
```

## Verification

Compile the active Python packages:

```bash
uv run python -m compileall -q agent explorer extractor scripts storage tool
```

Run evaluator and runtime tests:

```bash
uv run python -m scripts.result_evaluation.test_core
uv run python -m scripts.BIRD.test_result_match
uv run python -m scripts.BIRD.test_bird_runtime_policy
```

The BIRD business-correct contract is documented in
[Business Correct Evaluator](business-correct-evaluator.md).
