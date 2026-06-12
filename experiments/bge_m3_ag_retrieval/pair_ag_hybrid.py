#!/usr/bin/env python3
"""Hybrid semantic/signal AG candidate pairing for HMAC specs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np


HERE = Path(__file__).resolve().parent
DEFAULT_ATOMS = HERE / "out/atoms_hmac.jsonl"
DEFAULT_EMB = HERE / "out/embeddings_hmac.npz"
DEFAULT_OUT = HERE / "out/hybrid_pairs_hmac.json"


def _load_atoms(path: Path) -> list[dict[str, Any]]:
    atoms: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                atoms.append(json.loads(line))
    return atoms


def _field_signal_overlap(query: dict[str, Any], cand: dict[str, Any]) -> float:
    q = set(query.get("signals", []) or [])
    c = set(cand.get("signals", []) or [])
    if not q or not c:
        return 0.0
    return len(q & c) / len(q | c)


def _shared_field_signals(query: dict[str, Any], cand: dict[str, Any]) -> list[str]:
    return sorted(set(query.get("signals", []) or []) & set(cand.get("signals", []) or []))


def pair(
    atoms: list[dict[str, Any]],
    emb: np.ndarray,
    top_k: int,
    dense_weight: float,
    signal_weight: float,
    include_uncertain: bool,
    exclude_same_spec: bool,
) -> dict[str, Any]:
    id_to_index = {atom["atom_id"]: i for i, atom in enumerate(atoms)}
    queries = [
        atom
        for atom in atoms
        if atom["kind"] == "assumption" or (include_uncertain and atom["kind"] == "uncertain")
    ]
    guarantees = [atom for atom in atoms if atom["kind"] == "guarantee"]

    results: list[dict[str, Any]] = []
    for query in queries:
        qi = id_to_index[query["atom_id"]]
        rows = []
        for cand in guarantees:
            if exclude_same_spec and cand["spec_id"] == query["spec_id"]:
                continue
            dense = float(emb[id_to_index[cand["atom_id"]]] @ emb[qi])
            sig = _field_signal_overlap(query, cand)
            score = dense_weight * dense + signal_weight * sig
            rows.append((score, dense, sig, cand))
        rows.sort(key=lambda item: item[0], reverse=True)

        matches = []
        for rank, (score, dense, sig, cand) in enumerate(rows[:top_k], start=1):
            matches.append(
                {
                    "rank": rank,
                    "score": float(score),
                    "dense_score": float(dense),
                    "field_signal_overlap": float(sig),
                    "shared_field_signals": _shared_field_signals(query, cand),
                    "atom_id": cand["atom_id"],
                    "spec_id": cand["spec_id"],
                    "kind": cand["kind"],
                    "text": cand["text"],
                    "signals": cand.get("signals", []),
                    "source_refs": cand.get("source_refs", []),
                }
            )

        results.append(
            {
                "query": {
                    "atom_id": query["atom_id"],
                    "spec_id": query["spec_id"],
                    "kind": query["kind"],
                    "text": query["text"],
                    "signals": query.get("signals", []),
                    "source_refs": query.get("source_refs", []),
                },
                "matches": matches,
            }
        )

    return {
        "metadata": {
            "method": "hybrid_dense_field_signal",
            "top_k": top_k,
            "dense_weight": dense_weight,
            "signal_weight": signal_weight,
            "include_uncertain": include_uncertain,
            "exclude_same_spec": exclude_same_spec,
            "num_atoms": len(atoms),
            "num_queries": len(queries),
            "num_guarantees": len(guarantees),
        },
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atoms", type=Path, default=DEFAULT_ATOMS)
    parser.add_argument("--embeddings", type=Path, default=DEFAULT_EMB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--dense-weight", type=float, default=0.8)
    parser.add_argument("--signal-weight", type=float, default=0.2)
    parser.add_argument("--no-uncertain", action="store_true")
    parser.add_argument("--include-same-spec", action="store_true")
    args = parser.parse_args()

    total_weight = args.dense_weight + args.signal_weight
    if total_weight <= 0:
        raise SystemExit("dense-weight + signal-weight must be positive")

    atoms = _load_atoms(args.atoms)
    emb = np.load(args.embeddings, allow_pickle=False)["embeddings"]
    output = pair(
        atoms,
        emb,
        args.top_k,
        args.dense_weight / total_weight,
        args.signal_weight / total_weight,
        not args.no_uncertain,
        not args.include_same_spec,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["metadata"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
