# Guarded Batch LLM Experiment Report

## Implemented Guardrails

The batch prompt now treats each item as an independent AG checking task:

- The model must output one verdict per query.
- The model may use cross-item context only when another query clearly shares
  signal, guarantee, source, or control/data path.
- The output schema includes `cross_item_context_used` and
  `cross_item_context_ids`.

Batch construction also has hard limits:

- `max_queries_per_batch = 5`
- `max_prompt_tokens = 5500`
- `max_dense_fallback_uncertain = 1`
- `min_shared_roots = 1`
- `max_signal_roots = 4`

The last two limits prevent broad families such as AES selector-control from
mixing too many unrelated selector roots in one prompt.

## HMAC Guarded Result

Sample: 4 queries, 11 AG candidates.

- One-query: 4 calls, 3,523 input tokens, 8,736 total tokens, 96.0s.
- Guarded family-pack: 3 calls, 3,104 input tokens, 6,913 total tokens, 74.4s.
- Savings: 25% fewer calls, 11.9% fewer input tokens, 20.9% fewer total tokens,
  22.5% less wall time.

Quality:

- The earlier over-aggressive `CONTRADICTION` verdict disappeared.
- Verdicts stayed conservative: `GAP` / `UNCERTAIN`.
- `cross_item_context_used` was not used in parsed results, which means the
  prompt successfully prevented forced linkage.

## AES Guarded Result

Sample: 4 queries, 9 AG candidates.

- One-query: 4 calls, 3,600 input tokens, 7,707 total tokens.
- Guarded family-pack: 4 calls, 3,600 input tokens, 8,703 total tokens.
- No token saving for this sample because guardrails split all four query
  batches into single-query calls.

Quality:

- JSON output was stable.
- All verdicts remained `GAP`.
- `cross_item_context_used=false` for all parsed results.
- The broad AES selector-control batch that previously caused unstable JSON and
  over-aggressive `CONTRADICTION` was split by `max_signal_roots`.

## Interpretation

The guarded design behaves correctly:

- When queries are genuinely close, as in HMAC cfg lifecycle, batching saves
  tokens without harming quality.
- When a family is too broad, as in AES selector-control, guardrails force the
  system back to one-query-style calls instead of risking degraded judgment.

This is the right failure mode. It means batching is opportunistic: it saves
tokens when safe, and gives up savings when grouping would be risky.

## Recommendation

Use guarded batching as the default LLM dispatch layer:

- Keep per-query independent verdicts.
- Keep `cross_item_context_used` in the output schema for auditing.
- Keep signal-root and dense-fallback limits.
- Add a retry path for malformed JSON: rerun the same batch with a shorter
  JSON-only repair prompt or split the batch into individual queries.

For full-scale runs, the expected savings should be closer to HMAC than AES
when query clusters are coherent; for selector-heavy modules, savings will be
smaller but quality will be protected.
