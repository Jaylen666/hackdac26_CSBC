from __future__ import annotations

from rtl_bug_agent.phase2.channel_b import normalise_formal_property


class FakeGraph:
    """Minimal SignalGraph stand-in exposing a .signals mapping."""

    def __init__(self, names):
        self.signals = {n: object() for n in names}


def _finding(verdict, sva, signals=None, **fp):
    f = {"verdict": verdict}
    prop = {"sva": sva}
    if signals is not None:
        prop["bind_signals"] = signals
    prop.update(fp)
    f["formal_property"] = prop
    return f


def test_pending_when_all_signals_known() -> None:
    g = FakeGraph({"iv_we", "iv_sel", "clk_i", "rst_ni"})
    f = _finding(
        "GAP",
        "assert property (@(posedge clk_i) disable iff (!rst_ni) (iv_sel == 1) |-> (iv_we == 0));",
        signals=["iv_we", "iv_sel"],
        clock="clk_i", reset="rst_ni", bind_module="aes", formalizability="direct",
    )
    out = normalise_formal_property(f, graph=g)
    assert out["formal"]["status"] == "PENDING"
    assert out["formal"]["unknown_signals"] == []
    assert out["formal"]["sva_source"] == "channel_b"
    assert out["formal"]["bind_module"] == "aes"


def test_name_unverified_when_signal_hallucinated() -> None:
    g = FakeGraph({"iv_we", "clk_i", "rst_ni"})  # iv_sel missing
    f = _finding(
        "GAP",
        "assert property (@(posedge clk_i) (iv_sel == 1) |-> (iv_we == 0));",
        signals=["iv_we", "iv_sel"], clock="clk_i", reset="rst_ni",
        bind_module="aes", formalizability="direct",
    )
    out = normalise_formal_property(f, graph=g)
    assert out["formal"]["status"] == "NAME_UNVERIFIED"
    assert "iv_sel" in out["formal"]["unknown_signals"]


def test_no_property_for_satisfied_verdict() -> None:
    g = FakeGraph({"iv_we", "clk_i"})
    f = _finding("SATISFIED", "assert property (@(posedge clk_i) (iv_we == 0));",
                 signals=["iv_we"], clock="clk_i")
    out = normalise_formal_property(f, graph=g)
    assert out["formal"]["status"] == "NO_PROPERTY"


def test_no_property_when_sva_empty() -> None:
    g = FakeGraph({"iv_we"})
    f = _finding("GAP", "", signals=["iv_we"])
    out = normalise_formal_property(f, graph=g)
    assert out["formal"]["status"] == "NO_PROPERTY"


def test_missing_formal_property_key_is_no_property() -> None:
    g = FakeGraph({"iv_we"})
    out = normalise_formal_property({"verdict": "GAP"}, graph=g)
    assert out["formal"]["status"] == "NO_PROPERTY"
    assert out["formal"]["sva"] == ""


def test_no_graph_means_name_unverified() -> None:
    f = _finding("CONTRADICTION", "assert property (@(posedge clk_i) (a) |-> (b));",
                 signals=["a", "b"], clock="clk_i",
                 bind_module="m", formalizability="direct")
    out = normalise_formal_property(f, graph=None)
    # Without a graph we cannot trust names → never PENDING.
    assert out["formal"]["status"] == "NAME_UNVERIFIED"


def test_uncertain_verdict_gets_property() -> None:
    g = FakeGraph({"key_state_q", "key_state_d", "clk_i", "rst_ni"})
    f = _finding(
        "UNCERTAIN",
        "assert property (@(posedge clk_i) disable iff (!rst_ni) (1) |=> (key_state_q == $past(key_state_d)));",
        signals=["key_state_q", "key_state_d"], clock="clk_i", reset="rst_ni",
        bind_module="keymgr_ctrl", formalizability="direct",
    )
    out = normalise_formal_property(f, graph=g)
    assert out["formal"]["status"] == "PENDING"


def test_incomplete_when_bind_module_missing() -> None:
    g = FakeGraph({"iv_we", "iv_sel", "clk_i"})
    f = _finding(
        "GAP", "assert property (@(posedge clk_i) (iv_sel == 1) |-> (iv_we == 0));",
        signals=["iv_we", "iv_sel"], clock="clk_i", formalizability="direct",
    )  # no bind_module
    out = normalise_formal_property(f, graph=g)
    assert out["formal"]["status"] == "INCOMPLETE"
    assert "bind_module" in out["formal"]["incomplete_reason"]


def test_incomplete_when_clock_missing() -> None:
    g = FakeGraph({"iv_we", "iv_sel"})
    f = _finding(
        "GAP", "assert property ((iv_sel == 1) |-> (iv_we == 0));",
        signals=["iv_we", "iv_sel"], bind_module="aes", formalizability="direct",
    )  # no clock
    out = normalise_formal_property(f, graph=g)
    assert out["formal"]["status"] == "INCOMPLETE"
    assert "clock" in out["formal"]["incomplete_reason"]


def test_incomplete_when_formalizability_none() -> None:
    g = FakeGraph({"iv_we", "iv_sel", "clk_i"})
    f = _finding(
        "GAP", "assert property (@(posedge clk_i) (iv_sel == 1) |-> (iv_we == 0));",
        signals=["iv_we", "iv_sel"], clock="clk_i", bind_module="aes",
        formalizability="none",
    )
    out = normalise_formal_property(f, graph=g)
    # Self-declared non-formalizable must not be solver-ready.
    assert out["formal"]["status"] == "INCOMPLETE"
    assert "formalizability=none" in out["formal"]["incomplete_reason"]


def test_pending_requires_all_solver_fields() -> None:
    # All fields present + names known → genuinely solver-ready PENDING.
    g = FakeGraph({"iv_we", "iv_sel", "clk_i", "rst_ni"})
    f = _finding(
        "GAP",
        "assert property (@(posedge clk_i) disable iff (!rst_ni) (iv_sel == 1) |-> (iv_we == 0));",
        signals=["iv_we", "iv_sel"], clock="clk_i", reset="rst_ni",
        bind_module="aes", formalizability="direct",
    )
    out = normalise_formal_property(f, graph=g)
    assert out["formal"]["status"] == "PENDING"
    assert "incomplete_reason" not in out["formal"]

