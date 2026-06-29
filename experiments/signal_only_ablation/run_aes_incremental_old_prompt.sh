#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/smy/rtl_bug_agent"
OUT_ROOT="${ROOT}/experiments/signal_only_ablation/aes_incremental_old_prompt"
OLD_SPECS_DIR="${ROOT}/output/specs_aes"
AES_RTL_DIR="/home/smy/opentitan/hw/ip/aes/rtl"
AES_MIN_DIR="${OUT_ROOT}/_aes_min_rtl"
CHUNKS_JSON="${OUT_ROOT}/aes_chunks_incremental.json"
SPEC_IDS_FILE="${OUT_ROOT}/aes_incremental_spec_ids.txt"
LOG_FILE="${OUT_ROOT}/run.log"
PROMPT_FILE="${ROOT}/config/prompts/chunk_spec.md"

mkdir -p "${OUT_ROOT}"
cd "${ROOT}"
rm -rf "${AES_MIN_DIR}"
mkdir -p "${AES_MIN_DIR}"

for f in aes_control.sv aes_control_fsm.sv aes_core.sv; do
  ln -s "${AES_RTL_DIR}/${f}" "${AES_MIN_DIR}/${f}"
done

echo "[1/4] Chunk AES RTL" | tee -a "${LOG_FILE}"
python3 -m rtl_bug_agent.cli chunk \
  --rtl-dir "${AES_MIN_DIR}" \
  --out "${CHUNKS_JSON}" \
  | tee -a "${LOG_FILE}"

echo "[2/4] Collect incremental chunk ids" | tee -a "${LOG_FILE}"
python3 - <<'PY' | tee -a "${LOG_FILE}"
from pathlib import Path
import json

chunks = json.loads(Path("/home/smy/rtl_bug_agent/experiments/signal_only_ablation/aes_incremental_old_prompt/aes_chunks_incremental.json").read_text(encoding="utf-8"))
wanted = [
    "aes_control__always_comb__combine_sparse_signals__001",
    "aes_control__generate_for__gen_fsm__001",
    "aes_control__generate_for__gen_sel_buf_chk__001",
    "aes_control_fsm__always_comb__aes_ctrl_fsm__001",
    "aes_core__always_comb__iv_mux__001",
    "aes_core__always_ff__iv_reg__001",
    "aes_core__generate_for__gen_iv_we__001",
    "aes_core__generate_for__gen_sel_buf_chk__001",
]
have = {c["chunk_id"] for c in chunks}
missing = [x for x in wanted if x not in have]
if missing:
    raise SystemExit(f"missing chunks: {missing}")
Path("/home/smy/rtl_bug_agent/experiments/signal_only_ablation/aes_incremental_old_prompt/aes_incremental_spec_ids.txt").write_text("\n".join(wanted) + "\n", encoding="utf-8")
print("incremental chunk ids:")
for x in wanted:
    print(" ", x)
PY

echo "[3/4] Generate incremental specs into old aes spec dir" | tee -a "${LOG_FILE}"
while IFS= read -r CID; do
  [ -n "${CID}" ] || continue
  python3 scripts/generate_selected_specs.py \
    --chunks "${CHUNKS_JSON}" \
    --out-dir "${OLD_SPECS_DIR}" \
    --provider GUOCHUANG_DEEPSEEK \
    --prompt "${PROMPT_FILE}" \
    "${CID}" \
    | tee -a "${LOG_FILE}"
done < "${SPEC_IDS_FILE}"

echo "[4/4] Run phase2 channel B only with old specs dir" | tee -a "${LOG_FILE}"
/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python \
  scripts/run_phase2_e2e.py \
  --ip aes \
  --out-root "${OUT_ROOT}/phase2" \
  --specs-dir "${OLD_SPECS_DIR}" \
  --ag-pairing-mode legacy \
  --channels B \
  --workers 8 \
  --channel-b-max-tokens 5000 \
  | tee -a "${LOG_FILE}"

echo "done" | tee -a "${LOG_FILE}"
