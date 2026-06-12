# LLM Batch Experiment Report

## Setup

- Provider: `GUOCHUANG_DEEPSEEK`
- Scope: sampled known bug neighborhoods, not full HMAC/AES run.
- Compared strategies on the same selected query/pair sets:
  - `one_query`: one query per LLM call.
  - `family_pack`: group related queries from the same semantic family.

## Measured Results

HMAC sample:

- Queries: 4
- Candidate pairs: 11
- One-query: 4 calls, 2,759 input tokens, 7,923 total tokens, 112.9s.
- Family-pack: 2 calls, 2,303 input tokens, 5,974 total tokens, 70.4s.
- Savings: 50% fewer calls, 16.5% fewer input tokens, 24.6% fewer total tokens,
  37.6% less wall time.

AES sample:

- Queries: 4
- Candidate pairs: 9
- One-query: 4 calls, 2,836 input tokens, 6,726 total tokens, 82.8s.
- Family-pack: 2 calls, 2,380 input tokens, 5,761 total tokens, 59.2s.
- Savings: 50% fewer calls, 16.1% fewer input tokens, 14.3% fewer total tokens,
  28.5% less wall time.

## Quality Audit

HMAC family-pack preserved or improved debug quality:

- `cfg_block` key-update lifecycle was still reported as a GAP.
- The packed result gave a clearer combined path:
  `cfg_block <= hash_start_or_continue, reg_hash_done, reg_hash_stop`.
- `invalid_config` / `invalid_config_atstart` error-reporting chains were also
  preserved as GAP findings.

AES result is mixed:

- Small integrity-error family pack preserved both GAP judgments.
- Selector-control family pack was harder for the model: the model produced
  useful reasoning but ignored strict JSON output. This is repairable, but it
  indicates that large selector-control batches are cognitively denser than
  HMAC cfg/error batches.

## Architecture Recommendation

The direction is feasible: family batching saves tokens and does not inherently
hurt debug quality when the family is semantically tight.

Use these batching defaults:

- HMAC cfg/error/key families: family-pack up to 5 queries.
- AES integrity-error families: family-pack up to 4-5 queries.
- AES selector-control families: split more aggressively, preferably by exact
  normalized selector root (`key_words`, `key_full`, `state`, `add_rk`) and cap
  at 2-3 queries.
- Keep JSON-repair/fallback extraction because DeepSeek sometimes emits
  reasoning text despite JSON-only prompts.

Do not use naive fixed pack-5 across unrelated topics. It saves calls but mixes
unrelated protocols and is more likely to reduce debug precision.
