#!/usr/bin/env python3
"""Optimized AG candidate pairing with thresholds and uncertain fallback."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import numpy as np


HERE = Path(__file__).resolve().parent
DEFAULT_ATOMS = HERE / "out/atoms_hmac.jsonl"
DEFAULT_EMB = HERE / "out/embeddings_hmac.npz"
DEFAULT_OUT = HERE / "out/optimized_pairs_hmac.json"


def _load_atoms(path: Path) -> list[dict[str, Any]]:
    atoms: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                atoms.append(json.loads(line))
    return atoms


def _text_signals(atom: dict[str, Any]) -> set[str]:
    text = f"{atom.get('text', '')}\n{atom.get('embedding_text', '')}"
    return {x.strip() for x in re.findall(r"`([^`]+)`", text) if x.strip()}


def _expanded_signals(atom: dict[str, Any]) -> set[str]:
    return set(atom.get("signals", []) or []) | _text_signals(atom)


def _normalize_signal(signal: str) -> str:
    sig = signal.strip()
    sig = re.sub(r"\[[^\]]+\]", "", sig)
    sig = re.sub(r"\.[A-Za-z0-9_]+$", "", sig)
    changed = True
    while changed:
        changed = False
        for prefix in ("mr_",):
            if sig.startswith(prefix):
                sig = sig[len(prefix):]
                changed = True
        for suffix in ("_ctrl", "_raw", "_sel", "_o", "_i", "_q", "_d"):
            if sig.endswith(suffix) and len(sig) > len(suffix):
                sig = sig[: -len(suffix)]
                changed = True
    return sig


def _normalized_map(signals: set[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for sig in signals:
        norm = _normalize_signal(sig)
        if norm:
            out.setdefault(norm, []).append(sig)
    return out


def _field_signal_overlap(query: dict[str, Any], cand: dict[str, Any]) -> float:
    q = set(query.get("signals", []) or [])
    c = set(cand.get("signals", []) or [])
    if not q or not c:
        return 0.0
    return len(q & c) / len(q | c)


def _signal_relation(query: dict[str, Any], cand: dict[str, Any]) -> tuple[float, str, list[str]]:
    q_field = set(query.get("signals", []) or [])
    c_field = set(cand.get("signals", []) or [])
    shared_field = sorted(q_field & c_field)
    if shared_field:
        return _field_signal_overlap(query, cand), "field_overlap", shared_field

    q_norm = _normalized_map(q_field)
    c_norm = _normalized_map(c_field)
    shared_norm_keys = sorted(set(q_norm) & set(c_norm))
    if shared_norm_keys:
        shared_norm = sorted(
            {
                sig
                for key in shared_norm_keys
                for sig in q_norm.get(key, []) + c_norm.get(key, [])
            }
        )
        return 0.6, "normalized_field_overlap", shared_norm

    shared_text = sorted(_expanded_signals(query) & _expanded_signals(cand))
    if shared_text:
        return 0.2, "text_signal_overlap", shared_text

    q_text_norm = _normalized_map(_expanded_signals(query))
    c_text_norm = _normalized_map(_expanded_signals(cand))
    shared_text_norm_keys = sorted(set(q_text_norm) & set(c_text_norm))
    if shared_text_norm_keys:
        shared_text_norm = sorted(
            {
                sig
                for key in shared_text_norm_keys
                for sig in q_text_norm.get(key, []) + c_text_norm.get(key, [])
            }
        )
        return 0.2, "normalized_text_signal_overlap", shared_text_norm

    return 0.0, "none", []


def _pair_type(query: dict[str, Any], signal_relation: float, dense: float) -> str | None:
    if query["kind"] == "assumption":
        if signal_relation <= 0.0:
            return None
        return "normal"

    if query["kind"] == "uncertain":
        if signal_relation > 0.0:
            return "uncertain_with_signal"
        if dense >= 0.82:
            return "uncertain_dense_fallback"
        return None

    return None


def pair(
    atoms: list[dict[str, Any]],
    emb: np.ndarray,
    assumption_top_k: int,
    uncertain_top_k: int,
    assumption_min_score: float,
    uncertain_min_score_with_signal: float,
    uncertain_dense_fallback: float,
    dense_weight: float,
    signal_weight: float,
    exclude_same_spec: bool,
) -> dict[str, Any]:
    id_to_index = {atom["atom_id"]: i for i, atom in enumerate(atoms)}
    queries = [atom for atom in atoms if atom["kind"] in ("assumption", "uncertain")]
    guarantees = [atom for atom in atoms if atom["kind"] == "guarantee"]

    results: list[dict[str, Any]] = []
    selected_pairs: list[dict[str, Any]] = []

    for query in queries:
        qi = id_to_index[query["atom_id"]]
        rows = []
        for cand in guarantees:
            if exclude_same_spec and cand["spec_id"] == query["spec_id"]:
                continue
            dense = float(emb[id_to_index[cand["atom_id"]]] @ emb[qi])
            sig_rel, sig_kind, shared = _signal_relation(query, cand)
            pair_type = _pair_type(query, sig_rel, dense)
            if pair_type is None:
                continue

            score = dense_weight * dense + signal_weight * sig_rel
            if pair_type == "normal" and score < assumption_min_score:
                continue
            if pair_type == "uncertain_with_signal" and score < uncertain_min_score_with_signal:
                continue
            if pair_type == "uncertain_dense_fallback" and dense < uncertain_dense_fallback:
                continue

            rows.append(
                {
                    "score": float(score),
                    "dense_score": dense,
                    "signal_relation_score": float(sig_rel),
                    "signal_relation_kind": sig_kind,
                    "shared_signals": shared,
                    "pair_type": pair_type,
                    "atom_id": cand["atom_id"],
                    "spec_id": cand["spec_id"],
                    "kind": cand["kind"],
                    "text": cand["text"],
                    "signals": cand.get("signals", []),
                    "source_refs": cand.get("source_refs", []),
                }
            )

        rows.sort(key=lambda item: item["score"], reverse=True)
        limit = assumption_top_k if query["kind"] == "assumption" else uncertain_top_k
        kept = rows[:limit]
        for rank, row in enumerate(kept, start=1):
            row["rank"] = rank
            selected_pairs.append({"query_atom_id": query["atom_id"], **row})

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
                "matches": kept,
                "num_candidates_after_filter": len(rows),
            }
        )

    by_query_kind: dict[str, int] = {}
    by_pair_type: dict[str, int] = {}
    for item in results:
        by_query_kind[item["query"]["kind"]] = by_query_kind.get(item["query"]["kind"], 0) + len(item["matches"])
        for match in item["matches"]:
            by_pair_type[match["pair_type"]] = by_pair_type.get(match["pair_type"], 0) + 1

    return {
        "metadata": {
            "method": "optimized_dense_signal_relation",
            "assumption_top_k": assumption_top_k,
            "uncertain_top_k": uncertain_top_k,
            "assumption_min_score": assumption_min_score,
            "uncertain_min_score_with_signal": uncertain_min_score_with_signal,
            "uncertain_dense_fallback": uncertain_dense_fallback,
            "dense_weight": dense_weight,
            "signal_weight": signal_weight,
            "exclude_same_spec": exclude_same_spec,
            "num_atoms": len(atoms),
            "num_queries": len(queries),
            "num_guarantees": len(guarantees),
            "num_selected_pairs": len(selected_pairs),
            "selected_pairs_by_query_kind": by_query_kind,
            "selected_pairs_by_pair_type": by_pair_type,
        },
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atoms", type=Path, default=DEFAULT_ATOMS)
    parser.add_argument("--embeddings", type=Path, default=DEFAULT_EMB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--assumption-top-k", type=int, default=5)
    parser.add_argument("--uncertain-top-k", type=int, default=3)
    parser.add_argument("--assumption-min-score", type=float, default=0.66)
    parser.add_argument("--uncertain-min-score-with-signal", type=float, default=0.66)
    parser.add_argument("--uncertain-dense-fallback", type=float, default=0.82)
    parser.add_argument("--dense-weight", type=float, default=0.8)
    parser.add_argument("--signal-weight", type=float, default=0.2)
    parser.add_argument("--include-same-spec", action="store_true")
    args = parser.parse_args()

    total = args.dense_weight + args.signal_weight
    if total <= 0:
        raise SystemExit("dense-weight + signal-weight must be positive")

    atoms = _load_atoms(args.atoms)
    emb = np.load(args.embeddings, allow_pickle=False)["embeddings"]
    output = pair(
        atoms,
        emb,
        args.assumption_top_k,
        args.uncertain_top_k,
        args.assumption_min_score,
        args.uncertain_min_score_with_signal,
        args.uncertain_dense_fallback,
        args.dense_weight / total,
        args.signal_weight / total,
        not args.include_same_spec,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["metadata"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
