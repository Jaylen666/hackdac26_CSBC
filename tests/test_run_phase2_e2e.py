from __future__ import annotations

from scripts.run_phase2_e2e import _select_channel_f_candidates


def test_select_channel_f_candidates_semantic_includes_unmatched_assumptions() -> None:
    semantic_pairing = {
        "results": [
            {
                "query": {
                    "kind": "uncertain",
                    "spec_id": "C1",
                    "text": "u",
                    "atom_id": "U1",
                    "signals": ["a"],
                },
                "matches": [],
            },
            {
                "query": {
                    "kind": "assumption",
                    "spec_id": "C2",
                    "text": "a",
                    "atom_id": "A1",
                    "signals": ["b"],
                    "formal_sketch": {"formalizability": "direct"},
                },
                "matches": [],
            },
        ]
    }
    ph3_cands = [{"chunk_id": "legacy-only", "uncertain_text": "legacy"}]

    out = _select_channel_f_candidates(
        ag_pairing_mode="semantic",
        semantic_pairing=semantic_pairing,
        ph3_cands=ph3_cands,
    )

    assert {item["atom_id"] for item in out} == {"U1", "A1"}


def test_select_channel_f_candidates_shadow_stays_on_legacy_uncertain() -> None:
    semantic_pairing = {
        "results": [
            {
                "query": {
                    "kind": "assumption",
                    "spec_id": "C2",
                    "text": "a",
                    "atom_id": "A1",
                    "signals": ["b"],
                    "formal_sketch": {"formalizability": "direct"},
                },
                "matches": [],
            },
        ]
    }
    ph3_cands = [{"chunk_id": "U-legacy", "uncertain_text": "legacy uncertain"}]

    out = _select_channel_f_candidates(
        ag_pairing_mode="shadow",
        semantic_pairing=semantic_pairing,
        ph3_cands=ph3_cands,
    )

    assert out == ph3_cands
