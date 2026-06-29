"""
Phase 3: Source-Level Bug Confirmation
=======================================

Takes Phase 2 findings and verifies them against original RTL source
using a strong reasoning LLM.  Unlike Phase 2, which only compares
LLM-written behavioural specs, Phase 3 reads the actual SystemVerilog
code and makes an independent judgment.

Output verdicts: CONFIRMED, FALSE_ALARM, NEEDS_MORE_CONTEXT, UNCERTAIN.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rtl_bug_agent.llm.client import OpenAICompatibleClient
from rtl_bug_agent.phase2.formal_sketch import pick_property_draft, render_property_assertion
from rtl_bug_agent.phase2.llm_view import finding_for_llm
from rtl_bug_agent.phase2.signal_graph import SignalGraph
from rtl_bug_agent.phase2.trace import TraceSink, append_trace

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = _PROJECT_ROOT / "config/prompts/phase3/verify.md"


def _call_with_retry(fn, attempts: int = 3, delay: float = 1.0):
    import time as _time
    for a in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if a == attempts:
                print(f"ERROR (retries exhausted) ({exc})")
                return None
            _time.sleep(delay * a)

# Lines of context to include before/after each spec's line range.
_CONTEXT_WINDOW = 10
_MAX_LINES_PER_SECTION = 50
_MAX_SECTIONS_PER_FINDING = 2
_MAX_PAYLOAD_CHARS = 6800


def verify_finding(
    finding: dict[str, Any],
    graph: SignalGraph,
    client: OpenAICompatibleClient,
    official_claims: list[dict[str, Any]] | None = None,
    prompt_path: str | Path = DEFAULT_PROMPT,
    max_tokens: int = 2500,
) -> dict[str, Any]:
    """Verify a single Phase 2 finding against the original RTL source.

    Parameters
    ----------
    finding:
        One entry from the Phase 2 ``findings`` list.
    graph:
        The SignalGraph (needed to extract source file paths and line ranges).
    client:
        LLM client (should be a strong reasoning model).
    official_claims:
        Optional list of relevant official-spec claims (from Layer 2).
    """
    prompt_template = Path(prompt_path).read_text(encoding="utf-8")

    # ── Build the evidence package ────────────────────────────────
    source_sections = _extract_source_context(finding, graph)

    # Trim source code to stay under the API's payload limit
    while True:
        # Gate 1: the ONLY allowed projection of a finding into an LLM payload.
        # trace_ref and formal internals can never reach the prompt from here.
        finding_view = finding_for_llm(finding)
        finding_view["contradiction"] = str(finding_view.get("contradiction", "") or "")[:400]
        payload = {
            "finding": finding_view,
            "rtl_source": source_sections,
        }
        size = len(json.dumps(payload, ensure_ascii=False))
        if size <= _MAX_PAYLOAD_CHARS or not source_sections:
            break
        # Drop the largest section or trim the largest section's code
        largest = max(source_sections, key=lambda s: len(s["code"]))
        lines = largest["code"].split("\n")
        if len(lines) > _MAX_LINES_PER_SECTION // 2:
            largest["code"] = "\n".join(lines[:_MAX_LINES_PER_SECTION // 2])
        else:
            source_sections.remove(largest)

    if official_claims:
        payload["official_spec"] = [
            {"claim": c.get("claim", ""), "source": c.get("source", "")}
            for c in official_claims
        ]

    content = client.chat(
        messages=[
            {"role": "system", "content": prompt_template},
            {"role": "user",
             "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ],
        max_tokens=max_tokens,
    )

    return _parse_llm_response(content)


def verify_top_findings(
    findings: list[dict[str, Any]],
    graph: SignalGraph,
    client: OpenAICompatibleClient,
    top_n: int = 10,
    official_claims: list[dict[str, Any]] | None = None,
    trace_sink: "TraceSink | None" = None,
) -> list[dict[str, Any]]:
    """Verify the top-N Phase 2 findings (by score).

    Returns a list of verified findings (original finding + Phase 3 verdict).
    """
    from rtl_bug_agent.phase2.fusion import _score_to_severity

    # Sort by score descending, take top N
    sorted_findings = sorted(
        findings, key=lambda f: f.get("score", 0), reverse=True
    )[:top_n]

    results: list[dict[str, Any]] = []
    confirmed = 0
    false_alarms = 0

    for idx, f in enumerate(sorted_findings):
        fid = f.get("finding_id", f"F-{idx}")
        title = f.get("title", "")[:100]
        print(
            f"  Phase3 [{idx + 1}/{len(sorted_findings)}] {fid} ... ",
            end="", flush=True,
        )
        verdict = _call_with_retry(
            lambda: verify_finding(f, graph, client, official_claims),
            attempts=3,
        )
        if verdict is None:
            results.append({**f, "phase3_verdict": "ERROR"})
            append_trace(f, "phase3", sink=trace_sink, verdict="ERROR")
            continue

        v = verdict.get("verdict", "UNCERTAIN")
        if v == "CONFIRMED":
            confirmed += 1
        elif v == "FALSE_ALARM":
            false_alarms += 1
        print(f"{v} (confidence={verdict.get('confidence', '?')})")

        enriched = dict(f)
        enriched["phase3"] = verdict
        draft = _maybe_add_property_draft(enriched, graph)
        if draft:
            enriched["formal_draft"] = draft
        results.append(enriched)
        append_trace(
            enriched,
            "phase3",
            sink=trace_sink,
            verdict=v,
            confidence=verdict.get("confidence"),
        )

    print(
        f"  Phase3 done: {len(results)} verified "
        f"({confirmed} confirmed, {false_alarms} false alarms)"
    )
    return results


def _maybe_add_property_draft(
    finding: dict[str, Any],
    graph: SignalGraph,
) -> dict[str, Any] | None:
    verdict = str((finding.get("phase3") or {}).get("verdict", "")).upper()
    confidence = float((finding.get("phase3") or {}).get("confidence", 0.0) or 0.0)
    if verdict != "CONFIRMED":
        return None
    if confidence < 0.75:
        return None

    draft = pick_property_draft(finding, graph, min_confidence=0.75)
    if not draft:
        return None

    assertion = render_property_assertion(draft["sketch"])
    if not assertion:
        return None

    return {
        "spec_id": draft["spec_id"],
        "module": draft["module"],
        "assertion": assertion,
        "sketch": draft["sketch"],
    }


# ---------------------------------------------------------------------------
# Source extraction
# ---------------------------------------------------------------------------


def _extract_source_context(
    finding: dict[str, Any], graph: SignalGraph
) -> list[dict[str, Any]]:
    """Extract original RTL source code around the finding's involved specs.

    Reads actual .sv files with a ±30 line window around each spec's range.
    Overlapping ranges are merged.
    """
    # Collect all (source_file, line_start, line_end) tuples
    ranges: list[tuple[str, int, int]] = []
    seen: set[tuple[str, int, int]] = set()

    for spec_id in finding.get("involved_specs", []):
        meta = graph.spec_meta.get(spec_id, {})
        src = meta.get("source_file", "")
        if not src or not Path(src).exists():
            continue
        start = max(1, meta.get("line_start", 1) - _CONTEXT_WINDOW)
        end = meta.get("line_end", 99999) + _CONTEXT_WINDOW
        key = (src, start, end)
        if key not in seen:
            seen.add(key)
            ranges.append(key)

    # For each involved signal, also include driver/consumer spec ranges
    for sig in finding.get("involved_signals", []):
        info = graph.signals.get(sig)
        if not info:
            continue
        for spec_id in info.drivers + info.consumers:
            meta = graph.spec_meta.get(spec_id, {})
            src = meta.get("source_file", "")
            if not src or not Path(src).exists():
                continue
            start = max(1, meta.get("line_start", 1) - _CONTEXT_WINDOW)
            end = meta.get("line_end", 99999) + _CONTEXT_WINDOW
            key = (src, start, end)
            if key not in seen:
                seen.add(key)
                ranges.append(key)

    # Merge overlapping ranges and read code, cap sections
    sections = _read_ranges(ranges)
    # Prioritise smaller sections (more focused = more relevant)
    sections.sort(key=lambda s: len(s["code"]))
    return sections[:_MAX_SECTIONS_PER_FINDING]


def _read_ranges(
    ranges: list[tuple[str, int, int]]
) -> list[dict[str, Any]]:
    """Read source code for each range, merging overlaps."""
    # Group by file
    by_file: dict[str, list[tuple[int, int]]] = {}
    for src, start, end in ranges:
        by_file.setdefault(src, []).append((start, end))

    sections: list[dict[str, Any]] = []
    for src, file_ranges in by_file.items():
        merged = _merge_ranges(file_ranges)
        lines = Path(src).read_text(encoding="utf-8").splitlines()
        for start, end in merged:
            end = min(end, len(lines))
            total = end - start + 1
            if total <= _MAX_LINES_PER_SECTION:
                code = [f"{i + 1:5d}: {lines[i]}" for i in range(start - 1, end)]
            else:
                half = _MAX_LINES_PER_SECTION // 2
                code = [f"{i + 1:5d}: {lines[i]}" for i in range(start - 1, start - 1 + half)]
                code.append(f"     ... ({total - _MAX_LINES_PER_SECTION} lines omitted) ...")
                code += [f"{i + 1:5d}: {lines[i]}" for i in range(end - half, end)]
                start = start  # keep original line_start
            sections.append({
                "file": src,
                "line_start": start,
                "line_end": end,
                "code": "\n".join(code),
            })

    return sections


def _merge_ranges(
    ranges: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Merge overlapping line ranges."""
    if not ranges:
        return []
    sorted_ranges = sorted(ranges)
    merged = [sorted_ranges[0]]
    for start, end in sorted_ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 1:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _parse_llm_response(content: str) -> dict[str, Any]:
    import re
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))
