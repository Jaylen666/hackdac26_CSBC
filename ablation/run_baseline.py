#!/usr/bin/env python3
"""
Ablation Experiment: Bare LLM vs CSBC Pipeline (方案 1)

Feeds the raw official spec + RTL source code directly to an LLM
with a neutral, unbiased prompt.  Compares whether the LLM can
find the same bugs the full CSBC pipeline detects.

Control variables:
  - Same model (DeepSeek v4-pro, via GUOCHUANG_DEEPSEEK)
  - Same input materials (HMAC theory_of_operation.md + HMAC RTL)
  - No CSBC pipeline stages (no Phase 1 spec extraction, no Phase 2)
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rtl_bug_agent.env import load_dotenv, make_client

# ── Configuration ───────────────────────────────────────────────
IP = "hmac"
SPEC_PATH = Path(f"/home/smy/opentitan/hw/ip/{IP}/doc/theory_of_operation.md")
RTL_DIR = Path(f"/home/smy/opentitan/hw/ip/{IP}/rtl")

# Only include hand-written RTL files (skip auto-generated reggen/topgen)
HAND_WRITTEN = {
    "hmac": ["hmac.sv", "hmac_core.sv"],
    "aes": [],  # placeholder for future
}

OUT_DIR = ROOT / "ablation" / "outputs"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Prompt (ZERO BIAS — no hints about bug types, locations, or CSBC) ──

SYSTEM_PROMPT = """You are a hardware verification engineer. Analyze the RTL code against the design spec and find ALL discrepancies or potential bugs. List every issue you discover — do not stop at one.

Output ONLY a JSON list. Each bug: {"title": "...", "description": "...", "severity": "HIGH|MEDIUM|LOW", "files": ["..."], "lines": "..."}. No explanation, no reasoning, no markdown. If none: []."""


def main() -> None:
    t0 = time.monotonic()

    # ── Collect inputs ────────────────────────────────────────
    if not SPEC_PATH.exists():
        print(f"ERROR: spec not found: {SPEC_PATH}")
        return

    spec_text = SPEC_PATH.read_text(encoding="utf-8")
    rtl_files = HAND_WRITTEN.get(IP, [])
    if not rtl_files:
        rtl_files = sorted(
            f.name for f in RTL_DIR.glob("*.sv")
            if "auto-generated" not in "\n".join(f.read_text().splitlines()[:10]).lower()
            and "_reg_" not in f.name
        )

    print(f"IP: {IP}")
    print(f"Spec: {SPEC_PATH} ({len(spec_text)} chars)")
    print(f"RTL files ({len(rtl_files)}):")
    rtl_text = ""
    for fn in rtl_files:
        fp = RTL_DIR / fn
        code = fp.read_text(encoding="utf-8")
        rtl_text += f"\n// ====== {fn} ({len(code.splitlines())} lines) ======\n"
        rtl_text += code
        print(f"  {fn}: {len(code.splitlines())} lines")

    total_input = len(spec_text) + len(rtl_text)
    est_tokens = total_input // 3
    print(f"\nTotal input: {total_input:,} chars (est. {est_tokens:,} tokens)")
    print()

    # ── Load LLM ──────────────────────────────────────────────
    load_dotenv("/home/smy/.env")
    client = make_client("GUOCHUANG_DEEPSEEK")

    # ── Call LLM ──────────────────────────────────────────────
    user_content = f"""=== DESIGN SPECIFICATION ===
{spec_text}

=== RTL SOURCE CODE ===
{rtl_text}
"""

    print("Calling LLM ... ", end="", flush=True)
    t_call = time.monotonic()

    # Direct call to access both content and reasoning_content
    import requests
    key, url, model = None, None, None
    for k, v in __import__('os').environ.items():
        if k == 'GUOCHUANG_DEEPSEEK_API_KEY': key = v
        if k == 'GUOCHUANG_DEEPSEEK_BASE_URL': url = v
        if k == 'GUOCHUANG_DEEPSEEK_MODEL': model = v
    if not (key and url and model):
        print("ERROR: missing GUOCHUANG_DEEPSEEK env vars")
        return

    url = url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model, "max_tokens": 80000,
        "thinking": None,  # disable reasoning — force direct answer
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=180)
        data = resp.json()
        msg = data["choices"][0]["message"]
        # Prefer content over reasoning_content (thinking is not the answer)
        response = msg.get("content", "")
        if not response:
            response = msg.get("reasoning_content", "")
        # Track stats manually
        usage = data.get("usage", {})
        client.call_count += 1
        client.total_input_tokens += usage.get("prompt_tokens", 0)
        client.total_output_tokens += usage.get("completion_tokens", 0)
        client.total_tokens += usage.get("total_tokens", 0)
    except Exception as e:
        print(f"\nERROR: {e}")
        return
    elapsed_call = time.monotonic() - t_call
    print(f"done in {elapsed_call:.0f}s")

    # ── Save results ──────────────────────────────────────────
    elapsed_total = time.monotonic() - t0
    stats = client.stats()

    output = {
        "experiment": "ablation_baseline",
        "ip": IP,
        "model": stats.get("model", "GUOCHUANG_DEEPSEEK"),
        "spec_source": str(SPEC_PATH),
        "rtl_files": rtl_files,
        "total_input_chars": total_input,
        "estimated_input_tokens": est_tokens,
        "call_stats": stats,
        "wall_time_llm_s": round(elapsed_call, 1),
        "wall_time_total_s": round(elapsed_total, 1),
        "raw_response": response,
    }

    out_path = OUT_DIR / f"baseline_{IP}.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {out_path}")

    # ── Print summary ─────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Run Statistics")
    print(f"{'='*60}")
    print(f"  LLM calls:          {stats['call_count']}")
    print(f"  Input tokens (est):  {est_tokens:,}")
    print(f"  Output tokens:       {stats.get('total_output_tokens', '?'):,}")
    print(f"  LLM wall time:       {elapsed_call:.0f}s")
    print(f"  Total wall time:     {elapsed_total:.0f}s")
    print(f"\n  Response preview (first 500 chars):")
    print(f"  {response[:500]}")


if __name__ == "__main__":
    main()
