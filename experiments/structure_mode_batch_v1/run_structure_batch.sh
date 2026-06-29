#!/usr/bin/env bash
# Run structural-aware CSBC experiment for multiple OpenTitan IPs.
#
# Output layout matches experiments/structure_mode_trial_v4/hmac:
#   experiments/structure_mode_trial_v4/<ip>/<ip>_chunks.json
#   experiments/structure_mode_trial_v4/<ip>/<ip>_chunk_manifest.json
#   experiments/structure_mode_trial_v4/<ip>/<ip>_structure_facts.jsonl
#   experiments/structure_mode_trial_v4/<ip>/specs_<ip>/
#   experiments/structure_mode_trial_v4/<ip>/phase2_semantic/

set -u
set -o pipefail

REPO_ROOT="${REPO_ROOT:-/home/smy/rtl_bug_agent}"
OPENTITAN_ROOT="${OPENTITAN_ROOT:-/home/smy/opentitan}"
RUN_ROOT="${RUN_ROOT:-${REPO_ROOT}/experiments/structure_mode_trial_v4}"

PY_SEM="${PY_SEM:-${REPO_ROOT}/experiments/bge_m3_ag_retrieval/.venv/bin/python}"
PY_MAIN="${PY_MAIN:-${PY_SEM}}"

WORKERS="${WORKERS:-8}"
CHANNEL_B_MAX_TOKENS="${CHANNEL_B_MAX_TOKENS:-10000}"

MODULES=(kmac uart aes keymgr)

mkdir -p "${RUN_ROOT}/logs"
MASTER_LOG="${RUN_ROOT}/run_structure_batch.log"

log_master() {
  local msg="$1"
  printf '[%s] %s\n' "$(date '+%F %T')" "${msg}" | tee -a "${MASTER_LOG}"
}

log_module() {
  local module_log="$1"
  local msg="$2"
  printf '[%s] %s\n' "$(date '+%F %T')" "${msg}" | tee -a "${MASTER_LOG}" | tee -a "${module_log}" >/dev/null
}

run_cmd() {
  local module_log="$1"
  shift

  log_module "${module_log}" "CMD: $*"
  "$@" > >(tee -a "${module_log}" | tee -a "${MASTER_LOG}" >/dev/null) 2>&1
  local rc=$?
  log_module "${module_log}" "EXIT ${rc}: $*"
  return "${rc}"
}

if [[ ! -d "${REPO_ROOT}" ]]; then
  echo "Missing REPO_ROOT: ${REPO_ROOT}" >&2
  exit 2
fi

if [[ ! -x "${PY_SEM}" ]]; then
  echo "Missing semantic python environment: ${PY_SEM}" >&2
  echo "Expected the BGE-M3 experiment venv with FlagEmbedding installed." >&2
  exit 2
fi

cd "${REPO_ROOT}" || exit 2

log_master "============================================================"
log_master "Structural-mode batch started"
log_master "REPO_ROOT=${REPO_ROOT}"
log_master "OPENTITAN_ROOT=${OPENTITAN_ROOT}"
log_master "RUN_ROOT=${RUN_ROOT}"
log_master "PY_MAIN=${PY_MAIN}"
log_master "PY_SEM=${PY_SEM}"
log_master "WORKERS=${WORKERS}"
log_master "CHANNEL_B_MAX_TOKENS=${CHANNEL_B_MAX_TOKENS}"
log_master "MODULES=${MODULES[*]}"
log_master "============================================================"

for ip in "${MODULES[@]}"; do
  rtl_dir="${OPENTITAN_ROOT}/hw/ip/${ip}/rtl"
  mod_dir="${RUN_ROOT}/${ip}"
  specs_dir="${mod_dir}/specs_${ip}"
  phase2_dir="${mod_dir}/phase2_semantic"
  chunks_file="${mod_dir}/${ip}_chunks.json"
  manifest_file="${mod_dir}/${ip}_chunk_manifest.json"
  facts_file="${mod_dir}/${ip}_structure_facts.jsonl"
  module_log="${RUN_ROOT}/logs/${ip}.log"

  mkdir -p "${mod_dir}" "${specs_dir}" "${phase2_dir}" "$(dirname "${module_log}")"
  : > "${module_log}"

  log_module "${module_log}" "============================================================"
  log_module "${module_log}" "START ${ip}"
  log_module "${module_log}" "RTL_DIR=${rtl_dir}"
  log_module "${module_log}" "MODULE_DIR=${mod_dir}"

  if [[ ! -d "${rtl_dir}" ]]; then
    log_module "${module_log}" "SKIP ${ip}: missing RTL dir ${rtl_dir}"
    continue
  fi

  # Step 1: Structural-aware chunking.
  if run_cmd "${module_log}" \
    "${PY_MAIN}" -m rtl_bug_agent.cli chunk \
      --rtl-dir "${rtl_dir}" \
      --out "${chunks_file}" \
      --structural-aware \
      --manifest-out "${manifest_file}" \
      --structure-facts-out "${facts_file}"; then
    log_module "${module_log}" "CHUNK OK ${ip}"
  else
    log_module "${module_log}" "CHUNK FAIL ${ip}; skipping spec and phase2 for this module"
    continue
  fi

  # Step 2: Spec generation for behavior chunks. Existing specs are skipped,
  # so the script is resumable without deleting partial outputs.
  if run_cmd "${module_log}" \
    "${PY_MAIN}" scripts/generate_all_specs.py \
      --chunks "${chunks_file}" \
      --out-dir "${specs_dir}" \
      --workers "${WORKERS}"; then
    log_module "${module_log}" "SPEC OK ${ip}"
  else
    log_module "${module_log}" "SPEC FAIL ${ip}; continuing to phase2 only if specs exist"
  fi

  # Step 3: Phase2 semantic Channel B only, with structural facts attached.
  if run_cmd "${module_log}" \
    "${PY_SEM}" scripts/run_phase2_e2e.py \
      --ip "${ip}" \
      --out-root "${phase2_dir}" \
      --specs-dir "${specs_dir}" \
      --structural-facts "${facts_file}" \
      --ag-pairing-mode semantic \
      --semantic-batch-mode guarded \
      --channels B \
      --workers "${WORKERS}" \
      --channel-b-max-tokens "${CHANNEL_B_MAX_TOKENS}"; then
    log_module "${module_log}" "PHASE2 CHANNEL-B OK ${ip}"
  else
    log_module "${module_log}" "PHASE2 CHANNEL-B FAIL ${ip}"
  fi

  log_module "${module_log}" "DONE ${ip}"
  log_module "${module_log}" "Outputs:"
  log_module "${module_log}" "  chunks: ${chunks_file}"
  log_module "${module_log}" "  manifest: ${manifest_file}"
  log_module "${module_log}" "  structure facts: ${facts_file}"
  log_module "${module_log}" "  specs: ${specs_dir}"
  log_module "${module_log}" "  phase2: ${phase2_dir}/findings_${ip}.json"
done

log_master "============================================================"
log_master "Structural-mode batch finished"
log_master "Master log: ${MASTER_LOG}"
log_master "Per-module logs: ${RUN_ROOT}/logs"
log_master "============================================================"
