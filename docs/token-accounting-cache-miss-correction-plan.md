# Token Accounting Cache-Miss Correction Plan

## Background

The current benchmark metrics split LLM input tokens into:

- `pre_input_tokens`: stable prompt tokens, usually system prompt plus tool definitions.
- `runtime_input_tokens`: all remaining prompt tokens in each LLM request.
- `runtime_output_tokens`: model completion tokens.

This split is useful for separating static framework prompt from dynamic agent history, but it is not the same as commercial API cache billing.

For multi-round agents, each request resends the full message history. A commercial API can often cache the shared prefix between adjacent requests, not only the original system prompt. Therefore historical user, assistant, and tool-result messages may be billed as cache-hit input after their first appearance.

The current `runtime_input_tokens` metric treats those historical dynamic messages as uncached every time they appear in later rounds. This can significantly overestimate cache-miss input cost.

## Concrete Example

Consider a three-round agent:

```text
round1 input = static + user1
round2 input = static + user1 + assistant1 + tool1
round3 input = static + user1 + assistant1 + tool1 + assistant2 + tool2
```

The current Pontis-style split estimates:

```text
pre_input_total =
  static + static + static

runtime_input_total =
  user1
  + user1 + assistant1 + tool1
  + user1 + assistant1 + tool1 + assistant2 + tool2
```

For cache-aware billing, a better conceptual split is:

```text
cache_miss_input_total =
  first appearance of static
  + first appearance of user1
  + first appearance of assistant1/tool1
  + first appearance of assistant2/tool2

cache_hit_input_total =
  repeated prefix tokens reused in later requests
```

If provider prompt caching works on the complete stable prefix, then `user1`, `assistant1`, and `tool1` should not be charged as uncached again in round3.

## What Is Wrong Today

The currently reported fields are internally consistent but semantically overloaded.

`input_tokens` and `total_tokens` are still valid as total model traffic, because they are accumulated from provider usage for every request.

`runtime_output_tokens` is also valid as completion traffic.

The problematic field is `runtime_input_tokens` when interpreted as uncached input. It is actually:

```text
sum_over_rounds(prompt_tokens_this_round - static_prompt_tokens)
```

This is not:

```text
sum_of_cache_miss_input_tokens
```

Therefore any report that treats `runtime_input_tokens` as cache-miss input overstates the expensive part of input billing.

## Affected Methods

This is likely not a Pontis-only issue. Any baseline that estimates `pre_input_tokens` and `runtime_input_tokens` by subtracting static templates from `prompt_tokens` has the same semantic problem.

Observed local patterns:

- Pontis: `runtime_input_tokens = prompt_tokens - static_prompt_tokens` per LLM round.
- Alpha-SQL: records `pre_input_tokens` from static template estimates and treats the rest as runtime input.
- DeepEye-SQL: records `pre_input_tokens` from prompt content marked as static and treats the rest as runtime input.

So historical comparisons across Pontis, Alpha-SQL, and DeepEye-SQL should not use current `runtime_input_tokens` as uncached input cost.

## Target Metrics

Keep old fields for backward compatibility, but rename their interpretation.

Required stable fields:

```text
llm_rounds
input_tokens
output_tokens
total_tokens
```

Current compatibility fields:

```text
pre_input_tokens
runtime_input_tokens
runtime_output_tokens
```

New cache-aware fields:

```text
cache_hit_input_tokens
cache_miss_input_tokens
cache_unknown_input_tokens
fresh_input_tokens
```

Definitions:

- `cache_hit_input_tokens`: provider-reported cached input tokens when available.
- `cache_miss_input_tokens`: provider-reported uncached input tokens when available.
- `cache_unknown_input_tokens`: input tokens that cannot be confidently split.
- `fresh_input_tokens`: local estimate of newly appended message tokens per round, independent of provider cache reporting.

`fresh_input_tokens` is not exactly provider cache miss, but it is a much better fallback estimate than current `runtime_input_tokens`.

## Preferred Provider-Based Accounting

When the provider returns cache fields, use them directly.

Examples of usage field families to support:

```text
prompt_cache_hit_tokens
prompt_cache_miss_tokens
cached_tokens
input_token_details.cached_tokens
prompt_tokens_details.cached_tokens
```

