#!/bin/bash
# 实时监控 Formal CSBC v2.0 执行进度

OUTPUT_DIR="/home/smy/rtl_bug_agent/formal_csbc_v2"
LOG_FILE="${OUTPUT_DIR}/keymgr_e2e_run.log"

echo "========================================"
echo "Formal CSBC v2.0 Progress Monitor"
echo "========================================"
echo ""

if [ ! -f "${LOG_FILE}" ]; then
    echo "Log file not found: ${LOG_FILE}"
    echo "Run ./run_keymgr_e2e.sh first"
    exit 1
fi

echo "Latest progress:"
echo "----------------------------------------"
tail -50 "${LOG_FILE}"
echo "----------------------------------------"
echo ""

echo "Key milestones:"
echo ""

# Pass 0: Signal Graph
if grep -q "Pass 0: Signal Dependency Graph" "${LOG_FILE}"; then
    echo "✓ Pass 0: Signal Dependency Graph"
    grep "SignalGraph:" "${LOG_FILE}" | tail -1
fi

# Semantic AG
if grep -q "Semantic AG:" "${LOG_FILE}"; then
    echo "✓ Semantic AG Pairing"
    grep "Semantic AG:" "${LOG_FILE}" | grep -E "(pairs|query units|unmatched)" | tail -1
fi

# Channel B
if grep -q "Channel B semantic:" "${LOG_FILE}"; then
    echo "✓ Channel B (Semantic Mode)"
    grep -E "Channel B semantic:" "${LOG_FILE}" | tail -1
fi

# Channel F
if grep -q "Channel F:" "${LOG_FILE}"; then
    echo "✓ Channel F (Unpaired Items)"
    grep -E "Channel F:" "${LOG_FILE}" | tail -3
fi

# Formal Solver
if grep -q "Formal solver:" "${LOG_FILE}" || grep -q "run_formal_solver" "${LOG_FILE}"; then
    echo "✓ Formal Solver Execution"
    grep -E "(Formal solver|formal_result)" "${LOG_FILE}" | tail -5
fi

# Phase 3
if grep -q "Phase3" "${LOG_FILE}"; then
    echo "✓ Phase 3 Verification"
    grep -E "Phase3.*\[" "${LOG_FILE}" | tail -5
fi

echo ""
echo "Checkpoints:"
ls -lh "${OUTPUT_DIR}"/*.json 2>/dev/null | tail -10 || echo "No checkpoint files yet"

echo ""
echo "Trace records:"
if [ -f "${OUTPUT_DIR}/trace_keymgr.jsonl" ]; then
    wc -l "${OUTPUT_DIR}/trace_keymgr.jsonl"
else
    echo "No trace file yet"
fi
