#!/usr/bin/env python3
"""Ablation: Bare LLM on AES. Same protocol as HMAC baseline."""
import json, sys, time, os, requests
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rtl_bug_agent.env import load_dotenv

IP = "aes"
SPEC_PATH = Path(f"/home/smy/opentitan/hw/ip/{IP}/doc/theory_of_operation.md")
RTL_DIR = Path(f"/home/smy/opentitan/hw/ip/{IP}/rtl")

# Only the 4 files containing the 2 known CSBC bugs
HAND_WRITTEN = [
    "aes_cipher_core.sv", "aes_cipher_control.sv",
    "aes_cipher_control_fsm.sv", "aes_pkg.sv",
]

OUT_DIR = ROOT / "ablation" / "outputs"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SYSTEM_PROMPT = """You are a hardware verification engineer. Analyze the RTL code against the design spec and find ALL discrepancies or potential bugs. List every issue you discover — do not stop at one.

Output ONLY a JSON list. Each bug: {"title": "...", "description": "...", "severity": "HIGH|MEDIUM|LOW", "files": ["..."], "lines": "..."}. No explanation, no reasoning, no markdown. If none: []."""


def main():
    t0 = time.monotonic()
    load_dotenv("/home/smy/.env")

    spec_text = SPEC_PATH.read_text(encoding="utf-8")
    rtl_text = ""
    for fn in HAND_WRITTEN:
        code = (RTL_DIR / fn).read_text(encoding="utf-8")
        rtl_text += f"\n// ====== {fn} ({len(code.splitlines())} lines) ======\n"
        rtl_text += code

    total = len(spec_text) + len(rtl_text)
    print(f"IP: {IP}  Spec: {len(spec_text)} chars  RTL: {len(rtl_text)} chars  Total: {total:,} chars (~{total//3:,} tokens)")

    key = os.environ["GUOCHUANG_DEEPSEEK_API_KEY"]
    url = os.environ["GUOCHUANG_DEEPSEEK_BASE_URL"].rstrip("/")
    model = os.environ["GUOCHUANG_DEEPSEEK_MODEL"]

    payload = {
        "model": model, "max_tokens": 80000,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"=== DESIGN SPECIFICATION ===\n{spec_text}\n\n=== RTL SOURCE CODE ===\n{rtl_text}"},
        ],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    print("Calling LLM (max_tokens=80000) ... ", end="", flush=True)
    t_call = time.monotonic()
    resp = requests.post(f"{url}/chat/completions", headers=headers, json=payload, timeout=600)
    data = resp.json()
    msg = data["choices"][0]["message"]
    response = msg.get("content", "") or msg.get("reasoning_content", "")
    elapsed = time.monotonic() - t_call
    print(f"done in {elapsed:.0f}s")

    usage = data.get("usage", {})
    output = {
        "experiment": "ablation_baseline",
        "ip": IP,
        "rtl_files": HAND_WRITTEN,
        "total_input_chars": total,
        "estimated_input_tokens": total // 3,
        "output_tokens": usage.get("completion_tokens", 0),
        "wall_time_llm_s": round(elapsed, 1),
        "wall_time_total_s": round(time.monotonic() - t0, 1),
        "raw_response": response,
    }
    out_path = OUT_DIR / f"baseline_{IP}.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {out_path}")
    print(f"Output: {usage.get('completion_tokens', '?'):,} tokens in {elapsed:.0f}s")
    print(f"Response preview: {response[:300]}")


if __name__ == "__main__":
    main()
