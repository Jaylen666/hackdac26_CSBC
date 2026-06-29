"""
Uncertain Point Collector & Classifier
========================================

Phase 1 specs contain ``uncertain_points`` — LLM-written notes about
potential issues it cannot resolve from a single chunk.  This module:

1. Collects uncertain_points from all specs.
2. Classifies each: can it form an A-G pair (has driver)?  Is it redundant?
3. Injects qualified points as weak assumptions into Channel B.
4. Sends unqualifiable points directly to Phase 3 candidates.

Cost: 0 LLM calls.  SignalGraph lookup + regex only.
"""

from __future__ import annotations

import re
from typing import Any

from rtl_bug_agent.phase2.signal_graph import SignalGraph

# Common English/Verilog words to exclude from signal extraction
_NOISE = {
    "the", "is", "in", "of", "to", "be", "if", "at", "on", "by",
    "for", "not", "this", "that", "from", "with", "are", "was",
    "has", "can", "may", "will", "should", "does", "Idle", "None",
    "case", "default", "unique", "begin", "end", "assign", "always",
    "module", "endmodule", "input", "output", "reg", "wire", "logic",
}


def _uncertain_point_text(point: Any) -> str:
    """Normalize uncertain-point representations to plain text.

    Newer specs may emit structured dicts instead of bare strings.  We keep
    the collector tolerant so downstream legacy logic still works.
    """
    if isinstance(point, dict):
        parts = [
            str(point.get("claim", "")).strip(),
            str(point.get("cond", "")).strip(),
            str(point.get("risk", "")).strip(),
            str(point.get("property", "")).strip(),
            str(point.get("constraint", "")).strip(),
            str(point.get("bug_relevance", "")).strip(),
        ]
        return " ".join(part for part in parts if part)
    return str(point).strip()


def collect_and_classify(
    graph: SignalGraph,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collect uncertain_points from all specs and classify.

    Returns:
        (channel_b_candidates, phase3_candidates)
        - channel_b: qualified for weak-assumption injection into Channel B
        - phase3: no driver in graph → send directly to Phase 3
    """
    channel_b: list[dict[str, Any]] = []
    phase3: list[dict[str, Any]] = []
    seen: set[str] = set()

    for chunk_id, spec in graph.specs.items():
        for up in spec.get("uncertain_points", []):
            up_text = _uncertain_point_text(up)
            if not up_text:
                continue
            key = f"{chunk_id}::{up_text[:80]}"
            if key in seen:
                continue
            seen.add(key)

            # Extract signal names from text
            sigs = _extract_signals(up_text)
            sigs = [s for s in sigs if s in graph.signals]

            # Check: do any extracted signals have drivers?
            has_driver = any(
                bool(graph.signals[sig].drivers) for sig in sigs
                if sig in graph.signals
            )

            # Check semantic redundancy via TF-IDF cosine similarity.
            # Two texts that share signal names are only truly redundant
            # if their constraint texts describe the same concern.
            redundant = False
            if has_driver:
                for a in spec.get("assumptions", []):
                    if any(sig in a.get("related_signals", []) for sig in sigs):
                        sim = _text_similarity(up_text, a.get("constraint", ""))
                        if sim >= 0.3:  # high overlap → truly redundant
                            redundant = True
                            break

            candidate = {
                "chunk_id": chunk_id,
                "source_file": spec.get("source_file", ""),
                "line_start": spec.get("line_start", 0),
                "line_end": spec.get("line_end", 0),
                "uncertain_text": up_text[:400],
                "signals": sigs,
                "summary": spec.get("summary", "")[:120],
            }

            if has_driver and not redundant:
                # Build a weak assumption for Channel B injection
                candidate["weak_assumption"] = _build_weak_assumption(up_text, sigs)
                channel_b.append(candidate)
            elif not redundant:
                # No driver — can't form A-G pair; send directly to Phase 3
                phase3.append(candidate)
            # else: redundant AND has_driver — already covered by existing
            #        assumption; skip entirely (noise)

    return channel_b, phase3


def _extract_signals(text: str) -> list[str]:
    """Extract plausible signal names from uncertain_point text."""
    # Find backtick-quoted signals: `signal_name`
    quoted = re.findall(r"`([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)`", text)
    # Find bare signal-like words (underscore + lowercase/uppercase)
    bare = re.findall(
        r"\b([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\b", text
    )
    all_sigs = quoted + bare
    return list(dict.fromkeys(s for s in all_sigs if s.lower() not in _NOISE and len(s) > 2))


def _build_weak_assumption(text: str, signals: list[str]) -> dict[str, Any]:
    """Convert an uncertain_point into a weak assumption dict."""
    return {
        "constraint": text[:300],
        "bug_relevance": f"[UNCERTAIN — LLM flagged potential issue]: {text[:200]}",
        "related_signals": signals[:5],
        "source": "uncertain_point",  # marker for downstream processing
    }


# ── Semantic similarity (rule-based, 0 LLM calls) ──────────────────


def _text_similarity(a: str, b: str) -> float:
    """Composite similarity score for two short Chinese/English texts.

    Uses bigram overlap (captures phrase-level meaning) + word Jaccard
    (captures keyword overlap).  Returns 0-1.
    """
    if not a or not b:
        return 0.0

    def _tokenize(s):
        # Split on whitespace + Chinese char boundaries
        import re
        tokens = re.findall(r"[一-鿿]|[a-zA-Z0-9_]+", s.lower())
        return [t for t in tokens if len(t) >= 2]

    ta = _tokenize(a)
    tb = _tokenize(b)
    if not ta or not tb:
        return 0.0

    # Bigram overlap
    bigrams_a = set(zip(ta, ta[1:])) if len(ta) >= 2 else set()
    bigrams_b = set(zip(tb, tb[1:])) if len(tb) >= 2 else set()
    bigram_score = len(bigrams_a & bigrams_b) / max(len(bigrams_a | bigrams_b), 1)

    # Word Jaccard
    set_a = set(ta)
    set_b = set(tb)
    jaccard = len(set_a & set_b) / max(len(set_a | set_b), 1)

    # Combined: bigrams are stronger signal for phrase matching
    return 0.6 * bigram_score + 0.4 * jaccard


def print_summary(
    channel_b: list[dict[str, Any]],
    phase3: list[dict[str, Any]],
) -> None:
    """Print a summary of the collection results."""
    total = len(channel_b) + len(phase3)
    print(f"  Uncertain points: {total} total")
    print(f"    → Channel B (weak A-G): {len(channel_b)}")
    print(f"    → Phase 3 (direct):     {len(phase3)}")
    if channel_b:
        print(f"    Channel B samples:")
        for c in channel_b[:3]:
            print(f"      {c['signals'][:3]} | {c['uncertain_text'][:80]}")
    if phase3:
        print(f"    Phase 3 samples:")
        for c in phase3[:3]:
            print(f"      {c['signals'][:3]} | {c['uncertain_text'][:80]}")
