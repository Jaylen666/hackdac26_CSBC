#!/usr/bin/env bash
# Full pipeline: chunk → spec → Phase 2 for any IP.
# Usage: bash scripts/run_full_pipeline.sh hmac
#        bash scripts/run_full_pipeline.sh kmac
set -euo pipefail

IP="${1:?Usage: $0 <ip_name> (e.g. hmac, kmac)}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

RTL_DIR="/home/smy/opentitan/hw/ip/${IP}/rtl"

if [[ ! -d "$RTL_DIR" ]]; then
    echo "ERROR: RTL directory not found: $RTL_DIR"
    exit 1
fi

CHUNKS="output/${IP}_chunks.json"
SPECS="output/specs_${IP}"

echo "============================================================"
echo "  Pipeline: ${IP}"
echo "  RTL:    ${RTL_DIR}"
echo "  Chunks: ${CHUNKS}"
echo "  Specs:  ${SPECS}"
echo "============================================================"
echo ""

# ── Step 1: Chunk ────────────────────────────────────────────
echo ">>> Step 1/3: Chunking RTL ..."
python3 -m rtl_bug_agent.cli chunk --rtl-dir "$RTL_DIR" --out "$CHUNKS" --prefilter
echo ""

# ── Step 2: Spec generation ──────────────────────────────────
echo ">>> Step 2/3: Generating specs ..."
python3 scripts/generate_all_specs.py --chunks "$CHUNKS" --out-dir "$SPECS"
echo ""

# ── Step 3: Phase 2 ──────────────────────────────────────────
echo ">>> Step 3/3: Phase 2 bug detection ..."
python3 scripts/run_phase2_e2e.py --ip "$IP" --specs-dir "$SPECS"
echo ""

echo "============================================================"
echo "  Done. Findings: output/findings_${IP}.json"
echo "============================================================"
