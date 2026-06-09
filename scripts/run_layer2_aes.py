#!/usr/bin/env python3
"""Run Layer 2 (pre-extracted claims + LLM semantic match) on AES findings.
Usage: python3 scripts/run_layer2_aes.py
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rtl_bug_agent.env import load_dotenv, make_client
from rtl_bug_agent.phase2.signal_graph import build_signal_graph
from rtl_bug_agent.phase2.layer2_pull import extract_all_claims, verify_finding_llm_match

load_dotenv("/home/smy/.env")
client = make_client("GUOCHUANG_DEEPSEEK")

# ── Step 1: Extract claims (retry until stable) ──────────────
print("=== Step 1: Extracting claims ===")
doc_path = "/home/smy/opentitan/hw/ip/aes/doc/theory_of_operation.md"
claims = []
for attempt in range(1, 4):
    claims = extract_all_claims(doc_path, client, attempts=1)
    if len(claims) >= 5:
        break
    print(f"  Attempt {attempt}: {len(claims)} claims, retrying...")
print(f"  {len(claims)} claims")
for i, c in enumerate(claims[:8]):
    print(f"    [{i+1}] {c.get('claim','')[:100]}")
if len(claims) > 8:
    print(f"    ... and {len(claims)-8} more")
print()

if not claims:
    print("FATAL: Could not extract claims after retries.")
    sys.exit(1)

# ── Step 2: Verify findings ──────────────────────────────────
findings = json.load(open("output/findings_aes.json"))
findings_list = findings.get("findings", findings) if isinstance(findings, dict) else findings
findings_list = [f for f in findings_list if f.get("score", 0) >= 0.6]
print(f"=== Step 2: Verifying {len(findings_list)} findings ===\n")

graph = build_signal_graph("output/specs_aes")
results = []

for i, f in enumerate(findings_list):
    fid = f.get("finding_id", f"F-{i}")
    title = f.get("title", "")[:80]
    print(f"[{i+1}/{len(findings_list)}] {fid}: {title} ... ", end="", flush=True)

    try:
        verdict = verify_finding_llm_match(f, claims, graph, client)
    except Exception as e:
        print(f"ERROR ({e})")
        verdict = {"verdict": "ERROR", "error": str(e)}

    v = verdict.get("verdict", "?")
    c = verdict.get("matched_claim_id", "")
    print(f"{v} (claim={c})")
    results.append({**f, "layer2_pull": verdict})

# ── Save ─────────────────────────────────────────────────────
out_path = Path("output/aes_layer2_pull.json")
out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nWrote {out_path}")

violations = sum(1 for r in results if r.get("layer2_pull", {}).get("verdict") == "VIOLATION")
print(f"VIOLATIONS: {violations}/{len(results)}")
client.print_stats()
