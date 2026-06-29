from __future__ import annotations

import json

from rtl_bug_agent.phase2.channel_f import (
    gate_candidate,
    run_channel_f,
)
from rtl_bug_agent.phase2.trace import TraceSink


class FakeGraph:
    def __init__(self, names, security=None, spec_meta=None):
        self.signals = {n: object() for n in names}
        self._security = list(security or [])
        self.spec_meta = spec_meta or {}

    def get_security_signals(self):
        return self._security


class FakeClient:
    """Returns a canned formal_property JSON for every chat() call."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    def chat(self, messages, max_tokens=0):
        self.calls += 1
        return json.dumps(self._payload)


def test_gate_direct_and_security_and_low():
    assert gate_candidate({"formal_sketch": {"formalizability": "direct"}}, set())[0] is True
    assert gate_candidate({"signals": ["k"]}, {"k"})[0] is True
    allowed, reason = gate_candidate({"signals": ["x"], "formalizability": "partial"}, {"k"})
    assert allowed is False and reason == "low_value"


def test_gated_out_candidate_makes_no_llm_call(tmp_path):
    g = FakeGraph({"x"})
    client = FakeClient({"formal_property": {"sva": "x"}})
    cand = {"chunk_id": "C9", "text": "low value item", "signals": ["x"],
            "formalizability": "partial"}
    out = run_channel_f([cand], g, client, security_signals=set(),
                        trace_sink=None)
    assert client.calls == 0  # gated out → no token spend
    assert out[0]["formal"]["status"] == "GATED_OUT"
    assert out[0]["formal"]["gate_reason"] == "low_value"


def test_pending_property_for_security_candidate(tmp_path):
    g = FakeGraph({"key_state_q", "key_state_d", "clk_i", "rst_ni"},
                  security=["key_state_q"],
                  spec_meta={"C1": {"module": "keymgr_ctrl"}})
    payload = {"formal_property": {
        "sva": "assert property (@(posedge clk_i) disable iff (!rst_ni) (1) |=> (key_state_q == $past(key_state_d)));",
        "clock": "clk_i", "reset": "rst_ni", "bind_module": "keymgr_ctrl",
        "bind_signals": ["key_state_q", "key_state_d"], "formalizability": "direct",
    }}
    client = FakeClient(payload)
    cand = {"chunk_id": "C1", "text": "key_state_q stale?",
            "signals": ["key_state_q", "key_state_d"]}
    out = run_channel_f([cand], g, client, security_signals={"key_state_q"},
                        trace_sink=None)
    assert client.calls == 1
    assert out[0]["formal"]["status"] == "PENDING"
    assert out[0]["formal"]["sva_source"] == "channel_f"
    assert out[0]["channels"] == ["F-SVA"]


def test_channel_f_trace_emits_chunk_atom_channelf(tmp_path):
    g = FakeGraph({"k", "clk_i"}, security=["k"], spec_meta={"C2": {"module": "m"}})
    payload = {"formal_property": {
        "sva": "assert property (@(posedge clk_i) (k == 0));",
        "clock": "clk_i", "bind_module": "m", "bind_signals": ["k"],
        "formalizability": "direct",
    }}
    sink = TraceSink(tmp_path / "t.jsonl")
    cand = {"chunk_id": "C2", "atom_id": "U7", "text": "t", "signals": ["k"],
            "formal_sketch": {"formalizability": "direct"}}
    run_channel_f([cand], g, FakeClient(payload), security_signals={"k"},
                  trace_sink=sink)
    recs = sink.load()["U7"]
    assert [r["stage"] for r in recs] == ["chunk", "atom", "channel_f"]
    assert recs[-1]["sva_emitted"] is True


def test_gated_out_trace_records_reason(tmp_path):
    from rtl_bug_agent.phase2.channel_f import _cand_id
    g = FakeGraph({"x"})
    sink = TraceSink(tmp_path / "t.jsonl")
    cand = {"chunk_id": "C3", "text": "low", "signals": ["x"], "formalizability": "partial"}
    run_channel_f([cand], g, FakeClient({}), security_signals=set(), trace_sink=sink)
    recs = sink.load()[_cand_id(cand)]
    assert recs[-1]["stage"] == "channel_f"
    assert recs[-1]["gated_reason"] == "low_value"


def test_empty_candidates_returns_empty():
    g = FakeGraph({"x"})
    assert run_channel_f([], g, FakeClient({}), security_signals=set()) == []


def test_same_chunk_multiple_items_not_dropped(tmp_path):
    # Engineer feedback (HIGH): two unpaired items under the SAME chunk must not
    # collide on a single id. Reproduce: run u1 alone, then u1+u2 (same chunk).
    from rtl_bug_agent.phase2.channel_f import _cand_id
    g = FakeGraph({"a", "b", "clk_i"}, security=["a", "b"],
                  spec_meta={"C1": {"module": "m"}})
    payload = {"formal_property": {
        "sva": "assert property (@(posedge clk_i) (a == 0));",
        "clock": "clk_i", "bind_module": "m", "bind_signals": ["a"],
        "formalizability": "direct",
    }}
    u1 = {"chunk_id": "C1", "text": "first concern about a", "signals": ["a"]}
    u2 = {"chunk_id": "C1", "text": "second different concern about b", "signals": ["b"]}

    # Distinct ids despite same chunk.
    assert _cand_id(u1) != _cand_id(u2)

    ckpt = str(tmp_path / "F.jsonl")
    # First run: only u1.
    run_channel_f([u1], g, FakeClient(payload), security_signals={"a", "b"},
                  checkpoint_path=ckpt)
    # Second run: u1 + u2 (u1 resumes from checkpoint, u2 is new and must run).
    out = run_channel_f([u1, u2], g, FakeClient(payload), security_signals={"a", "b"},
                        checkpoint_path=ckpt)
    ids = {f["_channel_f_id"] for f in out}
    assert len(ids) == 2, f"second item dropped: {ids}"


def test_cand_id_stable_across_runs():
    from rtl_bug_agent.phase2.channel_f import _cand_id
    c = {"chunk_id": "C1", "text": "same text"}
    assert _cand_id(c) == _cand_id(dict(c))  # deterministic


def test_cand_id_prefers_atom_id():
    from rtl_bug_agent.phase2.channel_f import _cand_id
    assert _cand_id({"atom_id": "A7", "chunk_id": "C1", "text": "t"}) == "A7"

