# Optimized AG Pairing Report

## Method

The optimized candidate pairing uses BGE-M3 as semantic retrieval plus a
signal-relation constraint:

- Assumption queries require a non-zero signal relation.
- Uncertain queries allow either a signal relation or a dense-only fallback.
- Same-spec guarantees are excluded.
- Score is `0.8 * dense_similarity + 0.2 * signal_relation_score`.
- Default thresholds: assumption `>= 0.66`, uncertain-with-signal `>= 0.66`,
  uncertain dense fallback `dense >= 0.82`.
- Per-query caps: assumption top-5, uncertain top-3.

Signal relation includes exact field overlap, normalized field overlap
(`mr_key_words_sel` vs `key_words_sel_o`), exact text signal overlap, and
normalized text signal overlap.

## HMAC Results

With default threshold `0.66`:

- Total selected AG candidates: 342.
- Assumption-origin pairs: 209.
- Uncertain-origin pairs: 133.
- Pair types: 209 normal, 88 uncertain-with-signal, 45 uncertain dense fallback.

Known bug neighborhoods:

- HMAC-009 `cfg_block`: target lifecycle guarantee rank 1, score 0.6765.
- HMAC err_code / invalid_config neighborhood: target err_code guarantee rank 3,
  score 0.6838.
- HMAC `invalid_config_atstart` software-error chain: target start/error
  guarantee rank 1 through dense fallback, score 0.6809.

The `invalid_config_atstart` case confirms why uncertain dense fallback is
needed: the useful pair has no direct signal relation in the current fields.

## AES Results

With default threshold `0.66`:

- Total selected AG candidates: 65.
- Assumption-origin pairs: 40.
- Uncertain-origin pairs: 25.
- Pair types: 40 normal, 4 uncertain-with-signal, 21 uncertain dense fallback.

Known bug neighborhoods:

- AES N-001 `key_words_sel`: target OR-combine guarantee rank 1, score 0.6712,
  using normalized field overlap between `key_words_sel` and `key_words_sel_o`.
- AES key_full selector neighborhood: target combine-sparse guarantee rank 3,
  score 0.6822.
- AES N-002 `iv_sel` cannot be evaluated from the current AES spec output
  because existing specs do not include `iv_sel`, `iv_we`, `IV_CTR`, or CTR IV
  update chunks.

## Architecture Findings

- A single assumption threshold of 0.68 is too high for AES; it filters the
  real N-001 key_words pair at score 0.6712. Threshold 0.66 keeps the pair with
  modest extra cost.
- Signal normalization is necessary. Exact overlap misses common RTL naming
  patterns such as `mr_key_words_sel`, `key_words_sel_o`, and `key_words_sel`.
- Signal relation should remain a hard constraint for assumptions, but not for
  uncertain points. Some useful uncertain chains are semantic/downstream rather
  than same-signal pairs.
- Current AES N-002 miss is a Phase 1 spec coverage problem, not a pairing
  score problem.