Normalization rule:

```text
cache_hit_input_tokens = provider cached prompt/input tokens
cache_miss_input_tokens = input_tokens - cache_hit_input_tokens
cache_unknown_input_tokens = 0
```

If the provider separately reports hit and miss:

```text
cache_hit_input_tokens = reported_hit
cache_miss_input_tokens = reported_miss
cache_unknown_input_tokens = input_tokens - hit - miss, if positive
```

Provider fields should override local estimates. Do not promote locally estimated
static prompt tokens into cache-hit tokens when the provider reports fewer hits.
Commercial APIs bill from the provider-side cache decision, not from the client
side's idea of which prompt segments are stable.

Provider-specific notes:

- OpenAI-compatible APIs such as OpenAI and DeepSeek usually report total prompt
  input plus cache details, for example `prompt_tokens_details.cached_tokens` or
  DeepSeek's `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`.
- Anthropic reports `cache_read_input_tokens`, `cache_creation_input_tokens`, and
  `input_tokens` separately. The total input for billing and rate-limit analysis
  is their sum.
- Gemini explicit caching reports cached token counts through cache and
  `GenerateContent` usage metadata; cached tokens remain part of the prompt and
  non-cached input/output are charged separately.

## Fallback Local Estimation

If provider cache fields are unavailable, estimate repeated-prefix caching from
the serialized messages sent to the model. The first request in a local session
is treated as cache miss unless provider usage says otherwise.

For each LLM round:

1. Serialize the exact request prefix in a stable form.
2. Compare current request text with the previous request text.
3. Estimate token length of the longest common prefix.
4. Treat that prefix as `estimated_cache_hit_input_tokens`.
5. Treat the suffix as `estimated_cache_miss_input_tokens`.

Pseudo logic:

```python
current_prompt = serialize_messages_and_tools(messages, tools)
prefix_chars = longest_common_prefix(previous_prompt, current_prompt)

estimated_hit = estimate_tokens(current_prompt[:prefix_chars])
estimated_miss = input_tokens - estimated_hit
```

This approximates commercial API behavior better than only caching `system + tools`.

Limitations:

- Real providers may cache only above certain prefix lengths.
- Cache lifetime and organization-level cache sharing differ by provider.
- Tokenization of serialized chat messages is provider-specific.
- Some providers may not cache tool-call JSON exactly as serialized locally.

Because of these limitations, fallback values should be explicitly marked as estimated.

## Naming Change

The old `runtime_input_tokens` should not be displayed as "uncached input".

Recommended report names:

```text
Pre-input Tokens/Q
  -> Static Prefix Input Tokens/Q

Runtime Input Tokens/Q
  -> Dynamic History Input Tokens/Q

Cache-Miss Input Tokens/Q
  -> new provider-aware or fallback-estimated metric

Cache-Hit Input Tokens/Q
  -> new provider-aware or fallback-estimated metric
```

This preserves current data while preventing incorrect billing interpretation.

## Implementation Plan

### 1. Add a Shared Cache Accounting Utility

Create a shared utility, for example:

```text
Pontis/agent/cache_token_accounting.py
```

Responsibilities:

- Extract provider cache fields from response usage objects.
- Serialize request messages and tool definitions for fallback estimation.
- Track previous request prompt per agent/session.
- Return a normalized dict:

```python
{
    "input_tokens": int,
    "output_tokens": int,
    "total_tokens": int,
    "cache_hit_input_tokens": int,
    "cache_miss_input_tokens": int,
    "cache_unknown_input_tokens": int,
    "fresh_input_tokens": int,
    "cache_accounting_source": "provider" | "estimated_prefix" | "unknown",
}
```

### 2. Update Pontis Runtime Metrics

In `Pontis/agent/agent.py`, keep current fields but add the new fields to `llm_metrics()`.

Current fields remain:

```text
pre_input_tokens
runtime_input_tokens
runtime_output_tokens
```

New fields:

```text
cache_hit_input_tokens
cache_miss_input_tokens
cache_unknown_input_tokens
fresh_input_tokens
cache_accounting_source
```

The benchmark summary should report both old compatibility fields and new cache-aware fields.

### 3. Update Alpha-SQL

