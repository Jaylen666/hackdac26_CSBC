from __future__ import annotations

import json

from rtl_bug_agent.phase2.trace import (
    STAGE_ORDER,
    TraceSink,
    append_trace,
    ensure_trace_ref,
)


def test_append_trace_writes_sidecar_not_finding(tmp_path) -> None:
    sink = TraceSink(tmp_path / "trace_demo.jsonl")
    finding = {"finding_id": "F-0001", "verdict": "GAP"}

    append_trace(finding, "channel_b", sink=sink, verdict="GAP", sva_emitted=True)
    append_trace(finding, "formal_check", sink=sink, backend="sby_z3", verdict="CEX")

    # Gate 2: the finding carries only a pointer, never the trace records.
    assert finding["trace_ref"] == "F-0001"
    assert "trace" not in finding

    loaded = sink.load()
    assert [r["stage"] for r in loaded["F-0001"]] == ["channel_b", "formal_check"]
    assert loaded["F-0001"][1]["verdict"] == "CEX"


def test_append_trace_none_sink_is_strict_noop(tmp_path) -> None:
    finding = {"finding_id": "F-0009"}
    rec = append_trace(finding, "pair", sink=None, score=0.87)
    # Strict no-op: returns the record but does NOT mutate the finding at all,
    # so legacy output (tracing disabled) is byte-for-byte unchanged.
    assert "trace_ref" not in finding
    assert rec["stage"] == "pair" and rec["score"] == 0.87
    assert rec["finding_id"] == "F-0009"


def test_ensure_trace_ref_falls_back_to_existing(tmp_path) -> None:
    finding = {"trace_ref": "F-0042"}
    assert ensure_trace_ref(finding, "") == "F-0042"


def test_corrupted_line_is_skipped(tmp_path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps({"finding_id": "F-1", "stage": "chunk"}) + "\n"
        + "{ not json\n"
        + json.dumps({"finding_id": "F-1", "stage": "atom"}) + "\n",
        encoding="utf-8",
    )
    loaded = TraceSink(path).load()
    assert [r["stage"] for r in loaded["F-1"]] == ["chunk", "atom"]


def test_stage_order_canonical() -> None:
    assert STAGE_ORDER[0] == "chunk"
    assert STAGE_ORDER[-1] == "phase3"
    assert "formal_check" in STAGE_ORDER


def test_fuse_output_identical_with_and_without_trace(tmp_path) -> None:
    from rtl_bug_agent.phase2.fusion import fuse

    cf = {"B-AG": [
        {"signal": "iv_we", "verdict": "GAP", "title": "t",
         "involved_signals": ["iv_we"],
         "assumption": {"spec_id": "C2", "constraint": "x"},
         "relevant_guarantees": [{"spec_id": "C1", "property": "y"}]},
    ]}
    sec = {"iv_we"}

    off = [f.to_dict() for f in fuse(cf, sec, trace_sink=None)]
    from rtl_bug_agent.phase2.trace import TraceSink
    sink = TraceSink(tmp_path / "t.jsonl")
    on = [f.to_dict() for f in fuse(cf, sec, trace_sink=sink)]

    # Byte-for-byte parity: tracing must not alter the finding output at all.
    assert off == on
    # And trace_ref must NOT appear in legacy output.
    assert "trace_ref" not in off[0]
    # But the sidecar did capture the pair record.
    assert sink.load()
