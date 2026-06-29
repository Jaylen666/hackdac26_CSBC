#!/bin/bash
# Formal CSBC v2.0 端到端测试 - keymgr 模块
# 使用 DeepSeek v4-pro 模型
# Phase 3 由人工启动 Codex agent 完成（见 config/prompts/phase3/verify_agent.md）

set -e

OUTPUT_DIR="/home/smy/rtl_bug_agent/formal_csbc_v2"
VENV_PYTHON="/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python"

echo "========================================"
echo "Formal CSBC v2.0 E2E Test - keymgr"
echo "========================================"
echo "Output directory: ${OUTPUT_DIR}"
echo "Start time: $(date)"
echo ""

${VENV_PYTHON} /home/smy/rtl_bug_agent/scripts/run_phase2_e2e.py \
  --ip keymgr \
  --out-root "${OUTPUT_DIR}" \
  --specs-dir /home/smy/rtl_bug_agent/output/specs_keymgr \
  --rtl-root /home/smy/opentitan \
  --ag-pairing-mode semantic \
  --channels B \
  --channel-f \
  --channel-f-max-tokens 4000 \
  --channel-b-max-tokens 12000 \
  --run-solver \
  --solver-depth 20 \
  --solver-timeout 45 \
  --trace \
  --workers 4 \
  --semantic-max-queries-per-batch 5 2>&1 | tee "${OUTPUT_DIR}/keymgr_e2e_run.log"

echo ""
echo "========================================"
echo "Test completed: $(date)"
echo "========================================"
echo ""
echo "Output files:"
echo "  - Findings: ${OUTPUT_DIR}/findings_keymgr.json"
echo "  - Trace:    ${OUTPUT_DIR}/trace_keymgr.jsonl"
echo "  - Log:      ${OUTPUT_DIR}/keymgr_e2e_run.log"
echo ""
echo "Quick stats:"
${VENV_PYTHON} -c "
import json
from pathlib import Path
from collections import Counter

findings_file = Path('${OUTPUT_DIR}/findings_keymgr.json')
if not findings_file.exists():
    print('No findings file generated yet')
    exit()

data = json.loads(findings_file.read_text())
findings = data.get('findings', data) if isinstance(data, dict) else data
total = len(findings)
with_formal = sum(1 for f in findings if f.get('formal', {}).get('status'))
pending = sum(1 for f in findings if f.get('formal', {}).get('status') == 'PENDING')
with_formal_result = sum(1 for f in findings if f.get('formal_result'))
solver_verdicts = Counter(f.get('formal_result', {}).get('verdict') for f in findings if f.get('formal_result'))

print(f'Total findings:        {total}')
print(f'With formal SVA:       {with_formal}  (PENDING: {pending})')
print(f'With solver result:    {with_formal_result}')
if solver_verdicts:
    print(f'Solver verdicts:       {dict(solver_verdicts)}')
print()
print('Next step: start Codex agent with config/prompts/phase3/verify_agent.md')
print(f'  Findings: {findings_file}')
print(f'  RTL root: /home/smy/opentitan/hw/ip/keymgr/rtl/')
"
