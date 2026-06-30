#!/usr/bin/env bash
# CSBC run for dma (no formal, no phase3, no fusion/clustering)
# Usage: bash scripts/run_csbc_dma.sh
#rv_dm,tlul,soc_dbg_ctrl
set -euo pipefail

IP="kmac"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

RTL_DIR="/home/smy/opentitan/hw/ip/${IP}/rtl"
CHUNKS="output/${IP}_chunks.json"
SPECS="output/specs_${IP}"
OUT="output"

echo "============================================================"
echo "  CSBC: ${IP}"
echo "  RTL:    ${RTL_DIR}"
echo "  Chunks: ${CHUNKS}"
echo "  Specs:  ${SPECS}"
echo "============================================================"

# Step 1: Chunk
echo ">>> Step 1/3: Chunking RTL ..."
PYTHONUNBUFFERED=1 /home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python -m rtl_bug_agent.cli chunk --rtl-dir "$RTL_DIR" --out "$CHUNKS" --prefilter

# Step 2: Spec generation
echo ">>> Step 2/3: Generating specs ..."
PYTHONUNBUFFERED=1 /home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python scripts/generate_all_specs.py \
    --chunks "$CHUNKS" \
    --out-dir "$SPECS" \
    --workers 8

# Step 3: Phase 2 (all channels + layer2, no formal, no phase3)
echo ">>> Step 3/3: Phase 2 (B+C+D+L2, semantic AG) ..."
PYTHONUNBUFFERED=1 /home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python \
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
echo "  Done."
echo "  Chunks:   ${CHUNKS}"
echo "  Specs:    ${SPECS}/"
echo "  Findings: ${OUT}/findings_${IP}.json"
echo "  Sem AG:   ${OUT}/semantic_ag_${IP}_new.json"
echo "  Trace:    ${OUT}/trace_${IP}.jsonl"
echo "============================================================"
