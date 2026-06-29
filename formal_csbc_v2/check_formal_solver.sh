#!/bin/bash
# 检查 Formal Solver 执行详情

OUTPUT_DIR="/home/smy/rtl_bug_agent/formal_csbc_v2"
FINDINGS_FILE="${OUTPUT_DIR}/findings_keymgr.json"
LOG_FILE="${OUTPUT_DIR}/keymgr_e2e_run.log"

echo "========================================"
echo "Formal Solver Execution Details"
echo "========================================"
echo ""

if [ ! -f "${FINDINGS_FILE}" ]; then
    echo "Findings file not found: ${FINDINGS_FILE}"
    echo "Run ./run_keymgr_e2e.sh first"
    exit 1
fi

echo "Analyzing formal solver results..."
echo ""

python3 << 'PYEOF'
import json
from pathlib import Path
from collections import Counter

findings_file = Path("/home/smy/rtl_bug_agent/formal_csbc_v2/findings_keymgr.json")
findings = json.loads(findings_file.read_text())

print(f"Total findings: {len(findings)}")
print("")

# 统计 formal status
formal_statuses = []
for f in findings:
    formal = f.get("formal", {})
    if isinstance(formal, dict) and formal.get("status"):
        formal_statuses.append(formal["status"])

if formal_statuses:
    print("Formal property status distribution:")
    for status, count in Counter(formal_statuses).most_common():
        print(f"  {status}: {count}")
    print("")

# 统计 formal_result
formal_results = []
solver_verdicts = []
for f in findings:
    formal_result = f.get("formal_result", {})
    if isinstance(formal_result, dict):
        if formal_result.get("status"):
            formal_results.append(formal_result["status"])
        if formal_result.get("verdict"):
            solver_verdicts.append(formal_result["verdict"])

if formal_results:
    print("Formal solver execution status:")
    for status, count in Counter(formal_results).most_common():
        print(f"  {status}: {count}")
    print("")

if solver_verdicts:
    print("Formal solver verdict distribution:")
    for verdict, count in Counter(solver_verdicts).most_common():
        print(f"  {verdict}: {count}")
    print("")

# 详细展示前 5 个有 formal_result 的 finding
findings_with_formal = [f for f in findings if f.get("formal_result")]
if findings_with_formal:
    print(f"Findings with formal_result: {len(findings_with_formal)}")
    print("")
    print("Top 5 findings with formal solver results:")
    print("=" * 80)

    for i, f in enumerate(findings_with_formal[:5], 1):
        print(f"\n[{i}] {f.get('finding_id', 'N/A')}")
        print(f"Title: {f.get('title', 'N/A')[:80]}")
        print(f"Score: {f.get('score', 0):.2f}")

        formal = f.get("formal", {})
        if isinstance(formal, dict):
            print(f"Formal status: {formal.get('status', 'N/A')}")
            sva = formal.get('sva', '')
            if sva:
                print(f"SVA: {sva[:100]}...")

        formal_result = f.get("formal_result", {})
        if isinstance(formal_result, dict):
            print(f"Solver verdict: {formal_result.get('verdict', 'N/A')}")
            print(f"Solver status: {formal_result.get('status', 'N/A')}")
            print(f"Solver engine: {formal_result.get('engine', 'N/A')}")
            duration = formal_result.get('duration_s')
            if duration:
                print(f"Duration: {duration:.2f}s")

        phase3 = f.get("phase3", {})
        if isinstance(phase3, dict):
            print(f"Phase 3 verdict: {phase3.get('verdict', 'N/A')}")
            print(f"Phase 3 confidence: {phase3.get('confidence', 'N/A')}")
            alignment = phase3.get('formal_alignment')
            if alignment:
                print(f"Formal alignment: {alignment[:100]}")

        print("-" * 80)
else:
    print("No findings with formal_result found")
    print("")
    print("Possible reasons:")
    print("  1. No findings reached PENDING status (check formal.status)")
    print("  2. Solver execution not triggered (check --run-solver flag)")
    print("  3. All SVAs failed validation (check formal.status = NAME_UNVERIFIED)")

PYEOF

echo ""
echo "Log excerpts - Formal solver execution:"
if [ -f "${LOG_FILE}" ]; then
    grep -E "(Formal solver|formal_result|PENDING|sby|z3)" "${LOG_FILE}" | tail -20
else
    echo "No log file found"
fi
