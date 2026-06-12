#!/usr/bin/env python3
"""Retrieve guarantee candidates for HMAC assumption-like atoms."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np


HERE = Path(__file__).resolve().parent
DEFAULT_ATOMS = HERE / "out/atoms_hmac.jsonl"
DEFAULT_EMB = HERE / "out/embeddings_hmac.npz"
DEFAULT_OUT = HERE / "out/retrieval_hmac.json"


def _load_atoms(path: Path) -> list[dict[str, Any]]:
    atoms: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                atoms.append(json.loads(line))
    return atoms


def _signal_overlap(query: dict[str, Any], cand: dict[str, Any]) -> float:
    q = set(query.get("signals", []) or [])
    c = set(cand.get("signals", []) or [])
    if not q or not c:
        return 0.0
    return len(q & c) / len(q | c)


def retrieve(
    atoms: list[dict[str, Any]],
    emb: np.ndarray,
    top_k: int,
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

    results = []
    for query in queries:
        qi = id_to_index[query["atom_id"]]
        candidate_guarantees = [
            g
            for g in guarantees
            if not exclude_same_spec or g["spec_id"] != query["spec_id"]
        ]
        g_indices = [id_to_index[g["atom_id"]] for g in candidate_guarantees]
        sims = emb[g_indices] @ emb[qi]
        order = np.argsort(-sims)[:top_k]

        matches = []
        for rank, pos in enumerate(order, start=1):
            cand = candidate_guarantees[int(pos)]
            matches.append(
                {
                    "rank": rank,
                    "score": float(sims[int(pos)]),
                    "signal_overlap": _signal_overlap(query, cand),
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
            "top_k": top_k,
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
    parser.add_argument("--no-uncertain", action="store_true")
    parser.add_argument(
        "--include-same-spec",
        action="store_true",
        help="Keep guarantees from the same spec as the query. Default excludes them.",
    )
    args = parser.parse_args()

    atoms = _load_atoms(args.atoms)
    data = np.load(args.embeddings, allow_pickle=False)
    emb = data["embeddings"]
    if emb.shape[0] != len(atoms):
        raise SystemExit(f"Embedding/atom count mismatch: {emb.shape[0]} vs {len(atoms)}")

    output = retrieve(atoms, emb, args.top_k, not args.no_uncertain, not args.include_same_spec)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote retrieval results to {args.out}")
    print(json.dumps(output["metadata"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
