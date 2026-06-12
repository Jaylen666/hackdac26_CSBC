#!/usr/bin/env python3
"""
Test: give LLM only the raw code chunks involved in signal-only pairs,
no spec text, no signal hints.  Can it find the bugs?

This isolates two factors:
- Attention dilution (full source vs focused chunks)
- Lack of semantic anchors (spec text descriptions)
"""

import json, sys, os, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rtl_bug_agent.env import load_dotenv, make_client

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent / "out"

load_dotenv("/home/smy/.env")
client = make_client("GUOCHUANG_DEEPSEEK", thinking=None)

# ── Load bug pair coverage ──
with open(OUT_DIR / "bug_pair_coverage.json") as f:
    coverage = json.load(f)

# ── Minimal system prompt ──
SYSTEM_PROMPT = """You are a senior RTL design verification engineer.
Below are several SystemVerilog code fragments from the same hardware IP module.
These fragments were grouped together because they share related signal names.

Your task:
1. Read ALL fragments carefully.
2. Identify any potential hardware bugs or security vulnerabilities.
3. For each finding, describe:
   - What the bug is (concrete RTL behavior)
   - Which fragment(s) and line(s) contain the bug
   - Why it is a bug (security/functional impact)
   - Severity: HIGH / MEDIUM / LOW

Do NOT list benign observations, coding style issues, or suggestions.
Only report actual bugs or strong bug candidates.

Output format (valid JSON only, no markdown fences):
{
  "findings": [
    {
      "title": "short description",
      "severity": "HIGH|MEDIUM|LOW",
      "description": "detailed bug description with line references",
      "security_impact": "security consequence if applicable"
    }
  ]
}
"""

results = []

for entry in coverage:
    ip = entry["ip"]
    bug_id = entry["bug_id"]
    desc = entry["description"]
    status = entry["status"]

    if status == "MISS":
        results.append({
            "ip": ip, "bug_id": bug_id, "description": desc,
            "status": "SKIPPED_NO_PAIRS",
            "llm_raw": None, "num_chunks": 0,
        })
        print(f"\n[{ip}/{bug_id}] SKIPPED (no pairs)")
        continue

    # Collect unique chunks (deduplicate by chunk_id from atom_id)
    chunk_data = entry.get("involved_chunks", {})
    if not chunk_data:
        results.append({
            "ip": ip, "bug_id": bug_id, "description": desc,
            "status": "SKIPPED_NO_CHUNKS",
            "llm_raw": None, "num_chunks": 0,
        })
        continue

    # Build the code block
    code_blocks = []
    for chunk_id, cinfo in sorted(chunk_data.items()):
        src = cinfo.get("source_file", "").split("/")[-1]
        code_blocks.append(
            f"// Fragment: {chunk_id}\n"
            f"// Source: {src} lines {cinfo['line_start']}-{cinfo['line_end']}\n"
            f"{cinfo['code']}"
        )

    full_code = "\n\n".join(code_blocks)
    code_chars = len(full_code)
    num_chunks = len(chunk_data)

    print(f"\n[{ip}/{bug_id}] {num_chunks} chunks, {code_chars} chars ...", end=" ", flush=True)

    # Truncate if absurdly large (>40K chars = ~15K tokens for code)
    max_code_chars = 40000
    if code_chars > max_code_chars:
        # Keep first half of each block to stay under limit
        truncated = []
        per_block = max_code_chars // len(code_blocks)
        for block in code_blocks:
            if len(block) > per_block:
                block = block[:per_block] + "\n// ... (truncated)\n"
            truncated.append(block)
        full_code = "\n\n".join(truncated)
        print(f"(truncated to {len(full_code)} chars)", end=" ", flush=True)

    user_msg = f"Analyze the following RTL code fragments for hardware bugs:\n\n{full_code}"

    try:
        raw = client.chat(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=20000,
        )
        print(f"response={len(raw)} chars", end=" ", flush=True)

        # Try to parse JSON
        import re
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
            else:
                parsed = {"_parse_error": True, "_raw": raw[:2000]}

        findings = parsed.get("findings", [])
        print(f"→ {len(findings)} findings", flush=True)

        results.append({
            "ip": ip,
            "bug_id": bug_id,
            "description": desc,
            "status": "DONE",
            "num_chunks": num_chunks,
            "code_chars": code_chars,
            "llm_raw_response_chars": len(raw),
            "llm_parsed": parsed,
            "num_findings": len(findings),
        })

    except Exception as e:
        print(f"ERROR: {e}", flush=True)
        results.append({
            "ip": ip, "bug_id": bug_id, "description": desc,
            "status": f"ERROR: {e}",
            "num_chunks": num_chunks,
            "llm_raw": None,
        })

    # Small delay between calls
    time.sleep(1)

# ── Save results ──
out_path = OUT_DIR / "llm_raw_code_results.json"
with open(out_path, "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

# ── Summary ──
print(f"\n\n{'='*60}")
print(f"SUMMARY: LLM finds bugs from raw code chunks only")
print(f"{'='*60}")
print(f"{'IP':<8} {'Bug':<15} {'Chunks':>6} {'Findings':>8}  Key match?")
print("-" * 80)
for r in results:
    nf = r.get("num_findings", 0)
    nc = r.get("num_chunks", 0)
    # Quick check: does any finding title mention bug-relevant terms?
    match_hint = ""
    if isinstance(r.get("llm_parsed"), dict):
        for f in r["llm_parsed"].get("findings", []):
            title = f.get("title", "") + f.get("description", "")
            match_hint = "(see details)"
    print(f"{r['ip']:<8} {r['bug_id']:<15} {nc:>6} {nf:>8}  {match_hint}")

print(f"\nSaved: {out_path}")
