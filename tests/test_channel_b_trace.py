from __future__ import annotations

import inspect

from rtl_bug_agent.phase2 import channel_b
from rtl_bug_agent.phase2.channel_b import (
    _trace_channel_b,
    _trace_channel_b_legacy,
)
from rtl_bug_agent.phase2.trace import STAGE_ORDER, TraceSink


def test_legacy_trace_emits_three_stages(tmp_path) -> None:
    sink = TraceSink(tmp_path / "t.jsonl")
    finding = {
        "verdict": "GAP",
        "involved_signals": ["iv_we"],
        "assumption": {"spec_id": "C2", "formal_sketch": {"formalizability": "direct"}},
        "formal": {"sva": "assert property (x);", "status": "PENDING", "unknown_signals": []},
    }
    _trace_channel_b_legacy(finding, "iv_we", sink)
    recs = sink.load()["C2"]
    assert [r["stage"] for r in recs] == ["chunk", "atom", "channel_b"]
    cb = recs[-1]
    assert cb["verdict"] == "GAP" and cb["sva_emitted"] is True
    assert cb["formal_status"] == "PENDING"


def test_legacy_trace_falls_back_to_signal_key(tmp_path) -> None:
    sink = TraceSink(tmp_path / "t.jsonl")
    finding = {"verdict": "GAP", "assumption": {}, "formal": {}}
    _trace_channel_b_legacy(finding, "some_sig", sink)
    assert "signal:some_sig" in sink.load()


def test_semantic_trace_emits_three_stages(tmp_path) -> None:
    sink = TraceSink(tmp_path / "t.jsonl")
    unit = {
        "unit_id": "U1",
        "query": {"atom_id": "A1", "spec_id": "C2", "kind": "assumption",
                  "signals": ["iv_we"], "source_refs": ["f.sv:1-2"],
                  "formal_sketch": {"formalizability": "direct"}},
        "matches": [],
    }
    finding = {"verdict": "UNCERTAIN", "formal": {"sva": "assert property (y);", "status": "INCOMPLETE"}}
    _trace_channel_b(finding, unit, sink)
    recs = sink.load()["A1"]
    assert [r["stage"] for r in recs] == ["chunk", "atom", "channel_b"]
    assert recs[1]["formalizability"] == "direct"


def test_trace_helpers_noop_when_sink_none() -> None:
    # Strict no-op: no mutation, no error.
    f1 = {"verdict": "GAP", "assumption": {"spec_id": "C2"}, "formal": {}}
    _trace_channel_b_legacy(f1, "s", None)
    assert "trace_ref" not in f1
    f2 = {"verdict": "GAP", "formal": {}}
    _trace_channel_b(f2, {"unit_id": "U", "query": {"atom_id": "A"}}, None)
    assert "trace_ref" not in f2


def test_all_three_run_functions_accept_trace_sink() -> None:
    # Regression for engineer feedback: legacy + semantic + guarded helper must
    # all expose a trace_sink parameter so wiring is uniform.
    assert "trace_sink" in inspect.signature(channel_b.run_channel_b).parameters
    assert "trace_sink" in inspect.signature(channel_b.run_channel_b_semantic).parameters
    assert "trace_sink" in inspect.signature(channel_b._run_channel_b_semantic_batched).parameters


def test_channel_b_stage_is_canonical() -> None:
    assert "channel_b" in STAGE_ORDER
