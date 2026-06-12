# BGE-M3 HMAC AG Retrieval Pilot Report

## Environment

- Server GPU: NVIDIA A800 80GB PCIe.
- Driver: NVIDIA 570.133.20, CUDA capability reported by `nvidia-smi`: 12.8.
- Working torch: `torch 2.7.1+cu126`, `torch.cuda.is_available() == True`.
- Model: `BAAI/bge-m3`, cached under `out/hf_cache`.

Important deployment note: the default `torch 2.12.0+cu130` wheel was not usable
on this server because the driver only supports CUDA 12.8. Use the PyTorch
`cu126` wheel index for this experiment:

```bash
.venv/bin/pip install --force-reinstall torch==2.7.1 \
  --index-url https://download.pytorch.org/whl/cu126
```

## Data

- Specs read from `/home/smy/rtl_bug_agent/output/specs`.
- HMAC atoms generated: 396 total.
- Atom breakdown: 84 assumptions, 149 guarantees, 163 uncertain points.
- Retrieval mode: BGE-M3 dense cosine similarity, top-10, same-spec guarantees
  excluded by default.

## Gold Evaluation

Gold case: HMAC bug 009 `cfg_block` key-update protocol.

- Query: `hmac__always_comb__update_secret_key__001::assumption::0`
- Target spec: `hmac__always_ff__line_365__001`
- Hit rank: 4
- Metrics: `recall@5 = 1.0`, `recall@10 = 1.0`, `MRR = 0.25`

Top candidates show a useful pattern: dense retrieval first finds nearby
`cfg_block` consumers such as `update_seckey_inprocess` and `cfg_reg`, then the
actual `cfg_block` lifecycle guarantee appears at ranks 4 and 5. This is good
enough for candidate generation, but not enough to replace the LLM verifier.

## Uncertain-Point Analysis

- Uncertain points: 163
- High-overlap with existing assumptions (`max_sim >= 0.85`): 10
- Pairable with some guarantee (`max_sim >= 0.80`): 89
- Mean max similarity to assumptions: 0.797
- Mean max similarity to guarantees: 0.801

This suggests uncertain points are not mostly duplicates of assumptions. They
contain many semantically pairable claims, so dropping them loses signal. The
`invalid_config_atstart` uncertain point is a concrete example: it retrieves
guarantees around `invalid_config` / `hash_start`, matching the known err-code
copy-paste bug's neighborhood.

## Recommendation

Use BGE-M3 as a retrieval layer, not as a verdict layer.

Recommended phase-2 integration strategy:

- Treat assumptions and uncertain points as queries.
- Exclude same-spec guarantees by default.
- Keep top-5 or top-10 guarantee candidates per query.
- Deduplicate uncertain points that are highly similar to existing assumptions
  before sending to phase 3.
- Add a lightweight hybrid reranker next: dense similarity plus signal overlap.

For HMAC, dense-only already recovers the key bug 009 AG pair in top-5. The
remaining cost-control problem is not retrieval recall; it is filtering or
reranking the large number of uncertain-point candidates before LLM judgment.
