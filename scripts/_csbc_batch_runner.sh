#!/usr/bin/env bash
# Internal runner: runs CSBC sequentially for the listed IPs.
# Invoked by run_csbc_batch_bg.sh via nohup. Not meant to be run directly.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TS="${1:?missing timestamp}"
shift
IPS=("$@")

echo "############################################################"
echo "  CSBC batch start: ${TS}"
echo "  IPs (in order): ${IPS[*]}"
echo "############################################################"

for ip in "${IPS[@]}"; do
    ip_log="output/run_${ip}_${TS}.log"
    echo ""
    echo "############################################################"
    echo "  [$(date +%H:%M:%S)] START ${ip}  (log: ${ip_log})"
    echo "############################################################"

    if bash scripts/run_csbc.sh "$ip" > "$ip_log" 2>&1; then
        echo "  [$(date +%H:%M:%S)] DONE  ${ip}  OK"
    else
        echo "  [$(date +%H:%M:%S)] FAIL  ${ip}  (see ${ip_log}); continuing to next IP"
    fi
done

echo ""
echo "############################################################"
echo "  [$(date +%H:%M:%S)] Batch complete."
echo "############################################################"
