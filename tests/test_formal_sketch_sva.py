from __future__ import annotations

from rtl_bug_agent.phase2.formal_sketch import (
    render_sva_bind,
    validate_signal_names,
)


def _sketch():
    return {
        "scope": "keymgr_ctrl",
        "clock": "clk_i",
        "reset": "rst_ni",
        "temporal_shape": "next_cycle",
        "antecedent": "key_state_d_valid == 1",
        "consequent": "key_state_q == $past(key_state_d)",
        "signals": ["key_state_q", "key_state_d", "key_state_d_valid"],
    }


def test_render_sva_bind_builds_checker_and_bind() -> None:
    out = render_sva_bind(_sketch(), "keymgr_ctrl")
    assert out["sva"].startswith("assert property")
    assert "|=>" in out["sva"]  # next_cycle
    assert "disable iff (!rst_ni)" in out["sva"]
    # Checker module wraps the property and declares clock + reset + signals.
    assert out["checker"].startswith("module keymgr_ctrl_p_csbc_chk (")
    assert "input logic clk_i" in out["checker"]
    assert "input logic rst_ni" in out["checker"]
    assert "input logic key_state_q" in out["checker"]
    assert out["checker"].rstrip().endswith("endmodule")
    # Bind statement targets the DUT module.
    assert out["bind_stmt"] == "bind keymgr_ctrl keymgr_ctrl_p_csbc_chk i_keymgr_ctrl_p_csbc_chk (.*);"
    # Referenced signals collected; clock/reset excluded from bind_signals.
    assert "key_state_q" in out["bind_signals"]
    assert "clk_i" not in out["bind_signals"]
    assert "rst_ni" not in out["bind_signals"]


def test_render_sva_bind_empty_when_no_body() -> None:
    assert render_sva_bind({"clock": "clk_i"}, "m") == {}


def test_render_sva_bind_collects_expression_signals() -> None:
    sk = {
        "clock": "clk_i",
        "antecedent": "mux_sel_err == 1",
        "consequent": "iv_we == 0",
        "signals": [],
    }
    out = render_sva_bind(sk, "aes_ctr")
    # Names from expressions are picked up even when signals[] is empty.
    assert "mux_sel_err" in out["bind_signals"]
    assert "iv_we" in out["bind_signals"]


def test_validate_signal_names_all_known() -> None:
    known = {"key_state_q", "key_state_d", "key_state_d_valid", "clk_i", "rst_ni"}
    res = validate_signal_names(_sketch(), known)
    assert res["ok"] is True
    assert res["unknown_signals"] == []


def test_validate_signal_names_flags_hallucinated() -> None:
    known = {"key_state_q", "key_state_d"}  # key_state_d_valid missing
    res = validate_signal_names(_sketch(), known)
    assert res["ok"] is False
    assert "key_state_d_valid" in res["unknown_signals"]


def test_validate_signal_names_accepts_graph_like_object() -> None:
    class FakeGraph:
        signals = {"iv_we": object(), "mux_sel_err": object()}

    sk = {"clock": "clk_i", "antecedent": "mux_sel_err == 1", "consequent": "iv_we == 0"}
    res = validate_signal_names(sk, FakeGraph())
    assert res["ok"] is True


def test_validate_ignores_keywords_clock_reset_literals() -> None:
    sk = {
        "clock": "clk_i",
        "reset": "rst_ni",
        "antecedent": "state == 1",
        "consequent": "out == 0",
        "signals": ["state", "out"],
    }
    known = {"state", "out"}
    res = validate_signal_names(sk, known)
    # clk_i/rst_ni and the numeric literals must not be flagged unknown.
    assert res["ok"] is True
    assert "clk_i" not in res["unknown_signals"]
    assert "1" not in res["checked_signals"]


def test_validate_ignores_sv_sized_literals() -> None:
    # Engineer feedback #1: 2'b10 / 1'b0 / 8'hff must not leak as b10/b0/hff.
    sk = {
        "clock": "clk_i",
        "antecedent": "sel == 2'b10 && mode == 8'hff",
        "consequent": "iv_we == 1'b0",
        "signals": ["sel", "mode", "iv_we"],
    }
    known = {"sel", "mode", "iv_we", "clk_i"}
    res = validate_signal_names(sk, known)
    assert res["ok"] is True, res["unknown_signals"]
    for fake in ("b10", "b0", "hff"):
        assert fake not in res["checked_signals"]


def test_validate_ignores_sizeless_literal() -> None:
    sk = {"clock": "clk_i", "antecedent": "x == 'b0", "consequent": "y <= '0",
          "signals": ["x", "y"]}
    res = validate_signal_names(sk, {"x", "y", "clk_i"})
    assert res["ok"] is True
    assert "b0" not in res["checked_signals"]


def test_render_sva_bind_scalar_default_ports() -> None:
    out = render_sva_bind(_sketch(), "keymgr_ctrl")
    # No widths supplied → scalar ports, and no vector syntax leaks in.
    assert "input logic key_state_q" in out["checker"]
    assert "[" not in out["checker"].split("p_csbc:")[0]  # no [N:0] in port list
    assert out["port_widths"] == {}


def test_render_sva_bind_vector_ports_when_width_known() -> None:
    # Engineer feedback #2: multi-bit signals must not be 1-bit ports.
    widths = {"key_state_q": 64, "key_state_d": 64, "key_state_d_valid": 1}
    out = render_sva_bind(_sketch(), "keymgr_ctrl", signal_widths=widths)
    assert "input logic [63:0] key_state_q" in out["checker"]
    assert "input logic [63:0] key_state_d" in out["checker"]
    # 1-bit signal stays scalar.
    assert "input logic key_state_d_valid" in out["checker"]
    assert out["port_widths"]["key_state_q"] == 64

