from __future__ import annotations

from rtl_bug_agent.phase2.fusion import fuse


def test_fuse_preserves_formal_fields() -> None:
    findings = {
        "B": [
            {
                "title": "finding a",
                "verdict": "GAP",
                "signal": "sig_a",
                "involved_signals": ["sig_a"],
                "involved_specs": ["spec_a"],
                "formal_verdict": "PARTIAL",
                "formal_confidence": 0.7,
                "formal_draft": {"assertion": "assert property (@(posedge clk_i) a);"},
            }
        ],
        "C": [
            {
                "title": "finding a",
                "verdict": "UNCERTAIN",
                "signal": "sig_a",
                "involved_signals": ["sig_a"],
                "involved_specs": ["spec_b"],
                "formal_verdict": "DIRECT",
                "formal_confidence": 0.9,
            }
        ],
    }

    merged = fuse(findings, {"sig_a"})
    assert len(merged) == 1
    item = merged[0].to_dict()
    assert item["formal_verdict"] == "DIRECT"
    assert item["formal_confidence"] == 0.9
    assert item["formal_draft"]["assertion"].startswith("assert property")


def test_fuse_no_cluster_keeps_findings_separate() -> None:
    """cluster=False must keep every finding as its own entry, even when they
    share a signal name — we accept duplicates rather than risk dropping a
    real bug behind a merged representative (keymgr N-003 audit)."""
    findings = {
        "B": [
            {
                "title": "finding a",
                "verdict": "CONTRADICTION",
                "signal": "key_state_q",
                "involved_signals": ["key_state_q"],
                "involved_specs": ["spec_a"],
            }
        ],
        "C": [
            {
                "title": "finding b",
                "verdict": "GAP",
                "signal": "key_state_q",
                "involved_signals": ["key_state_q"],
                "involved_specs": ["spec_b"],
            }
        ],
    }

    merged = fuse(findings, {"key_state_q"}, cluster=False)
    # Both findings survive (default clustering would have merged them into 1).
    assert len(merged) == 2
    titles = {m.title for m in merged}
    assert titles == {"finding a", "finding b"}
    # Sanity: the same input WITH clustering collapses to a single entry.
    assert len(fuse(findings, {"key_state_q"}, cluster=True)) == 1
