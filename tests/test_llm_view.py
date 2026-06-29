from __future__ import annotations

import json

from rtl_bug_agent.phase2.llm_view import finding_for_llm


def test_trace_and_internal_fields_never_reach_llm() -> None:
    finding = {
        "title": "key_state_q not updated with ecc",
        "severity": "HIGH",
        "verdict": "GAP",
        "channels": ["B-AG"],
        "contradiction": "ecc updated, data dropped",
        "involved_signals": ["key_state_q", "key_state_ecc_q"],
        "trace_ref": "F-0001",
        "formal": {
            "status": "PENDING",
            "sva": "assert property (@(posedge clk_i) a |-> b);",
            "clock": "clk_i",
            "reset": "rst_ni",
            "bind_module": "keymgr_ctrl",
            "bind_signals": ["key_state_q"],
        },
        "formal_result": {
            "verdict": "CEX",
            "workdir": "/tmp/leak",
            "log_excerpt": "secret log",
            "counterexample_path": "/tmp/cex.vcd",
            "solver": "z3",
            "depth": 20,
        },
    }
    view = finding_for_llm(finding)
    blob = json.dumps(view, ensure_ascii=False)

    # Gate 1: trace pointer and solver internals must not be in the projection.
    assert "trace_ref" not in view
    assert "trace" not in blob
    assert "workdir" not in blob and "/tmp/leak" not in blob
    assert "log_excerpt" not in blob and "secret log" not in blob
    assert "counterexample_path" not in blob and "cex.vcd" not in blob
    assert "bind_module" not in blob

    # The conclusion + checked property remain visible (that is the evidence).
    assert view["formal_result"]["verdict"] == "CEX"
    assert "assert property" in view["formal_result"]["sva"]


def test_unknown_added_field_is_invisible_by_default() -> None:
    finding = {"title": "x", "verdict": "GAP", "secret_new_field": "should not leak"}
    view = finding_for_llm(finding)
    assert "secret_new_field" not in json.dumps(view)


def test_no_formal_result_means_no_formal_key() -> None:
    finding = {"title": "x", "verdict": "SATISFIED"}
    view = finding_for_llm(finding)
    assert "formal_result" not in view
    assert view["verdict"] == "SATISFIED"


def test_sva_falls_back_to_formal_when_result_lacks_it() -> None:
    finding = {
        "title": "x",
        "verdict": "GAP",
        "formal": {"sva": "assert property (@(posedge clk) p);"},
        "formal_result": {"verdict": "INCONCLUSIVE"},
    }
    view = finding_for_llm(finding)
    assert view["formal_result"]["sva"].startswith("assert property")
