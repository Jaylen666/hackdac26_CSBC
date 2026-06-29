"""
Test Phase 3 integration with formal_result field.

Verifies that findings with formal_result are correctly projected through
finding_for_llm() and that the Phase 3 prompt receives formal evidence.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rtl_bug_agent.phase2.llm_view import finding_for_llm
from rtl_bug_agent.phase2.phase3 import verify_finding


def test_finding_for_llm_includes_formal_result():
    """Gate 1: formal_result.{verdict, sva} should be visible to Phase 3."""
    finding = {
        "title": "Test bug",
        "severity": "HIGH",
        "verdict": "BUG",
        "channels": ["A", "B"],
        "contradiction": "Signal X driven incorrectly",
        "involved_signals": ["sig_x"],
        "formal_result": {
            "verdict": "FAIL",
            "sva": "assert property (@(posedge clk) sig_x == 0);",
            "status": "counterexample",
            "engine": "smtbmc z3",
            "duration_s": 2.3,
            "trace_file": "/tmp/cex.vcd",
        },
        "formal": {
            "sva": "assert property (@(posedge clk) sig_x == 0);",
            "status": "PENDING",
            "clock": "clk",
            "bind_module": "top",
        },
        "trace_ref": "trace_12345.jsonl#67",
    }

    projected = finding_for_llm(finding)

    # Should include allowed finding fields
    assert projected["title"] == "Test bug"
    assert projected["severity"] == "HIGH"
    assert projected["verdict"] == "BUG"
    assert projected["channels"] == ["A", "B"]
    assert projected["contradiction"] == "Signal X driven incorrectly"
    assert projected["involved_signals"] == ["sig_x"]

    # Should include formal_result with only {verdict, sva}
    assert "formal_result" in projected
    assert projected["formal_result"]["verdict"] == "FAIL"
    assert projected["formal_result"]["sva"] == "assert property (@(posedge clk) sig_x == 0);"

    # Should NOT include formal_result internals
    assert "status" not in projected["formal_result"]
    assert "engine" not in projected["formal_result"]
    assert "duration_s" not in projected["formal_result"]
    assert "trace_file" not in projected["formal_result"]

    # Should NOT include formal internals
    assert "formal" not in projected

    # Should NOT include trace_ref
    assert "trace_ref" not in projected


def test_finding_for_llm_formal_result_fallback_sva():
    """If formal_result lacks sva, fall back to formal.sva."""
    finding = {
        "title": "Test bug",
        "severity": "HIGH",
        "verdict": "BUG",
        "channels": ["A"],
        "contradiction": "X",
        "involved_signals": ["x"],
        "formal_result": {
            "verdict": "PASS",
            # no sva here
        },
        "formal": {
            "sva": "assert property (@(posedge clk) x != 0);",
            "status": "PENDING",
        },
    }

    projected = finding_for_llm(finding)

    assert projected["formal_result"]["verdict"] == "PASS"
    # Should fall back to formal.sva
    assert projected["formal_result"]["sva"] == "assert property (@(posedge clk) x != 0);"


def test_finding_for_llm_no_formal_result():
    """Findings without formal_result should not have that field in projection."""
    finding = {
        "title": "Test bug",
        "severity": "HIGH",
        "verdict": "BUG",
        "channels": ["A"],
        "contradiction": "X",
        "involved_signals": ["x"],
    }

    projected = finding_for_llm(finding)

    assert "formal_result" not in projected
    assert projected["title"] == "Test bug"


def test_phase3_payload_includes_formal_result(tmp_path):
    """Phase 3 verify_finding should pass formal_result to the LLM."""
    # Create a minimal RTL source file
    rtl_file = tmp_path / "test.sv"
    rtl_file.write_text("module top; logic x; endmodule\n")

    # Mock SignalGraph
    graph = MagicMock()
    graph.spec_meta = {}
    graph.signals = {}

    # Mock LLM client - capture what payload it receives
    captured_payload = {}

    def mock_chat(messages, max_tokens):
        nonlocal captured_payload
        # Extract the user message (JSON payload)
        user_msg = messages[1]["content"]
        captured_payload = json.loads(user_msg)
        # Return a valid Phase 3 response
        return json.dumps({
            "verdict": "CONFIRMED",
            "confidence": 0.9,
            "summary": "Bug confirmed by formal evidence",
            "root_cause": "Signal x incorrectly driven",
            "trigger_condition": "Always",
            "security_impact": "None",
            "software_visible": False,
            "reasoning": "formal_result.verdict=FAIL shows counterexample",
            "additional_findings": [],
            "formal_alignment": "FAIL verdict aligns with CONFIRMED",
        })

    client = MagicMock()
    client.chat = mock_chat

    finding = {
        "title": "Test bug",
        "severity": "HIGH",
        "verdict": "BUG",
        "channels": ["A"],
        "contradiction": "Signal x driven incorrectly",
        "involved_signals": ["x"],
        "involved_specs": [],
        "formal_result": {
            "verdict": "FAIL",
            "sva": "assert property (@(posedge clk) x == 0);",
            "status": "counterexample",
            "engine": "smtbmc z3",
        },
        "trace_ref": "should_not_appear_in_payload",
    }

    result = verify_finding(finding, graph, client)

    # Verify the LLM received formal_result
    assert "finding" in captured_payload
    finding_view = captured_payload["finding"]
    assert "formal_result" in finding_view
    assert finding_view["formal_result"]["verdict"] == "FAIL"
    assert finding_view["formal_result"]["sva"] == "assert property (@(posedge clk) x == 0);"

    # Verify trace_ref and formal internals did NOT leak
    assert "trace_ref" not in finding_view
    assert "status" not in finding_view.get("formal_result", {})
    assert "engine" not in finding_view.get("formal_result", {})

    # Verify Phase 3 result includes formal_alignment
    assert result["verdict"] == "CONFIRMED"
    assert result["formal_alignment"] == "FAIL verdict aligns with CONFIRMED"


def test_phase3_prompt_mentions_formal_result():
    """Phase 3 prompt template should mention formal_result and its semantics."""
    from rtl_bug_agent.phase2.phase3 import DEFAULT_PROMPT

    prompt_text = Path(DEFAULT_PROMPT).read_text(encoding="utf-8")

    # Check for key formal evidence concepts
    assert "formal_result" in prompt_text.lower() or "formal verification" in prompt_text.lower()
    assert "PASS" in prompt_text
    assert "FAIL" in prompt_text
    assert "counterexample" in prompt_text.lower()
    assert "formal_alignment" in prompt_text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
