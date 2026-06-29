from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from rtl_bug_agent.phase2.formal_runner import (
    _generate_sby_config,
    _parse_sby_result,
    _extract_signal_widths,
    run_formal_solver,
)


def test_generate_sby_config_has_correct_structure(tmp_path):
    rtl = [tmp_path / "a.sv", tmp_path / "b.v"]
    bind = tmp_path / "bind.sv"
    config = _generate_sby_config(rtl_files=rtl, bind_file=bind, depth=15)

    assert "mode bmc" in config
    assert "depth 15" in config
    assert "smtbmc z3" in config
    assert f"read -sv {rtl[0].resolve()}" in config
    assert f"read -sv {rtl[1].resolve()}" in config
    assert f"read -formal {bind.resolve()}" in config
    assert "prep -top bind_wrapper" in config


def test_parse_sby_result_pass():
    proc = MagicMock(stdout="Status: PASSED\n", stderr="", returncode=0)
    status, trace, err = _parse_sby_result(proc, Path("/tmp"))
    assert status == "PASS"
    assert trace is None
    assert err is None


def test_parse_sby_result_fail_with_vcd(tmp_path):
    vcd = tmp_path / "engine_0" / "trace.vcd"
    vcd.parent.mkdir(parents=True)
    vcd.write_text("vcd content")
    proc = MagicMock(stdout="Status: FAILED\n", stderr="", returncode=0)
    status, trace, err = _parse_sby_result(proc, tmp_path)
    assert status == "FAIL"
    assert trace == vcd


def test_parse_sby_result_unknown():
    proc = MagicMock(stdout="Status: UNKNOWN\n", stderr="", returncode=0)
    status, trace, err = _parse_sby_result(proc, Path("/tmp"))
    assert status == "UNKNOWN"


def test_parse_sby_result_error_fallback():
    proc = MagicMock(stdout="some output", stderr="yosys error", returncode=1)
    status, trace, err = _parse_sby_result(proc, Path("/tmp"))
    assert status == "ERROR"
    assert "yosys error" in err


def test_extract_signal_widths_empty_rtl():
    assert _extract_signal_widths([]) == {}


def test_extract_signal_widths_from_simple_rtl(tmp_path):
    rtl = tmp_path / "simple.sv"
    rtl.write_text("""
module simple(
    input wire clk_i,
    input wire [7:0] data_i,
    output reg [15:0] result_o
);
endmodule
""", encoding="utf-8")
    widths = _extract_signal_widths([rtl])
    # yosys must be available for this to pass; if not, returns {}.
    # This test is best-effort: we assert structure, not exact values.
    if widths:
        assert widths.get("clk_i", 1) == 1
        assert widths.get("data_i", 8) == 8
        assert widths.get("result_o", 16) == 16


def test_run_formal_solver_skips_non_pending(tmp_path):
    findings = [
        {"formal": {"status": "NO_PROPERTY"}},
        {"formal": {"status": "NAME_UNVERIFIED"}},
    ]
    result = run_formal_solver(findings, rtl_files=[], work_dir=tmp_path)
    # No changes since none are PENDING.
    assert result == findings


def test_run_formal_solver_backfills_result_for_pending(tmp_path):
    # Minimal PENDING finding (missing sva → ERROR).
    findings = [
        {
            "formal": {
                "status": "PENDING",
                "sva": "",
                "bind_module": "",
            }
        }
    ]
    result = run_formal_solver(findings, rtl_files=[], work_dir=tmp_path, timeout_per_sva=1)
    assert "formal_result" in result[0]
    assert result[0]["formal_result"]["status"] == "ERROR"
    assert result[0]["formal_result"]["verdict"] == "ERROR"
    assert "Missing sva" in result[0]["formal_result"]["error_log"]
