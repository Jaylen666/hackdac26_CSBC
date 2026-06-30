#!/usr/bin/env bash
# Run CSBC sequentially for multiple IPs in the background (survives SSH disconnect).
# Each IP runs to completion before the next starts. If one IP fails, the next
# still runs (failures are logged, not fatal).
#
# Usage: bash scripts/run_csbc_batch_bg.sh [ip1 ip2 ...]
#        bash scripts/run_csbc_batch_bg.sh              # uses default list below
#        bash scripts/run_csbc_batch_bg.sh dma kmac     # custom list

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Default IP list (in order) — override by passing args
if [[ $# -gt 0 ]]; then
    IPS=("$@")
else
    IPS=(rv_dm tlul soc_dbg_ctrl)
fi

TS="$(date +%Y%m%d_%H%M%S)"
BATCH_LOG="output/batch_${TS}.log"

echo "Starting CSBC batch in background..."
echo "  IPs (in order): ${IPS[*]}"
echo "  Batch log:    ${BATCH_LOG}"
echo "  Per-IP logs:  output/run_<ip>_${TS}.log"

nohup bash scripts/_csbc_batch_runner.sh "$TS" "${IPS[@]}" > "$BATCH_LOG" 2>&1 &
PID=$!

echo "  Background PID: ${PID}"
echo ""
echo "Monitor with:"
echo "  tail -f ${BATCH_LOG}                 # batch progress (which IP is running)"
echo "  tail -f output/run_${IPS[0]}_${TS}.log   # detailed per-IP progress"
echo "To stop: kill ${PID}  (also kill child python if needed)"
echo ""
echo "Detached. You can safely close SSH now."
