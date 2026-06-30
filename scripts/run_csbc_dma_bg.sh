#!/usr/bin/env bash
# CSBC run for dma in background (survives SSH disconnect)
# Usage: bash scripts/run_csbc_dma_bg.sh
set -euo pipefail

IP=""
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG="output/run_${IP}_$(date +%Y%m%d_%H%M%S).log"

echo "Starting CSBC for ${IP} in background..."
echo "Log file: ${LOG}"
echo "Monitor with: tail -f ${LOG}"

nohup bash scripts/run_csbc_dma.sh > "${LOG}" 2>&1 &
PID=$!

echo "Background PID: ${PID}"
echo "To check status: ps -p ${PID}"
echo "To stop: kill ${PID}"
echo ""
echo "Detached. You can safely close SSH now."
