#!/usr/bin/env python3
"""Summarize optimized HMAC/AES pairing results for known bug neighborhoods."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent


BUG_QUERIES = {
    "hmac": [
        {
            "case": "HMAC-009 cfg_block key rewrite",
            "query_contains": "cfg_block",
            "query_spec": "hmac__always_comb__update_secret_key__001",
            "target_spec": "hmac__always_ff__line_365__001",
        },
        {
            "case": "HMAC err_code invalid_config neighborhood",
            "query_contains": "invalid_config",
            "query_spec": "hmac__continuous_region__declarations_or_instances__005",
            "target_spec": "hmac__always_comb__line_846__001",
        },
        {
            "case": "HMAC invalid_config_atstart software-error chain",
            "query_contains": "invalid_config_atstart",
            "query_spec": "hmac__continuous_region__declarations_or_instances__010",
            "target_spec": "hmac__continuous_region__declarations_or_instances__005",
        },
    ],
    "aes": [
        {
            "case": "AES N-001 key_words selector producer-consumer",
            "query_contains": "key_words_sel",
            "query_spec": "aes_cipher_core__generate_for__gen_shares_round_key__001",
            "target_spec": "aes_cipher_control__always_comb__combine_sparse_signals__001",
        },
        {
            "case": "AES selector buffer check mux_sel_err",
            "query_contains": "mux_sel_err",
            "query_spec": "aes_cipher_core__continuous_region__declarations_or_instances__010",
            "target_spec": "aes_cipher_control__continuous_region__batched_small_assigns__001",
        },
        {
            "case": "AES key_full_clear mux neighborhood",
            "query_contains": "key_full_sel",
            "query_spec": "aes_cipher_core__always_comb__key_full_mux__001",
            "target_spec": "aes_cipher_control__always_comb__combine_sparse_signals__001",
        },
        {
            "case": "AES state_sel mux neighborhood",
            "query_contains": "state_sel",
            "query_spec": "aes_cipher_core__always_comb__state_mux__001",
            "target_spec": "aes_cipher_control__always_comb__combine_sparse_signals__001",
        },
    ],
}


def _load(module: str) -> dict[str, Any]:
    return json.loads((HERE / f"out/optimized_pairs_{module}.json").read_text(encoding="utf-8"))


def _query_matches(query: dict[str, Any], spec: str, text: str) -> bool:
    haystack = " ".join(
        [
            query.get("atom_id", ""),
            query.get("spec_id", ""),
            query.get("text", ""),
            " ".join(query.get("signals", []) or []),
        ]
    ).lower()
    return query.get("spec_id") == spec and text.lower() in haystack


def _summarize_case(data: dict[str, Any], case: dict[str, str]) -> dict[str, Any]:
    rows = [
        row
        for row in data["results"]
        if _query_matches(row["query"], case["query_spec"], case["query_contains"])
    ]
    if not rows:
        return {**case, "found_query": False}

    best = None
    for row in rows:
        target_matches = [
            match for match in row["matches"] if match["spec_id"] == case["target_spec"]
        ]
        if not target_matches:
            candidate = {
                "found_query": True,
                "query_atom_id": row["query"]["atom_id"],
                "query_kind": row["query"]["kind"],
                "target_rank": None,
                "matches": row["matches"][:5],
            }
        else:
            match = target_matches[0]
            candidate = {
                "found_query": True,
                "query_atom_id": row["query"]["atom_id"],
                "query_kind": row["query"]["kind"],
                "target_rank": match["rank"],
                "target_score": match["score"],
                "target_dense_score": match["dense_score"],
                "target_signal_relation_score": match["signal_relation_score"],
                "target_signal_relation_kind": match["signal_relation_kind"],
                "target_shared_signals": match["shared_signals"],
                "target_atom_id": match["atom_id"],
                "target_text": match["text"],
                "matches": row["matches"][:5],
            }

        if best is None:
            best = candidate
        elif candidate.get("target_rank") is not None and (
            best.get("target_rank") is None or candidate["target_rank"] < best["target_rank"]
        ):
            best = candidate

    return {**case, **(best or {})}


def main() -> None:
    output: dict[str, Any] = {}
    for module in ["hmac", "aes"]:
        data = _load(module)
        output[module] = {
            "metadata": data["metadata"],
            "bug_neighborhoods": [
                _summarize_case(data, case) for case in BUG_QUERIES[module]
            ],
        }

    out = HERE / "out/optimized_summary.json"
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    for module, section in output.items():
        print(f"\n[{module}]")
        print(json.dumps(section["metadata"], ensure_ascii=False, sort_keys=True))
        for case in section["bug_neighborhoods"]:
            print(
                f"- {case['case']}: query={case.get('query_atom_id')} "
                f"rank={case.get('target_rank')} score={case.get('target_score')} "
                f"sig={case.get('target_signal_relation_kind')} shared={case.get('target_shared_signals')}"
            )


if __name__ == "__main__":
    main()
