from __future__ import annotations

from rtl_bug_agent.phase2.formal_sketch import (
    attach_formal_sketches,
    render_property_assertion,
    summarise_formal_context,
)


def test_attach_formal_sketches_handles_old_and_new_items() -> None:
    spec = {
        "module": "demo_mod",
        "assumptions": [{"claim": "when `a` then `b`", "signals": ["a", "b"]}],
        "guarantees": ["`c` shall stay low"],
        "uncertain_points": [{"claim": "could be late", "signals": []}],
    }
    out = attach_formal_sketches(spec)

    assert out["assumptions"][0]["formal_sketch"]["scope"] == "demo_mod"
    assert out["assumptions"][0]["formal_sketch"]["signals"] == ["a", "b"]
    assert out["guarantees"][0]["formal_sketch"]["scope"] == "demo_mod"
    assert out["uncertain_points"][0]["formal_sketch"]["formalizability"] in {"none", "partial"}


def test_render_property_assertion_uses_clock_and_reset() -> None:
    sketch = {
        "clock": "clk_i",
        "reset": "rst_ni",
        "temporal_shape": "next_cycle",
        "antecedent": "a",
        "consequent": "b",
    }
    assertion = render_property_assertion(sketch)
    assert "@(posedge clk_i)" in assertion
    assert "disable iff (!rst_ni)" in assertion
    assert "|=>" in assertion


def test_summarise_formal_context_prefers_direct() -> None:
    summary = summarise_formal_context(
        [
            {"formal_sketch": {"formalizability": "partial", "confidence": 0.6}},
            {"formal_sketch": {"formalizability": "direct", "confidence": 0.8}},
        ]
    )
    assert summary == {"formal_verdict": "DIRECT", "formal_confidence": 0.8}
