#!/usr/bin/env python3
"""Compare legacy signal AG pairing with optimized semantic pairing.

This script is experiment-local and does not modify the main framework.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPO = Path("/home/smy/rtl_bug_agent")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

import plan_llm_batches as batch_plan  # noqa: E402
from rtl_bug_agent.phase2.signal_graph import build_signal_graph  # noqa: E402


MODULES = {
    "hmac": (REPO / "output/specs", "hmac"),
    "aes": (REPO / "output/specs_aes", "aes"),
    "keymgr": (REPO / "output/specs_keymgr", "keymgr"),
    "rv_dm": (REPO / "output/specs_rv_dm", "rv_dm"),
    "kmac": (REPO / "output/specs_kmac", "kmac"),
    "uart": (REPO / "output/specs_uart", "uart"),
}


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=HERE)


def ensure_artifacts(module: str, specs_dir: Path, prefix: str, rebuild_embeddings: bool) -> None:
    atoms = HERE / f"out/atoms_{module}.jsonl"
    emb = HERE / f"out/embeddings_{module}.npz"
    opt = HERE / f"out/optimized_pairs_{module}.json"

    run([
        sys.executable,
        str(HERE / "build_hmac_atoms.py"),
        "--module",
        prefix,
        "--specs-dir",
        str(specs_dir),
        "--out",
        str(atoms),
    ])
    if rebuild_embeddings or not emb.exists():
        run([
            sys.executable,
            str(HERE / "embed_atoms.py"),
            "--atoms",
            str(atoms),
            "--out",
            str(emb),
            "--batch-size",
            "8",
        ])
    run([
        sys.executable,
        str(HERE / "pair_ag_optimized.py"),
        "--atoms",
        str(atoms),
        "--embeddings",
        str(emb),
        "--out",
        str(opt),
    ])


def legacy_stats(specs_dir: Path) -> dict[str, Any]:
    graph = build_signal_graph(specs_dir)
    all_pairs = graph.find_ag_pairs(filter_mode="all")
    beh_pairs = graph.find_ag_pairs(filter_mode="behavioral")
    return {
        "legacy_all_pair_items": len(all_pairs),
        "legacy_all_edges": sum(len(p.get("driver_guarantees", [])) for p in all_pairs),
        "legacy_all_signals": len({p["signal"] for p in all_pairs}),
        "legacy_behavioral_pair_items": len(beh_pairs),
        "legacy_behavioral_edges": sum(len(p.get("driver_guarantees", [])) for p in beh_pairs),
        "legacy_behavioral_signals": len({p["signal"] for p in beh_pairs}),
    }


def atom_stats(module: str) -> dict[str, int]:
    atoms_path = HERE / f"out/atoms_{module}.jsonl"
    counts = {"assumption_atoms": 0, "guarantee_atoms": 0, "uncertain_atoms": 0, "total_atoms": 0}
    with atoms_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            atom = json.loads(line)
            counts["total_atoms"] += 1
            key = atom["kind"] + "_atoms"
            counts[key] = counts.get(key, 0) + 1
    return counts


def optimized_stats(module: str) -> dict[str, Any]:
    data = json.loads((HERE / f"out/optimized_pairs_{module}.json").read_text(encoding="utf-8"))
    meta = data["metadata"]
    non_empty = [r for r in data["results"] if r["matches"]]
    return {
        "optimized_selected_pairs": meta["num_selected_pairs"],
        "optimized_assumption_pairs": meta["selected_pairs_by_query_kind"].get("assumption", 0),
        "optimized_uncertain_pairs": meta["selected_pairs_by_query_kind"].get("uncertain", 0),
        "optimized_normal_pairs": meta["selected_pairs_by_pair_type"].get("normal", 0),
        "optimized_uncertain_with_signal_pairs": meta["selected_pairs_by_pair_type"].get("uncertain_with_signal", 0),
        "optimized_uncertain_dense_fallback_pairs": meta["selected_pairs_by_pair_type"].get("uncertain_dense_fallback", 0),
        "optimized_nonempty_queries": len(non_empty),
        "optimized_nonempty_assumption_queries": sum(1 for r in non_empty if r["query"]["kind"] == "assumption"),
        "optimized_nonempty_uncertain_queries": sum(1 for r in non_empty if r["query"]["kind"] == "uncertain"),
    }


def batch_stats(module: str, max_queries: int, max_prompt_tokens: int) -> dict[str, Any]:
    data = json.loads((HERE / f"out/optimized_pairs_{module}.json").read_text(encoding="utf-8"))
    units = batch_plan.query_units(data)
    topic_batches = batch_plan.pack_units(
        units, max_queries=max_queries, max_prompt_tokens=max_prompt_tokens, group_key="topic"
    )
    fixed_batches = batch_plan.pack_units(
        units, max_queries=max_queries, max_prompt_tokens=max_prompt_tokens, group_key=None
    )
    # Generic family-free approximation: strict topic is quality-favoring; fixed is cost lower bound.
    topic_summary = batch_plan.summarize_batches(topic_batches)
    fixed_summary = batch_plan.summarize_batches(fixed_batches)
    return {
        "new_calls_one_query": len(units),
        "new_calls_fixed_pack5": fixed_summary["calls"],
        "new_calls_topic_pack5": topic_summary["calls"],
        "new_est_tokens_one_query": sum(u["est_tokens"] + 780 for u in units),
        "new_est_tokens_fixed_pack5": fixed_summary["total_prompt_tokens"],
        "new_est_tokens_topic_pack5": topic_summary["total_prompt_tokens"],
        "new_topic_pack_avg_queries": topic_summary["avg_queries_per_call"],
        "new_fixed_pack_avg_queries": fixed_summary["avg_queries_per_call"],
    }


def summarize_module(module: str, specs_dir: Path, max_queries: int, max_prompt_tokens: int) -> dict[str, Any]:
    row: dict[str, Any] = {
        "module": module,
        "spec_files": len([p for p in specs_dir.glob("*.json") if p.name != "_stats.json"]),
    }
    row.update(atom_stats(module))
    row.update(legacy_stats(specs_dir))
    row.update(optimized_stats(module))
    row.update(batch_stats(module, max_queries, max_prompt_tokens))

    # Old framework note from user: uncertain points were also individually sent to LLM.
    row["legacy_plus_uncertain_calls_behavioral"] = row["legacy_behavioral_edges"] + row["uncertain_atoms"]
    row["legacy_plus_uncertain_work_items_behavioral"] = row["legacy_behavioral_edges"] + row["uncertain_atoms"]
    row["optimized_vs_legacy_behavioral_edge_ratio"] = (
        row["optimized_selected_pairs"] / row["legacy_behavioral_edges"]
        if row["legacy_behavioral_edges"] else None
    )
    row["optimized_vs_legacy_plus_uncertain_work_ratio"] = (
        row["optimized_selected_pairs"] / row["legacy_plus_uncertain_work_items_behavioral"]
        if row["legacy_plus_uncertain_work_items_behavioral"] else None
    )
    row["call_reduction_topic_pack_vs_legacy_plus_uncertain"] = (
        1 - row["new_calls_topic_pack5"] / row["legacy_plus_uncertain_calls_behavioral"]
        if row["legacy_plus_uncertain_calls_behavioral"] else None
    )
    row["call_reduction_fixed_pack_vs_legacy_plus_uncertain"] = (
        1 - row["new_calls_fixed_pack5"] / row["legacy_plus_uncertain_calls_behavioral"]
        if row["legacy_plus_uncertain_calls_behavioral"] else None
    )
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modules", nargs="+", default=list(MODULES))
    parser.add_argument("--rebuild-embeddings", action="store_true")
    parser.add_argument("--max-queries", type=int, default=5)
    parser.add_argument("--max-prompt-tokens", type=int, default=6000)
    parser.add_argument("--out", type=Path, default=HERE / "out/multi_module_comparison.json")
    args = parser.parse_args()

    rows = []
    for module in args.modules:
        specs_dir, prefix = MODULES[module]
        ensure_artifacts(module, specs_dir, prefix, args.rebuild_embeddings)
        rows.append(summarize_module(module, specs_dir, args.max_queries, args.max_prompt_tokens))

    output = {
        "config": {
            "modules": args.modules,
            "max_queries": args.max_queries,
            "max_prompt_tokens": args.max_prompt_tokens,
            "note": "Legacy+uncertain assumes legacy behavioral AG edges and each uncertain point is an individual work item.",
        },
        "rows": rows,
    }
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    for row in rows:
        print(
            row["module"],
            "legacy_edges=", row["legacy_behavioral_edges"],
            "uncertain=", row["uncertain_atoms"],
            "legacy+unc=", row["legacy_plus_uncertain_work_items_behavioral"],
            "optimized_pairs=", row["optimized_selected_pairs"],
            "nonempty_queries=", row["optimized_nonempty_queries"],
            "calls(topic/fixed)=", row["new_calls_topic_pack5"], row["new_calls_fixed_pack5"],
        )


if __name__ == "__main__":
    main()
