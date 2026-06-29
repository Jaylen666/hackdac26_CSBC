from __future__ import annotations

from rtl_bug_agent.phase2.semantic_ag import (
    SemanticAgConfig,
    _consequent_conflict,
    _formal_relation,
    _normalised_weights,
)


def _mk(scope, ant, cons, sigs, shape="comb"):
    return {
        "formal_sketch": {
            "scope": scope,
            "clock": "clk_i",
            "temporal_shape": shape,
            "antecedent": ant,
            "consequent": cons,
            "signals": sigs,
        }
    }


def test_conflict_not_added_to_score_but_reported_in_diagnostics() -> None:
    # Same antecedent context, conflicting consequent on iv_we.
    q = _mk("iv", "iv_sel == IV_CTR", "iv_we == 0", ["iv_we", "iv_sel"])
    c = _mk("iv", "iv_sel == IV_CTR", "iv_we == 1", ["iv_we", "iv_sel"])

    score, kind, shared, diag = _formal_relation(q, c)

    # Conflict surfaces as diagnostics, not as a ranking boost.
    assert diag.get("conflict_signals") == ["iv_we"]
    # kind is never "conflict" anymore (pairing stays purely similarity-based).
    assert kind in ("aligned", "weak")
    # The +0.25 conflict bonus is gone: score must equal the pure similarity sum
    # with no conflict term. Recompute expected similarity:
    #   scope match 0.18 + clock 0.16 + shape 0.14 + signals(2 shared) 0.10 = 0.58
    assert abs(score - 0.58) < 1e-9


def test_no_conflict_when_consequents_agree() -> None:
    q = _mk("iv", "iv_sel == IV_CTR", "iv_we == 0", ["iv_we"])
    c = _mk("iv", "iv_sel == IV_CTR", "iv_we == 0", ["iv_we"])
    score, kind, shared, diag = _formal_relation(q, c)
    assert "conflict_signals" not in diag


def test_conflict_requires_shared_antecedent_context() -> None:
    # Conflicting values but totally unrelated antecedents → not a conflict.
    q = _mk("iv", "mode == A", "iv_we == 0", ["iv_we"])
    c = _mk("iv", "totally_different == Z", "iv_we == 1", ["iv_we"])
    assert _consequent_conflict(q["formal_sketch"], c["formal_sketch"]) == []


def test_default_formal_weight_is_zero() -> None:
    # v2.0 §3.2: pairing purely semantic by default.
    cfg = SemanticAgConfig()
    assert cfg.formal_weight == 0.0
    dense_w, signal_w, formal_w = _normalised_weights(cfg)
    assert formal_w == 0.0
    assert abs(dense_w - 0.8) < 1e-9 and abs(signal_w - 0.2) < 1e-9


def test_formal_weight_zero_means_score_independent_of_formal_rel() -> None:
    # With formal_weight 0, two candidates differing ONLY in formal similarity
    # must rank identically on the formal axis (formal_rel * 0 == 0).
    cfg = SemanticAgConfig()
    _, _, formal_w = _normalised_weights(cfg)
    high_formal_contribution = 0.58 * formal_w
    assert high_formal_contribution == 0.0


def test_empty_sketches_return_neutral() -> None:
    score, kind, shared, diag = _formal_relation({}, {})
    assert score == 0.0 and kind == "none" and shared == [] and diag == {}


def test_unmatched_query_candidates_surfaces_assumptions() -> None:
    from rtl_bug_agent.phase2.semantic_ag import (
        unmatched_query_candidates,
        unmatched_uncertain_candidates,
    )
    pairing = {"results": [
        {"query": {"kind": "uncertain", "spec_id": "C1", "text": "u", "atom_id": "U1",
                   "signals": ["a"]}, "matches": []},
        {"query": {"kind": "assumption", "spec_id": "C2", "text": "asm", "atom_id": "A1",
                   "signals": ["b"], "formal_sketch": {"formalizability": "direct"}},
         "matches": []},
        {"query": {"kind": "assumption", "spec_id": "C3", "text": "paired", "atom_id": "A2"},
         "matches": [{"atom_id": "G1"}]},  # paired → excluded
    ]}
    # Default: uncertain only (back-compat).
    only_u = unmatched_uncertain_candidates(pairing)
    assert [c["atom_id"] for c in only_u] == ["U1"]
    # Extended: uncertain + unpaired assumptions, paired one excluded.
    both = unmatched_query_candidates(pairing, kinds=("uncertain", "assumption"))
    ids = {c["atom_id"] for c in both}
    assert ids == {"U1", "A1"}
    # formal_sketch carried through for the gate.
    a1 = next(c for c in both if c["atom_id"] == "A1")
    assert a1["formal_sketch"]["formalizability"] == "direct"
