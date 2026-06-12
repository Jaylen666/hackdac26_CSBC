# Ablation README

This file summarizes the main ablation artifacts under `experiments/` and explains how to read the latest results.

## 0. Experiment Setup

This repository currently mixes several ablation styles. The main configurations used in the latest comparisons are:

- `CSBC`
  - Spec generation uses the old prompt:
    - `config/prompts/chunk_spec.md`
  - Phase 2 semantic pairing uses:
    - `0.8 * embedding score + 0.2 * signal score`
  - Phase 3 verification uses Codex with the prompt:
    - `config/prompts/phase3/verify.md`

- `Codex_agent`
  - Earlier whole-RTL direct agent baseline.
  - Prompt location:
    - `experiments/signal_only_ablation/whole_rtl_codex_prompt_reconstructed.md`
  - Note:
    - This file is a reconstructed protocol prompt, because the exact original prompt was not separately persisted during the earlier run.

- `gpt 5.4 with chunks`
  - Blind chunk-package ablation prompt:
    - `experiments/signal_only_ablation/blind_chunk_codex_prompt.md`

## 1. Directory Layout

There are two main ablation directories:

- `experiments/signal_only_ablation/`
  - Raw-code / chunk-based bug finding ablations.
- `experiments/bge_m3_ag_retrieval/`
  - Semantic AG retrieval / batching / uncertain-point ablations.

## 2. Signal-Only / Chunk Ablations

Location:

- `experiments/signal_only_ablation/`

Most important files:

- `experiments/signal_only_ablation/bug_comparison_table.xlsx`
  - Main summary table for bug-level comparison.
  - This is the easiest file to open when reviewing final hit/miss status.
- `experiments/signal_only_ablation/bug_comparison_table.csv`
  - Same content as the xlsx version.
- `experiments/signal_only_ablation/out/blind_chunk_codex_results.json`
  - Latest blind chunk ablation result.
  - This is the package-level output from Codex when only bug-related chunk packages were provided.
  - The file also contains:
    - `prompt_file`
    - `prompt_text`
    - the exact prompt used in this run for reproducibility.
- `experiments/signal_only_ablation/blind_chunk_codex_prompt.md`
  - The prompt template used for the blind chunk Codex ablation.
- `experiments/signal_only_ablation/out/bug_chunks_blind.json`
  - Input packages for the blind chunk ablation.
  - Packages are grouped by module and package id, e.g. `PKG-001`, `PKG-012`, etc.
- `experiments/signal_only_ablation/out/llm_raw_code_results.json`
  - Raw-code ablation results from DeepSeek.
- `experiments/signal_only_ablation/out/llm_raw_code_results_gpt54.json`
  - Raw-code ablation results from GPT-5.4.
- `experiments/signal_only_ablation/out/llm_raw_code_vs_pipeline.png`
  - Latest rendered overview chart for the ablation summary table.
- `experiments/signal_only_ablation/generate_chart.py`
  - Script used to generate the latest comparison overview chart.
- `experiments/signal_only_ablation/whole_rtl_codex_prompt_reconstructed.md`
  - Reconstructed prompt for the earlier whole-RTL Codex ablation.
  - This is a protocol reconstruction, not a byte-for-byte historical prompt log.

Supporting input files:

- `experiments/signal_only_ablation/out/pairs_signal_only_hmac.json`
- `experiments/signal_only_ablation/out/pairs_signal_only_aes.json`
- `experiments/signal_only_ablation/out/pairs_signal_only_keymgr.json`
- `experiments/signal_only_ablation/out/pairs_signal_only_uart.json`
  - Signal-based chunk/group inputs used in earlier raw-code ablations.

- `experiments/signal_only_ablation/out/bug_chunks_packages.json`
  - Earlier package-style grouping file.
- `experiments/signal_only_ablation/out/extra_bug_chunks*.json`
  - Extra bug-specific chunk collections used during later analysis.

## 3. How To Read `bug_comparison_table`

Main file:

- `experiments/signal_only_ablation/bug_comparison_table.xlsx`

Each row is one bug case.

Important columns:

- `IP`, `ID`, `Location`, `Description`
  - Basic bug identity.
- `In_AGU`
  - Whether the old spec-generated A/G/U content already touched this bug.
  - Typical values are `strong`, `weak`, or `no spec`.
- `In_Spec`
  - Whether official module spec gives a direct basis for calling the bug a violation.
  - `strong` means direct and strict support.
  - `weak` means only related or indirect support.
  - `no` means no useful official-spec basis was found.
- `In_ref`
  - Whether the same issue also exists in the reference repository.
- `CSBC`
  - Whether the full CSBC pipeline found the bug.
  - Typical values currently include `yes`, `no`, or `unknown` depending on the earlier bookkeeping.
- `Codex_agent`
  - Result from the earlier whole-RTL Codex agent ablation.
  - `exact` means precise hit.
  - `miss` means not found.
  - `extra` means found via additional Codex review behavior beyond the exact benchmark phrasing.
- `codex with chunk`
  - Result from the latest blind chunk ablation.
  - `yes` means exact hit.
  - `miss` means everything else.

