#!/usr/bin/env python3
"""Agent-based Phase 3 (GPT function calling).
Usage:
    python3 scripts/run_phase3_agent.py --findings output/findings_hmac_curated_top10.json --specs-dir output/specs --top 10
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rtl_bug_agent.env import load_dotenv, make_client
from rtl_bug_agent.phase2.signal_graph import build_signal_graph
from rtl_bug_agent.phase2.phase3_agent import verify_top_findings_agent

parser = argparse.ArgumentParser()
parser.add_argument("--findings", required=True)
parser.add_argument("--top", type=int, default=10)
parser.add_argument("--specs-dir", required=True)
args = parser.parse_args()

load_dotenv("/home/smy/.env")
client = make_client("OPENAI", timeout_s=180)

data = json.loads(Path(args.findings).read_text(encoding="utf-8"))
findings = data.get("findings", data)
findings.sort(key=lambda f: f.get("score", 0), reverse=True)

graph = build_signal_graph(args.specs_dir)

print(f"Phase3 Agent: verifying top {args.top} of {len(findings)} findings")
verified = verify_top_findings_agent(findings, graph, client, top_n=args.top)

out_path = Path(args.findings).with_suffix(".agent.json")
output = {"_source": args.findings, "findings": verified}
out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Wrote {out_path}")
client.print_stats()
