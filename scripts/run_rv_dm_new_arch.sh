#!/bin/bash
# Run RV_DM through the NEW architecture:
#   Phase 1: spec extraction (retry failed chunks)
#   Phase 2: semantic AG (BGE-M3) + Channel B + C + D + Layer 2
#   Phase 3: source-level verification (top 20 findings)
#
# Usage: bash scripts/run_rv_dm_new_arch.sh

set -euo pipefail
cd /home/smy/rtl_bug_agent

# ── Config ──────────────────────────────────────────────────────────
IP="rv_dm"
SPECS_DIR="output/specs_${IP}"
OUT_FILE="output/findings_${IP}_new.json"
VENV_PY="/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python"

echo "╔══════════════════════════════════════════════════════════╗"
echo "║  RV_DM — New Architecture Pipeline                      ║"
echo "║  Phase 1 (specs) + Phase 2 (semantic AG) + Phase 3      ║"
echo "╚══════════════════════════════════════════════════════════╝"

# ── Step 1: Retry failed spec generation ─────────────────────────────
echo ""
echo "=== Step 1: Retry failed specs ==="
python3 << 'PYEOF'
import json, sys
sys.path.insert(0, '/home/smy/rtl_bug_agent')

from rtl_bug_agent.env import load_dotenv, make_client
from rtl_bug_agent.spec.extractor import generate_chunk_spec
from rtl_bug_agent.schema import RtlChunk

load_dotenv("/home/smy/.env")
client = make_client("GUOCHUANG_DEEPSEEK", thinking=None)

specs_dir = "/home/smy/rtl_bug_agent/output/specs_rv_dm"
chunks_file = "/home/smy/rtl_bug_agent/output/rv_dm_chunks.json"

with open(chunks_file) as f:
    all_chunks = json.load(f)

# Find failed specs
failed_ids = set()
import os
for f in os.listdir(specs_dir):
    path = os.path.join(specs_dir, f)
    with open(path) as fh:
        spec = json.load(fh)
    if 'error' in spec:
        failed_ids.add(spec.get('chunk_id', '').strip())

print(f"Failed specs: {len(failed_ids)}")
for cid in sorted(failed_ids):
    print(f"  {cid}")

# Retry
retried = 0
for c in all_chunks:
    if c['chunk_id'] not in failed_ids:
        continue
    retried += 1

    chunk = RtlChunk(
        chunk_id=c['chunk_id'], kind=c['kind'],
        source_file=c['source_file'], module=c.get('module', 'rv_dm'),
        line_start=c['line_start'], line_end=c['line_end'],
        title=c.get('title', ''), context_summary=c.get('context_summary', ''),
        code=c['code'], dependencies=c.get('dependencies', []),
    )

    print(f"\n  [{retried}/{len(failed_ids)}] {c['chunk_id']} (L{c['line_start']}-{c['line_end']})")
    try:
        spec = generate_chunk_spec(chunk, client, max_tokens=4000)
        g = len(spec.get('guarantees', []))
        a = len(spec.get('assumptions', []))
        u = len(spec.get('uncertain_points', []))
        print(f"    ✓ {spec.get('summary','')[:100]}")
        print(f"    G:{g} A:{a} U:{u}")

        out_path = os.path.join(specs_dir, f"{c['chunk_id']}.json")
        with open(out_path, 'w') as fh:
            json.dump(spec, fh, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"    ✗ {e}")

print(f"\nRetried {retried} specs")
PYEOF

# ── Step 2: Verify all specs now valid ────────────────────────────────
echo ""
echo "=== Step 2: Verify specs ==="
python3 -c "
import json, os
specs_dir = 'output/specs_rv_dm'
errs = 0
ok = 0
for f in os.listdir(specs_dir):
    with open(os.path.join(specs_dir, f)) as fh:
        spec = json.load(fh)
    if 'error' in spec:
        errs += 1
        print(f'  ❌ {f}: {spec[\"error\"][:80]}')
    else:
        ok += 1
print(f'OK: {ok}, Errors: {errs}')
"

# ── Step 3: Run Phase 2 + Phase 3 ─────────────────────────────────────
echo ""
echo "=== Step 3: Phase 2 pipeline (semantic AG mode) ==="

python3 scripts/run_phase2_e2e.py \
    --ip "${IP}" \
    --specs-dir "${SPECS_DIR}" \
    --ag-pairing-mode semantic \
    --semantic-cache-dir output/.semantic_ag_cache \
    --semantic-batch-mode guarded \
    --semantic-max-queries-per-batch 5 \
    --semantic-max-prompt-tokens 5500 \
    --phase3-top-n 20 \
    --workers 8 \
    --force

echo ""
echo "=== Done ==="
echo "Output: ${OUT_FILE}"
ls -lh "${OUT_FILE}"
