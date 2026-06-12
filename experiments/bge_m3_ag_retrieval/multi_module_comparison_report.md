# Multi-Module Pairing Comparison Report

## Scope

Compared legacy signal-based AG pairing against optimized semantic/signal
pairing on:

- HMAC
- AES
- KEYMGR
- RV_DM
- KMAC
- UART

Legacy baseline uses behavioral AG edges plus uncertain points as individual
LLM work items, matching the assumption that uncertain points previously needed
their own LLM calls.

## Summary Table

| Module | Legacy AG Edges | Uncertain | Legacy+Uncertain Work | Optimized Pairs | Optimized Queries | Topic-Pack Calls | Fixed-Pack Calls |
|---|---:|---:|---:|---:|---:|---:|---:|
| hmac | 265 | 163 | 428 | 342 | 135 | 62 | 27 |
| aes | 25 | 103 | 128 | 65 | 37 | 17 | 8 |
| keymgr | 59 | 128 | 187 | 101 | 51 | 28 | 11 |
| rv_dm | 2 | 23 | 25 | 6 | 6 | 4 | 2 |
| kmac | 923 | 371 | 1294 | 679 | 282 | 124 | 57 |
| uart | 10 | 32 | 42 | 8 | 6 | 5 | 2 |

## Main Findings

- Optimized selected pair count is lower than legacy+uncertain work items for
  every tested module.
- The reduction is strongest on small sparse modules such as UART and RV_DM.
- KMAC remains large in absolute terms, but optimized pairing still reduces
  work items from 1294 to 679.
- Topic-pack batching gives large call reductions even with quality-oriented
  grouping: 84-90% fewer calls versus legacy+uncertain one-item calls.
- Fixed-pack is the cost lower bound, but it may mix unrelated topics and should
  not be treated as quality-safe without guardrails.

## KMAC Update

The table above reflects the earlier experiment-local `kmac*.json` prefix-only
scope. The current main framework semantic mode builds atoms from the entire
`output/specs_kmac/` directory, which also includes related blocks such as
`sha3*`, `keccak*`, and other non-`kmac_` chunks. That scope is larger and is
the one used by `scripts/run_phase2_e2e.py --ag-pairing-mode semantic`.

Current main-framework KMAC numbers:

| Scope | Legacy Pair Items | Legacy AG Edges | Legacy Signal Calls | Semantic Pairs | Semantic Query Units | Guarded Batch Calls |
|---|---:|---:|---:|---:|---:|---:|
| `output/specs_kmac/` full directory | 396 | 923 | 220 | 1015 | 409 | 310 |

Interpretation:

- `396` is the current Pass-0 `behavioral` pair-item count printed by the main
  framework.
- `923` is the corresponding expanded AG-edge count, which matches the older
  report's legacy KMAC edge count.
- The semantic side is larger than the old prefix-only experiment because the
  current scope includes all KMAC-adjacent specs in the directory, not just
  files whose chunk id starts with `kmac`.
- For the current main-framework KMAC run, semantic Channel B dispatch is
  higher than legacy signal-grouped Channel B dispatch (`310` guarded calls vs
  `220` legacy signal calls), but guarded batching still reduces semantic
  single-query dispatch from `409` to `310`.

## Ratios

Optimized pairs divided by legacy+uncertain work items:

- HMAC: 0.80
- AES: 0.51
- KEYMGR: 0.54
- RV_DM: 0.24
- KMAC: 0.52
- UART: 0.19

Topic-pack call reduction versus legacy+uncertain calls:

- HMAC: 85.5%
- AES: 86.7%
- KEYMGR: 85.0%
- RV_DM: 84.0%
- KMAC: 90.4%
- UART: 88.1%

## Plots

- `out/plots/ag_work_items.png`
- `out/plots/llm_calls.png`
- `out/plots/prompt_tokens.png`
- `out/plots/optimized_pair_composition.png`
- `out/plots/optimized_vs_legacy_ratio.png`

## Interpretation

The optimized method does not simply increase candidate volume by adding
uncertain points. After thresholding, signal-relation constraints, and top-k
limits, it reduces total work items compared with legacy AG plus independent
uncertain processing in all tested modules.

The biggest practical gain is in LLM dispatch: grouping optimized non-empty
queries into topic batches reduces the number of LLM calls by roughly 84-90%.
This should be combined with guarded batching, not naive fixed batching, to
avoid quality loss from unrelated query mixing.
