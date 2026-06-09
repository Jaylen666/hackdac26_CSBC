#!/usr/bin/env python3
"""Test uncertain-point collection on HMAC specs."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rtl_bug_agent.phase2.signal_graph import build_signal_graph
from rtl_bug_agent.phase2.uncertain_collector import (
    collect_and_classify, print_summary,
)

g = build_signal_graph("/home/smy/rtl_bug_agent/output/specs")
ch_b, ph3 = collect_and_classify(g)
print_summary(ch_b, ph3)

# Show which known bugs get a hit
bugs = {
    "Bug 009 (wipe/cfg)": ["cfg_block", "wipe_secret", "secret_key"],
    "Bug 010 (SHA-512)": ["sha_msg_len", "SHA2_512", "digest_size"],
    "Bug 011 (stale)": ["hash_done_event", "in_process", "cool_down_ct"],
    "Bug 019 (alert)": ["alert_tx", "alerts"],
}

print("\n=== Known Bug Hit Check ===")
for bug_name, kw_list in bugs.items():
    hits = [c for c in ch_b + ph3 if any(kw in c.get("uncertain_text","") for kw in kw_list)]
    if hits:
        dest = "ChB" if hits[0] in ch_b else "Ph3"
        print(f"  {bug_name}: {len(hits)} hit(s) → {dest}")
        for h in hits[:2]:
            print(f"    {h['uncertain_text'][:150]}")
    else:
        print(f"  {bug_name}: 0 hits")
