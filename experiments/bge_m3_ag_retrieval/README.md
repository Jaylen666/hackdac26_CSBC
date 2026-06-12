# BGE-M3 AG Retrieval Pilot

This experiment tests whether BGE-M3 can retrieve useful assumption-guarantee
candidate pairs from existing HMAC specs without modifying the main CSBC
pipeline.

## Layout

- `build_hmac_atoms.py`: converts HMAC spec JSON files into retrieval atoms.
- `embed_atoms.py`: embeds atoms with `BAAI/bge-m3` and caches vectors.
- `retrieve_ag_pairs.py`: ranks guarantees for every assumption-like query.
- `pair_ag_hybrid.py`: reranks candidates with dense similarity plus field-level signal overlap.
- `pair_ag_optimized.py`: applies optimized AG/uncertain thresholds and signal-relation constraints.
- `plan_llm_batches.py`: estimates LLM calls/tokens for query/topic batching.
- `evaluate_hmac_pairs.py`: computes recall/MRR against a small gold set.
- `analyze_uncertain_overlap.py`: estimates uncertain-point redundancy and pairability.
- `run_hmac_pilot.sh`: runs the full offline pipeline.
- `gold_hmac_ag_pairs.json`: initial gold targets for known HMAC cases.

Generated files are written under `out/` and are intentionally ignored by this
prototype's workflow.

## Setup

Create an isolated virtualenv if desired:

```bash
cd /home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On this server the NVIDIA driver reports CUDA 12.8. If pip selects a CUDA 13
torch wheel, reinstall torch with the CUDA 12.6 PyTorch index:

```bash
pip install --force-reinstall torch==2.7.1 \
  --index-url https://download.pytorch.org/whl/cu126
```

This server already has a local BGE-M3 cache at:

```text
/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/out/hf_cache
```

The main framework now runs semantic AG offline by default and uses that cache.
For new modules, only the module-specific embeddings are recomputed; model
weights are not downloaded again.

If you run the standalone experiment scripts directly, keep the same offline
environment:

```bash
export HF_HOME=/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/out/hf_cache
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
```

Only unset these variables or use the framework's explicit online flags when
you intentionally want to download or refresh model files.

## Run

```bash
cd /home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval
export HF_HOME=/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/out/hf_cache
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
./run_hmac_pilot.sh
```

Or run steps manually:

```bash
python3 build_hmac_atoms.py
python3 embed_atoms.py
python3 retrieve_ag_pairs.py
python3 pair_ag_hybrid.py
python3 pair_ag_optimized.py
python3 evaluate_hmac_pairs.py
python3 analyze_uncertain_overlap.py
```

## Interpretation

The main acceptance target is HMAC bug 009: the `cfg_block` assumption from
`hmac__always_comb__update_secret_key__001` should retrieve the guarantee from
`hmac__always_ff__line_365__001` in top-5, or at least top-10.

This prototype only performs candidate retrieval. A high similarity score is
not a bug verdict; it should feed the existing LLM judgment or a later hybrid
ranker.
