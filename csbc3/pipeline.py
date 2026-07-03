"""
CSBC v3 Pipeline: integrates all construct types into one signal graph.

┌─────────────┐   ┌──────────────────┐   ┌─────────────┐
│ Assign chunk │   │ Always chunk     │   │ Instance    │
│ (parser)     │   │ (NL + Formal LLM)│   │ (contract)  │
└──────┬──────┘   └──────┬───────────┘   └──────┬──────┘
       │                 │                      │
       ▼                 ▼                      ▼
   ┌──────────────────────────────────────────────┐
   │            Signal Graph                      │
   │  signal → { drivers: [G, ...],              │
   │              consumers: [A, ...] }           │
   └──────────────────┬───────────────────────────┘
                      │
          ┌───────────┴───────────┐
          ▼                       ▼
   ┌──────────────┐     ┌────────────────┐
   │ Structural   │     │ Z3 cross-check │
   │ anomaly      │     │ + LLM residual │
   │ (suffix)     │     │ (same signal)  │
   └──────┬───────┘     └───────┬────────┘
          │                     │
          ▼                     ▼
   ┌────────────────────────────────┐
   │        Findings                │
   └────────────────────────────────┘
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from csbc3.chunker import chunk_file, Chunk
from csbc3.parser import parse_all_assigns, find_gating_anomalies
from csbc3.always_extract import extract_always, run_on_chunks


def build_signal_graph(
    assign_clauses: list[dict],
    always_results: list,
) -> dict[str, dict]:
    """Build signal graph from all extraction sources.

    Returns: { signal_name: { "drivers": [G, ...], "consumers": [A, ...] } }
    """
    graph: dict[str, dict] = {}

    def _add(signal: str, role: str, entry: Any):
        if signal not in graph:
            graph[signal] = {"drivers": [], "consumers": []}
        graph[signal][role].append(entry)

    # Assign chunks: all are guarantees (drivers)
    for clause in assign_clauses:
        sig = clause.get("signal", "")
        if sig:
            _add(sig, "drivers", clause)

    # Always results: distinguish G (driven signals) vs A (uncertain points)
    for r in always_results:
        entry = {
            "type": "always",
            "signal": r.signal,
            "nl_claim": r.nl_claim,
            "nl_uncertainty": r.nl_uncertainty,
            "formal_antecedent": r.formal_antecedent,
            "formal_consequent": r.formal_consequent,
            "formalizable": r.formalizable,
            "cross_check": r.cross_check,
            "chunk_id": r.chunk_id,
            "temporal": r.temporal,
        }
        _add(r.signal, "drivers", entry)

    return graph


def match_pairs(
    graph: dict[str, dict],
) -> list[dict]:
    """For each signal, pair drivers vs consumers and check contradiction.

    Uses Z3 when both sides have formal clauses.
    Falls back to LLM mismatch check when either side is NL-only.
    """
    findings: list[dict] = []

    for signal, info in graph.items():
        drivers = info.get("drivers", [])
        consumers = info.get("consumers", [])

        if not drivers or not consumers:
            continue

        for g in drivers:
            for a in consumers:
                # Both formalizable? Use Z3
                g_formal = isinstance(g, dict) and g.get("formalizable", False)
                a_formal = isinstance(a, dict) and a.get("formalizable", False)

                if g_formal and a_formal:
                    from csbc3.z3_check import check_pair

                    g_sig = g.get("signal", signal)
                    a_sig = a.get("signal", signal)
                    g_ant = g.get("formal_antecedent", g.get("antecedent", "1"))
                    g_cons = g.get("formal_consequent", g.get("consequent", "1"))
                    a_ant = a.get("formal_antecedent", "1")
                    a_cons = a.get("formal_consequent", "1")

                    verdict = check_pair(g_sig, g_ant, g_cons, a_ant, a_cons)
                    if verdict == "CONTRADICTION":
                        findings.append({
                            "finding_id": f"Z-{len(findings)+1:04d}",
                            "signal": signal,
                            "verdict": "CONTRADICTION",
                            "source": "z3",
                            "guarantee": g.get("chunk_id", "assign"),
                            "assumption": a.get("chunk_id", "always"),
                        })
                    continue

                # NL-only residual check
                g_nl = g.get("nl_claim", g.get("claim", ""))
                a_nl = a.get("nl_claim", "")
                if g_nl and a_nl and check_nl_mismatch(g_nl, a_nl, signal):
                    findings.append({
                        "finding_id": f"N-{len(findings)+1:04d}",
                        "signal": signal,
                        "verdict": "SUSPECTED_MISMATCH",
                        "source": "nl",
                        "guarantee_nl": g_nl[:200],
                        "assumption_nl": a_nl[:200],
                    })

    return findings


def check_nl_mismatch(g_nl: str, a_nl: str, signal: str) -> bool:
    """Simple NL mismatch check.

    If driver says 'defaults to 0' but consumer says 'assumes non-zero',
    that's a mismatch worth flagging.
    """
    g = g_nl.lower()
    a = a_nl.lower()

    opposites = [
        ("zero", "non-zero"),
        ("cleared", "set"),
        ("disable", "enable"),
        ("never", "always"),
        ("0", "1"),
    ]

    for neg, pos in opposites:
        if neg in g and pos in a:
            return True
        if pos in g and neg in a:
            return True

    return False


def process_module(rtl_path: str) -> dict:
    """Full pipeline for one .sv file."""
    print(f"Chunking {rtl_path}...")
    chunks = chunk_file(rtl_path)
    print(f"  {len(chunks)} chunks")

    # Phase 1a: Parse assign chunks (deterministic)
    assign_clauses = parse_all_assigns(chunks)
    print(f"  {len(assign_clauses)} assign clauses")

    # Phase 1b: Extract always blocks (NL + Formal, parallel per block)
    always_results = run_on_chunks(chunks)
    print(f"  {len(always_results)} always signal specs")

    # Structural anomalies
    anomalies = find_gating_anomalies(assign_clauses)
    print(f"  {len(anomalies)} structural anomalies")

    # Signal graph
    graph = build_signal_graph(assign_clauses, always_results)
    print(f"  {len(graph)} signals in graph")

    # Cross-chunk matching
    pairs = match_pairs(graph)
    print(f"  {len(pairs)} pair contradictions")

    return {
        "file": rtl_path,
        "chunks": len(chunks),
        "assign_clauses": len(assign_clauses),
        "always_signals": len(always_results),
        "structural_anomalies": anomalies,
        "signal_graph_size": len(graph),
        "pairs": pairs,
    }


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "/home/AM/hack2dac/opentitan/hw/ip/hmac/rtl/hmac.sv"
    result = process_module(path)
    print(f"\n=== Results ===")
    print(f"  File: {result['file']}")
    print(f"  Chunks: {result['chunks']}")
    print(f"  Assign clauses: {result['assign_clauses']}")
    print(f"  Always signal specs: {result['always_signals']}")
    print(f"  Structural anomalies: {len(result['structural_anomalies'])}")
    print(f"  Cross-chunk pairs: {len(result['pairs'])}")
    for a in result['structural_anomalies'][:5]:
        print(f"    S: {a['title'][:100]}")
    for p in result['pairs'][:5]:
        print(f"    P: [{p['source']}] {p['signal']}: {p['verdict']}")
