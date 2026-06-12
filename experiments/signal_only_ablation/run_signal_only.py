#!/usr/bin/env python3
"""
Ablation: pure signal-name AG pairing (dense_weight=0, signal_weight=1.0).

No BGE-M3 embeddings.  Pairs are selected solely by signal-name overlap.
Useful for quantifying the marginal value of semantic embedding.

Usage:
    /home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python \
      experiments/signal_only_ablation/run_signal_only.py \
      --ip aes --specs-dir output/specs_aes --workers 8
"""

import argparse, json, sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rtl_bug_agent.phase2.signal_graph import build_signal_graph
from rtl_bug_agent.phase2.semantic_ag import (
    SemanticAgConfig,
    build_atoms, embed_atoms_cached, pair_atoms,
)


def main():
    parser = argparse.ArgumentParser(description="Signal-only AG pairing ablation")
    parser.add_argument("--ip", default="aes")
    parser.add_argument("--specs-dir", default="output/specs_aes")
    args = parser.parse_args()

    ip = args.ip
    specs_dir = args.specs_dir
    out_dir = Path(__file__).resolve().parent / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    sem_cfg = SemanticAgConfig(
        model_name="BAAI/bge-m3",
        dense_weight=0.0,
        signal_weight=1.0,
        assumption_top_k=5,
        uncertain_top_k=3,
        assumption_min_score=0.01,
        uncertain_min_score_with_signal=0.01,
        uncertain_dense_fallback=1.0,
        exclude_same_spec=True,
    )

    print(f"Building SignalGraph from {specs_dir} ...")
    graph = build_signal_graph(specs_dir)
    print(graph.summary())

    atoms = build_atoms(graph)
    print(f"{len(atoms)} atoms")

    cache_dir = out_dir / ".cache"
    embeddings = embed_atoms_cached(atoms, str(cache_dir), sem_cfg)

    pairing = pair_atoms(atoms, embeddings, sem_cfg)
    meta = pairing.get("metadata", {})

    all_pairs = []
    for item in pairing.get("results", []):
        query = item["query"]
        for match in item.get("matches", []):
            all_pairs.append({
                "q_id": query["atom_id"],
                "q_kind": query["kind"],
                "q_text": query["text"][:150],
                "q_signals": query.get("signals", []),
                "c_id": match["atom_id"],
                "c_text": match["text"][:150],
                "c_signals": match.get("signals", []),
                "score": match["score"],
                "signal": match["signal_relation_score"],
                "rel": match["signal_relation_kind"],
                "pair_type": match["pair_type"],
                "shared": match.get("shared_signals", []),
                "rank": match["rank"],
            })

    out_path = out_dir / f"pairs_signal_only_{ip}.json"
    json.dump({"metadata": meta, "pairs": all_pairs}, open(out_path, "w"),
              indent=2, ensure_ascii=False)

    print(f"\nSaved: {out_path}")
    print(f"  selected pairs: {meta.get('num_selected_pairs', 0)}")
    print(f"  by kind: {meta.get('selected_pairs_by_query_kind', {})}")
    print(f"  by type: {meta.get('selected_pairs_by_pair_type', {})}")
    print(f"  unmatched uncertain: {meta.get('num_unmatched_uncertain', 0)}")


if __name__ == "__main__":
    main()