For the latest update:

- `codex with chunk = yes`
  - The chunk-only Codex run precisely found the bug.
- `codex with chunk = miss`
  - The chunk-only Codex run did not precisely hit the bug, even if it produced a nearby suspicion.

## 4. How To Read `blind_chunk_codex_results.json`

Main file:

- `experiments/signal_only_ablation/out/blind_chunk_codex_results.json`

This is the most complete record of the latest blind chunk experiment.

Top-level fields:

- `experiment`
  - Experiment name.
- `prompt_file`
  - Path to the prompt file.
- `prompt_text`
  - Full prompt content used for the run.
- `input_file`
  - Source package file.
- `modules`
  - Modules included in the run.
- `results`
  - Package-level judgments.

Each `results[]` entry contains:

- `package_id`
  - Package id such as `PKG-001`.
- `ip`
  - Module name.
- `summary`
  - What the package is about.
- `spec_consulted`, `spec_files`
  - Whether the agent used official spec and which files it read.
- `rtl_evidence`
  - Local RTL evidence used by the agent.
- `spec_evidence`
  - Official-spec evidence used by the agent.
- `judgment`
  - One of:
    - `bug`
    - `suspicious`
    - `no_bug`
- `confidence`
  - Agent confidence.
- `rationale`
  - Short explanation.
- `bug_hypothesis`
  - Failure-mode explanation when the agent judged `bug` or `suspicious`.

Interpretation:

- `bug`
  - Strong local hit under chunk-only input.
- `suspicious`
  - Partial signal only; not counted as an exact hit in `bug_comparison_table`.
- `no_bug`
  - No confirmed issue from the chunk-only run.

## 5. Semantic AG Retrieval Ablations

Location:

- `experiments/bge_m3_ag_retrieval/`

This directory contains the semantic retrieval and batching experiments that compare the new AG pairing method against legacy signal-based pairing.

Most important files:

- `experiments/bge_m3_ag_retrieval/README.md`
  - Setup and execution notes for the semantic AG retrieval experiment.
- `experiments/bge_m3_ag_retrieval/report.md`
  - General experiment summary.
- `experiments/bge_m3_ag_retrieval/optimized_pairing_report.md`
  - Main report for the optimized semantic pairing configuration.
- `experiments/bge_m3_ag_retrieval/multi_module_comparison_report.md`
  - Cross-module comparison of semantic pairing vs previous methods.
- `experiments/bge_m3_ag_retrieval/guarded_batch_experiment_report.md`
  - Report for guarded batch clustering / grouping.
- `experiments/bge_m3_ag_retrieval/llm_batch_experiment_report.md`
  - LLM-call batching experiment report.
- `experiments/bge_m3_ag_retrieval/bug_ag_coverage_quality_report.md`
  - Bug-to-AG coverage quality analysis.

Useful scripts:

- `retrieve_ag_pairs.py`
  - Retrieve semantic AG candidates.
- `pair_ag_optimized.py`
  - Optimized AG pairing logic.
- `plan_llm_batches.py`
  - Group AG pairs into batch inputs for LLM analysis.
- `run_multi_module_comparison.py`
  - Cross-module evaluation entry point.
- `plot_multi_module_comparison.py`
  - Plotting script for the comparison figures.
- `build_bug_attribution_matrix.py`
  - Attribution analysis script.

## 6. Recommended Reading Order

If someone only wants the latest final outcomes:

1. Open `experiments/signal_only_ablation/bug_comparison_table.xlsx`.
2. Check `codex with chunk`, `Codex_agent`, and `CSBC` side by side.
3. If a row is interesting, inspect the corresponding package-level record in:
   - `experiments/signal_only_ablation/out/blind_chunk_codex_results.json`
4. If a visual overview is preferred, open:
   - `experiments/signal_only_ablation/out/llm_raw_code_vs_pipeline.png`

If someone wants the latest chunk-only ablation specifically:

1. Read `experiments/signal_only_ablation/blind_chunk_codex_prompt.md`
2. Read `experiments/signal_only_ablation/out/bug_chunks_blind.json`
3. Read `experiments/signal_only_ablation/out/blind_chunk_codex_results.json`
4. Cross-check exact hit/miss in `bug_comparison_table.xlsx`

If someone wants the earlier whole-RTL Codex baseline specifically:

1. Read `experiments/signal_only_ablation/whole_rtl_codex_prompt_reconstructed.md`
2. Check the `Codex_agent` column in `bug_comparison_table.xlsx`
3. Compare it against `codex with chunk` and `CSBC`

If someone wants semantic AG retrieval results:

1. Read `experiments/bge_m3_ag_retrieval/README.md`
2. Read `optimized_pairing_report.md`
3. Read `multi_module_comparison_report.md`
4. Read `guarded_batch_experiment_report.md`

## 7. Notes

- `bug_comparison_table.xlsx` is the current best single-file summary.
- `blind_chunk_codex_results.json` is the current best detailed artifact for the latest chunk-only Codex ablation.
- The xlsx and csv versions of the bug comparison table were kept synchronized in the latest update.
