#!/usr/bin/env python3
"""Run Channel D standalone and save findings.
Usage: python3 scripts/run_channel_d.py --specs-dir output/specs_hmac --out output/channel_d_findings.json
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rtl_bug_agent.env import load_dotenv, make_client
from rtl_bug_agent.phase2.signal_graph import build_signal_graph
from rtl_bug_agent.phase2.channel_d import run_channel_d

parser = argparse.ArgumentParser()
parser.add_argument("--specs-dir", required=True)
parser.add_argument("--out", required=True)
parser.add_argument("--top-n", type=int, default=50)
parser.add_argument("--no-llm", action="store_true",
                    help="Script-only mode: output scored pairs without LLM verification")
args = parser.parse_args()

load_dotenv("/home/smy/.env")
if args.no_llm:
    # Script-only: just generate pairs, no LLM
    graph = build_signal_graph(args.specs_dir)
    from rtl_bug_agent.phase2.channel_d import (
        _extract_timing_atoms, _build_causal_index, _generate_anchors, _generate_pairs
    )
    atoms = _extract_timing_atoms(graph)
    causal_index = _build_causal_index(graph, atoms, max_depth=4)
    anchors = _generate_anchors(graph, atoms, causal_index)
    pairs = _generate_pairs(anchors, atoms, causal_index, max_pairs=args.top_n)
    # Serialize atoms for readability
    output = []
    for p in pairs:
        output.append({
            "signal_pair": [p["signal_a"], p["signal_b"]],
            "anchor": p["anchor"],
            "risk_score": p["risk_score"],
            "atom_a": {
                "signal": p["atoms_a"]["signal"],
                "timing_class": p["atoms_a"]["timing_class"],
                "event_class": p["atoms_a"]["event_class"],
                "transition": p["atoms_a"]["transition"],
                "property_snippet": p["atoms_a"].get("property_snippet", "")[:200],
            },
            "atom_b": {
                "signal": p["atoms_b"]["signal"],
                "timing_class": p["atoms_b"]["timing_class"],
                "event_class": p["atoms_b"]["event_class"],
                "transition": p["atoms_b"]["transition"],
                "property_snippet": p["atoms_b"].get("property_snippet", "")[:200],
            },
            "path_a": p.get("path_a", []),
            "path_b": p.get("path_b", []),
        })
    findings = output
else:
    client = make_client("GUOCHUANG_DEEPSEEK")
    graph = build_signal_graph(args.specs_dir)
    findings = run_channel_d(graph, client, top_n_pairs=args.top_n)
    client.print_stats()

Path(args.out).write_text(
    json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8"
)
if not args.no_llm:
    client.print_stats()
print(f"Wrote {len(findings)} findings to {args.out}")
