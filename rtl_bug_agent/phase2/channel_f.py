"""
Channel F: Property Synthesis for Unpaired Items (Formal CSBC v2.0 §3.4)
=======================================================================

Semantic pairing leaves some items unmatched — most importantly uncertain
points, but also high-value assumptions/guarantees that found no counterpart.
Legacy behaviour dropped these to LOW priority with no formal follow-up.

Channel F gives each *gated* unpaired item one independent LLM call whose ONLY
job is to synthesise a single solver-ready SVA (never a bug verdict). The SVA is
run through the same ``normalise_formal_property`` status machine as Channel B,
so both SVA sources share one schema. The bug decision still belongs to Phase 3.

Gating (v2.0 §3.4): only items that are ``formalizability == "direct"`` OR touch
a security signal get an LLM call. Everything else is recorded as ``GATED_OUT``
so traceability is preserved without spending tokens on low-value items.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from rtl_bug_agent.llm.client import OpenAICompatibleClient
from rtl_bug_agent.phase2.channel_b import (
    _JsonlCheckpoint,
    _call_with_retry,
    _parse_llm_response,
    normalise_formal_property,
)
from rtl_bug_agent.phase2.signal_graph import SignalGraph
from rtl_bug_agent.phase2.trace import append_trace

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = _PROJECT_ROOT / "config/prompts/phase2/channel_f_property_synth.md"

_SOURCE_WINDOW = 6
_MAX_SOURCE_LINES = 40


def gate_candidate(
    candidate: dict[str, Any],
    security_signals: set[str],
) -> tuple[bool, str]:
    """Decide whether a candidate is worth an LLM property-synthesis call.

    Returns ``(allowed, reason)``. Allowed when the item is formalizability
    ``direct`` OR any of its signals is a security signal. Reason is a short
    tag for trace ("direct", "security", or "low_value").
    """
    formalizability = str(
        (candidate.get("formal_sketch", {}) or {}).get("formalizability", "")
        or candidate.get("formalizability", "")
    ).strip().lower()
    if formalizability == "direct":
        return True, "direct"
    sigs = {str(s) for s in candidate.get("signals", []) or []}
    if sigs & security_signals:
        return True, "security"
    return False, "low_value"


def run_channel_f(
    candidates: list[dict[str, Any]],
    graph: SignalGraph,
    client: OpenAICompatibleClient,
    security_signals: set[str],
    prompt_path: str | Path = DEFAULT_PROMPT,
    max_tokens: int = 4000,
    workers: int = 4,
    checkpoint_path: str | None = None,
    trace_sink: Any | None = None,
) -> list[dict[str, Any]]:
    """Synthesise SVAs for gated unpaired *candidates*.

    Each candidate is one dict from ``unmatched_uncertain_candidates`` (or a
    high-value a/g built the same way), carrying at least ``chunk_id``,
    ``uncertain_text``/``text``, ``signals``, ``source_file``, ``line_start/end``.
    """
    prompt_template = Path(prompt_path).read_text(encoding="utf-8")
    if not candidates:
        print("Channel F: no unpaired candidates.")
        return []

    ckpt = _JsonlCheckpoint(checkpoint_path) if checkpoint_path else None
    all_findings: list[dict[str, Any]] = ckpt.load() if ckpt else []
    processed: set[str] = {f.get("_channel_f_id", "") for f in all_findings}
    remaining = [c for c in candidates if _cand_id(c) not in processed]

    if not remaining:
        print(f"  Channel F done: {len(all_findings)} findings (all from checkpoint)")
        return [f for f in all_findings if not f.get("_empty")]

    def _process_one(cand):
        cand_id = _cand_id(cand)
        allowed, reason = gate_candidate(cand, security_signals)
        if not allowed:
            finding = _gated_out_finding(cand, reason)
            _trace_channel_f(finding, cand, trace_sink, gated=reason)
            if ckpt:
                ckpt.append_all([finding])
            return finding
        finding = _call_with_retry(
            lambda: _synthesise_one(cand, graph, client, prompt_template, max_tokens),
            attempts=3,
        )
        if finding is None:
            finding = _gated_out_finding(cand, "llm_error")
        normalise_formal_property(finding, graph=graph, sva_source="channel_f")
        finding["_channel_f_id"] = cand_id
        finding["channels"] = ["F-SVA"]
        _trace_channel_f(finding, cand, trace_sink, gated="")
        if ckpt:
            ckpt.append_all([finding])
        return finding

    findings: list[dict[str, Any]] = []
    gated = 0
    pending = 0
    if workers <= 1:
        for cand in remaining:
            f = _process_one(cand)
            findings.append(f)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_process_one, c): c for c in remaining}
            for future in as_completed(futures):
                try:
                    findings.append(future.result())
                except Exception:
                    pass

    all_findings.extend(findings)
    all_findings = [f for f in all_findings if not f.get("_empty")]
    for f in all_findings:
        status = (f.get("formal", {}) or {}).get("status", "")
        if status == "PENDING":
            pending += 1
        elif status == "GATED_OUT":
            gated += 1
    print(
        f"  Channel F done: {len(all_findings)} findings "
        f"({pending} PENDING SVA, {gated} gated out)"
    )
    return all_findings


def _synthesise_one(
    cand: dict[str, Any],
    graph: SignalGraph,
    client: OpenAICompatibleClient,
    prompt_template: str,
    max_tokens: int,
) -> dict[str, Any]:
    text = str(cand.get("uncertain_text") or cand.get("text") or "")
    context = {
        "item": {
            "chunk_id": cand.get("chunk_id", ""),
            "kind": cand.get("kind", "uncertain"),
            "text": text,
            "signals": cand.get("signals", []),
            "module": _module_of(cand, graph),
        },
        "source_excerpt": _source_excerpt(cand, graph),
        "signal_context": [
            {"signal": s, "is_security": s in set(graph.get_security_signals())}
            for s in (cand.get("signals", []) or [])[:8]
        ],
    }
    content = client.chat(
        messages=[
            {"role": "system", "content": prompt_template},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False, indent=2)},
        ],
        max_tokens=max_tokens,
    )
    parsed = _parse_llm_response(content)
    fp = parsed.get("formal_property", {})
    if not isinstance(fp, dict):
        fp = {}
    return {
        "title": f"[F-SVA] {text[:140]}",
        "severity": "LOW",
        "verdict": "UNCERTAIN",
        "involved_signals": cand.get("signals", []),
        "involved_specs": [cand.get("chunk_id", "")],
        "contradiction": text[:400],
        "formal_property": fp,
    }


def _gated_out_finding(cand: dict[str, Any], reason: str) -> dict[str, Any]:
    text = str(cand.get("uncertain_text") or cand.get("text") or "")
    return {
        "title": f"[F-SVA] {text[:140]}",
        "severity": "LOW",
        "verdict": "UNCERTAIN",
        "involved_signals": cand.get("signals", []),
        "involved_specs": [cand.get("chunk_id", "")],
        "contradiction": text[:400],
        "channels": ["F-SVA"],
        "_channel_f_id": _cand_id(cand),
        "formal": {
            "status": "GATED_OUT",
            "sva": "",
            "sva_source": "channel_f",
            "gate_reason": reason,
        },
    }


def _trace_channel_f(
    finding: dict[str, Any],
    cand: dict[str, Any],
    sink: Any | None,
    *,
    gated: str,
) -> None:
    if sink is None:
        return
    cand_id = _cand_id(cand)
    append_trace(
        finding, "chunk", sink=sink, finding_id=cand_id,
        id=cand.get("chunk_id", ""), signals=cand.get("signals", []),
        source_refs=cand.get("source_refs", []),
    )
    append_trace(
        finding, "atom", sink=sink, finding_id=cand_id,
        kind=cand.get("kind", "uncertain"),
        formalizability=(cand.get("formal_sketch", {}) or {}).get("formalizability", ""),
    )
    formal = finding.get("formal", {}) or {}
    append_trace(
        finding, "channel_f", sink=sink, finding_id=cand_id,
        gated_reason=gated or formal.get("gate_reason", ""),
        sva_emitted=bool(formal.get("sva")),
        formal_status=formal.get("status", ""),
        unknown_signals=formal.get("unknown_signals", []),
    )


def _cand_id(cand: dict[str, Any]) -> str:
    """Stable, collision-free identity for a candidate.

    Requirements: re-running the *same* candidate must yield the same id (for
    checkpoint resume), while *different* candidates under the same chunk must
    differ (otherwise the second one is silently dropped as "already processed"
    and trace records collapse onto one key).

    Upstream producers (semantic_ag.unmatched_uncertain_candidates,
    uncertain_collector) only fill ``chunk_id``, so ``chunk_id`` alone collides
    when a chunk has several unpaired items. We therefore fold a short hash of
    the item text into the id. An explicit ``atom_id`` (semantic atoms) wins
    when present, since it is already unique.
    """
    atom_id = str(cand.get("atom_id") or "").strip()
    if atom_id:
        return atom_id
    chunk_id = str(cand.get("chunk_id") or cand.get("source", "") or "item").strip()
    text = str(cand.get("uncertain_text") or cand.get("text") or "")
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"{chunk_id}#{digest}"


def _module_of(cand: dict[str, Any], graph: SignalGraph) -> str:
    chunk_id = cand.get("chunk_id", "")
    meta = graph.spec_meta.get(chunk_id, {}) if hasattr(graph, "spec_meta") else {}
    return str(meta.get("module", "") or "")


def _source_excerpt(cand: dict[str, Any], graph: SignalGraph) -> str:
    src = str(cand.get("source_file", "") or "")
    if not src:
        meta = graph.spec_meta.get(cand.get("chunk_id", ""), {}) if hasattr(graph, "spec_meta") else {}
        src = str(meta.get("source_file", "") or "")
    if not src or not Path(src).exists():
        return ""
    start = max(1, int(cand.get("line_start", 0) or 0) - _SOURCE_WINDOW)
    end = int(cand.get("line_end", 0) or 0) + _SOURCE_WINDOW
    try:
        lines = Path(src).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    if end <= start:
        end = min(len(lines), start + _MAX_SOURCE_LINES)
    end = min(end, len(lines), start + _MAX_SOURCE_LINES)
    out = [f"{i + 1:5d}: {lines[i]}" for i in range(start - 1, end)]
    return "\n".join(out)