Alpha-SQL currently records prompt/completion tokens through its LLM wrapper and a `CostRecorder`.

Needed changes:

- Add provider cache-field extraction in the OpenAI-compatible response handler.
- Add fallback prefix estimation per task or per MCTS solver session.
- Extend result records with the new fields.
- Stop using `runtime_input_tokens` as cost-equivalent cache miss.

Important detail: Alpha-SQL uses many independent MCTS calls. Cache estimation should be scoped to a single task/session, not globally across unrelated questions.

### 4. Update DeepEye-SQL

DeepEye-SQL has multiple stages:

- value retrieval keyword extraction
- schema linking
- SQL generation
- SQL revision
- SQL selection

Needed changes:

- Add cache-field extraction in `app/llm/llm.py`.
- Add fallback prefix estimation within each stage item.
- Aggregate new fields into each `DataItem.total_llm_cost`.
- Preserve stage-level breakdowns so cache behavior can be compared across pipeline stages.

Important detail: many DeepEye-SQL calls are parallel independent samples. Cache estimation must not share previous request state across unrelated samples unless the provider cache is directly reporting actual hit/miss.

### 5. Update Benchmark Outputs

Every result row should include:

```json
{
  "llm_rounds": 10,
  "input_tokens": 123456,
  "output_tokens": 2345,
  "total_tokens": 125801,

  "pre_input_tokens": 90000,
  "runtime_input_tokens": 33456,

  "cache_hit_input_tokens": 100000,
  "cache_miss_input_tokens": 23456,
  "cache_unknown_input_tokens": 0,
  "fresh_input_tokens": 22000,
  "cache_accounting_source": "provider"
}
```

For mixed sources across rounds, use:

```text
cache_accounting_source = "mixed"
```

and optionally record per-round details in verbose logs.

### 6. Recompute Historical Summaries

Historical raw logs do not contain provider cache hit/miss fields, so they cannot be perfectly corrected.

Two options:

1. Keep historical summaries as legacy metrics and label them clearly.
2. Recompute estimated cache metrics from detailed logs if exact message histories can be reconstructed.

Pontis detailed logs currently contain tool calls/results but not exact serialized API messages or provider cache fields. Recomputed fallback metrics will be approximate.

## Cost Formula After Fix

Use this for commercial API cost estimates:

```text
input_cost =
  cache_miss_input_tokens * uncached_input_price
  + cache_hit_input_tokens * cached_input_price
  + cache_unknown_input_tokens * uncached_input_price

output_cost =
  output_tokens * output_price

total_cost = input_cost + output_cost
```

For conservative estimates, price `cache_unknown_input_tokens` as uncached.

For old runs without corrected fields, do not estimate cost from `runtime_input_tokens` unless explicitly labeling it as a conservative upper bound.

## Migration Policy

Do not delete old metrics immediately. For at least one iteration, reports should show:

```text
Static Prefix Input Tokens/Q
Dynamic History Input Tokens/Q
Cache-Hit Input Tokens/Q
Cache-Miss Input Tokens/Q
Output Tokens/Q
Rounds/Q
```

This makes it clear which fields are traffic diagnostics and which fields are billing diagnostics.

## Validation Checklist

For a synthetic three-round conversation:

```text
round1 = static + user1
round2 = static + user1 + assistant1 + tool1
round3 = static + user1 + assistant1 + tool1 + assistant2 + tool2
```

Expected behavior:

- `input_tokens` equals the sum of all three request prompt tokens.
- `runtime_input_tokens` may still include repeated dynamic history for compatibility.
- `fresh_input_tokens` roughly equals first appearances of `static`, `user1`, `assistant1/tool1`, and `assistant2/tool2`.
- `cache_miss_input_tokens` equals provider miss tokens if reported.
- fallback-estimated `cache_miss_input_tokens` should be close to `fresh_input_tokens`, not to old `runtime_input_tokens`.

## Bottom Line

The old metrics are not useless, but their names led to a wrong billing interpretation. They measure repeated dynamic context traffic, not commercial API cache-miss input.

The fix should be applied uniformly across Pontis and all baselines. Otherwise cost comparisons between Pontis, Alpha-SQL, DeepEye-SQL, and future agents will be systematically distorted.
