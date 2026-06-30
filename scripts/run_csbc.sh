#!/usr/bin/env bash
# CSBC run for a single IP (no formal, no phase3, no fusion/clustering)
# Usage: bash scripts/run_csbc.sh <ip_name>
#        e.g. bash scripts/run_csbc.sh dma
set -euo pipefail

IP="${1:?Usage: $0 <ip_name> (e.g. dma, kmac, rv_dm, tlul, soc_dbg_ctrl)}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV_PY="/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python"
RTL_DIR="/home/smy/opentitan/hw/ip/${IP}/rtl"
CHUNKS="output/${IP}_chunks.json"
SPECS="output/specs_${IP}"
OUT="output"

if [[ ! -d "$RTL_DIR" ]]; then
    echo "ERROR: RTL directory not found: $RTL_DIR"
    exit 1
fi

echo "============================================================"
echo "  CSBC: ${IP}"
echo "  RTL:    ${RTL_DIR}"
echo "  Chunks: ${CHUNKS}"
echo "  Specs:  ${SPECS}"
echo "============================================================"

# Step 1: Chunk
echo ">>> Step 1/3: Chunking RTL ..."
PYTHONUNBUFFERED=1 "$VENV_PY" -m rtl_bug_agent.cli chunk --rtl-dir "$RTL_DIR" --out "$CHUNKS" --prefilter

# Step 2: Spec generation
echo ">>> Step 2/3: Generating specs ..."
PYTHONUNBUFFERED=1 "$VENV_PY" scripts/generate_all_specs.py \
    --chunks "$CHUNKS" \
    --out-dir "$SPECS" \
    --workers 8

# Step 3: Phase 2 (all channels + layer2, no formal, no phase3)
echo ">>> Step 3/3: Phase 2 (B+C+D+L2, semantic AG) ..."
PYTHONUNBUFFERED=1 "$VENV_PY" \
    scripts/run_phase2_e2e.py \
    --ip "$IP" \
    --specs-dir "$SPECS" \
    --out-root "$OUT" \
    --channels B,C,D,L2 \
    --ag-pairing-mode semantic \
    --workers 8 \
    --trace

# Rename shadow file to match expected naming
mv -f "${OUT}/semantic_ag_shadow_${IP}.json" "${OUT}/semantic_ag_${IP}_new.json" 2>/dev/null || true

echo "============================================================"
echo "  Done: ${IP}"
echo "  Chunks:   ${CHUNKS}"
echo "  Specs:    ${SPECS}/"
echo "  Findings: ${OUT}/findings_${IP}.json"
echo "  Sem AG:   ${OUT}/semantic_ag_${IP}_new.json"
echo "  Trace:    ${OUT}/trace_${IP}.jsonl"
echo "============================================================"
