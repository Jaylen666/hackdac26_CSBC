#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export HF_HOME="${HF_HOME:-$(pwd)/out/hf_cache}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"

python3 build_hmac_atoms.py
python3 embed_atoms.py "$@"
python3 retrieve_ag_pairs.py
python3 pair_ag_hybrid.py
python3 pair_ag_optimized.py
python3 evaluate_hmac_pairs.py
python3 analyze_uncertain_overlap.py
