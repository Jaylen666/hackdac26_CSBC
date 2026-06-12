#!/usr/bin/env python3
"""Analyze semantic overlap between uncertain points and A/G atoms."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np


HERE = Path(__file__).resolve().parent
DEFAULT_ATOMS = HERE / "out/atoms_hmac.jsonl"
DEFAULT_EMB = HERE / "out/embeddings_hmac.npz"
DEFAULT_OUT = HERE / "out/uncertain_overlap_hmac.json"


def _load_atoms(path: Path) -> list[dict[str, Any]]:
    atoms: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                atoms.append(json.loads(line))
    return atoms


def _bucket(values: list[float]) -> dict[str, int]:
    buckets = {
        "lt_0.70": 0,
        "0.70_0.80": 0,
        "0.80_0.85": 0,
        "0.85_0.90": 0,
        "gte_0.90": 0,
    }
    for value in values:
        if value < 0.70:
            buckets["lt_0.70"] += 1
        elif value < 0.80:
            buckets["0.70_0.80"] += 1
        elif value < 0.85:
            buckets["0.80_0.85"] += 1
        elif value < 0.90:
            buckets["0.85_0.90"] += 1
        else:
            buckets["gte_0.90"] += 1
    return buckets


def _top_match(
    atom: dict[str, Any],
    atom_index: int,
    target_atoms: list[dict[str, Any]],
    target_indices: list[int],
    emb: np.ndarray,
) -> dict[str, Any] | None:
    if not target_atoms:
        return None
    sims = emb[target_indices] @ emb[atom_index]
    order = np.argsort(-sims)
    for pos in order:
        target = target_atoms[int(pos)]
        if target["spec_id"] == atom["spec_id"]:
            continue
        return {
            "score": float(sims[int(pos)]),
            "atom_id": target["atom_id"],
            "spec_id": target["spec_id"],
            "kind": target["kind"],
            "text": target["text"],
            "signals": target.get("signals", []),
        }
    return None


def analyze(atoms: list[dict[str, Any]], emb: np.ndarray) -> dict[str, Any]:
    id_to_index = {atom["atom_id"]: i for i, atom in enumerate(atoms)}
    uncertain = [atom for atom in atoms if atom["kind"] == "uncertain"]
    assumptions = [atom for atom in atoms if atom["kind"] == "assumption"]
    guarantees = [atom for atom in atoms if atom["kind"] == "guarantee"]
    a_indices = [id_to_index[a["atom_id"]] for a in assumptions]
    g_indices = [id_to_index[g["atom_id"]] for g in guarantees]

    rows = []
    max_a_scores: list[float] = []
    max_g_scores: list[float] = []
    for atom in uncertain:
        idx = id_to_index[atom["atom_id"]]
        best_a = _top_match(atom, idx, assumptions, a_indices, emb)
        best_g = _top_match(atom, idx, guarantees, g_indices, emb)
        max_a = best_a["score"] if best_a else 0.0
        max_g = best_g["score"] if best_g else 0.0
        max_a_scores.append(max_a)
        max_g_scores.append(max_g)
        rows.append(
            {
                "atom_id": atom["atom_id"],
                "spec_id": atom["spec_id"],
                "text": atom["text"],
                "signals": atom.get("signals", []),
                "best_assumption": best_a,
                "best_guarantee": best_g,
                "likely_redundant_with_assumption": max_a >= 0.85,
                "likely_pairable_with_guarantee": max_g >= 0.80,
            }
        )

    def mean(values: list[float]) -> float:
        return float(np.mean(values)) if values else 0.0

    summary = {
        "num_uncertain": len(uncertain),
        "num_assumptions": len(assumptions),
        "num_guarantees": len(guarantees),
        "max_assumption_similarity_mean": mean(max_a_scores),
        "max_guarantee_similarity_mean": mean(max_g_scores),
        "uncertain_redundant_with_assumption_gte_0.85": sum(1 for x in max_a_scores if x >= 0.85),
        "uncertain_pairable_with_guarantee_gte_0.80": sum(1 for x in max_g_scores if x >= 0.80),
        "max_assumption_similarity_buckets": _bucket(max_a_scores),
        "max_guarantee_similarity_buckets": _bucket(max_g_scores),
    }
    return {"summary": summary, "uncertain_points": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atoms", type=Path, default=DEFAULT_ATOMS)
    parser.add_argument("--embeddings", type=Path, default=DEFAULT_EMB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    atoms = _load_atoms(args.atoms)
    emb = np.load(args.embeddings, allow_pickle=False)["embeddings"]
    output = analyze(atoms, emb)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["summary"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
