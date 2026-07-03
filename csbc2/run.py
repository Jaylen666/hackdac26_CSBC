"""
CSBC v3 pipeline runner.
Phase 1: Extract formal clauses from chunks via LLM
Phase 2: Cross-reference + structural anomaly detection + Z3 check
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from rtl_bug_agent.schema import RtlChunk
from csbc2.phase1 import (
    FormalClause,
    call_llm,
    _PROMPT_PATH,
    _with_line_numbers,
    parse_response,
)
from csbc2.phase2 import pair_and_check


def load_chunks(path: str) -> list[RtlChunk]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [RtlChunk(**c) for c in data]


def extract_all(chunks: list[RtlChunk], max_tokens: int = 6000) -> list[FormalClause]:
    """Phase 1: Extract formal clauses from all chunks."""
    prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    all_clauses: list[FormalClause] = []

    for i, chunk in enumerate(chunks):
        chunk_id = chunk.chunk_id
        print(f"  [{i+1}/{len(chunks)}] {chunk_id[:60]}...", end=" ", flush=True)

        user = json.dumps({
            "chunk_id": chunk_id,
            "kind": chunk.kind,
            "module": chunk.module,
            "source_file": chunk.source_file,
            "line_start": chunk.line_start,
            "line_end": chunk.line_end,
            "code": _with_line_numbers(chunk),
        }, ensure_ascii=False, indent=2)

        raw = call_llm(prompt, user, max_tokens)
        if raw.startswith("ERROR:"):
            print(f"LLM error: {raw}")
            continue

        try:
            parsed = parse_response(raw)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"Parse error: {e}")
            continue

        count = 0
        for item in parsed.get("guarantees", []):
            if isinstance(item, dict):
                all_clauses.append(_dict_to_clause(item, "guarantee", chunk_id, chunk))
                count += 1
        for item in parsed.get("assumptions", []):
            if isinstance(item, dict):
                all_clauses.append(_dict_to_clause(item, "assumption", chunk_id, chunk))
                count += 1

        nf = sum(1 for c in all_clauses[-count:] if c.formalizable) if count else 0
        print(f"{count} clauses ({nf} formalizable)")

    return all_clauses


def _dict_to_clause(
    item: dict[str, Any], kind: str, spec_id: str, chunk: RtlChunk,
) -> FormalClause:
    return FormalClause(
        signal=str(item.get("signal", "")),
        kind=kind,
        antecedent=str(item.get("antecedent", "")),
        consequent=str(item.get("consequent", "")),
        temporal=str(item.get("temporal", "comb")),
        formalizable=bool(item.get("formalizable", False)),
        claim=str(item.get("claim", "")),
        risk=str(item.get("risk", "")),
        spec_id=spec_id,
        source_file=chunk.source_file,
        line_start=chunk.line_start,
        line_end=chunk.line_end,
    )


# ---------------------------------------------------------------------------
# Phase 2a: Structural anomaly detection
# ---------------------------------------------------------------------------

def find_gating_anomalies(clauses: list[FormalClause]) -> list[dict[str, Any]]:
    """Find signals whose gating pattern differs from others with the same suffix.

    E.g., if ALL `_we` signals use `!reg_error` but one uses `reg_error`,
    that's an anomaly.
    """
    # Group by signal suffix pattern
    by_suffix: dict[str, list[FormalClause]] = defaultdict(list)
    for c in clauses:
        if c.kind != "guarantee" or not c.formalizable:
            continue
        # Extract the base pattern from the signal name
        sig = c.signal
        # Match common suffixes
        for suffix in ("_we", "_re", "_en", "_sel", "_d", "_q", "_valid", "_ready", "_ack"):
            if sig.endswith(suffix):
                by_suffix[suffix].append(c)
                break
        else:
            by_suffix["_other"].append(c)

    findings: list[dict[str, Any]] = []

    for suffix, group in by_suffix.items():
        if len(group) < 3:
            continue  # need at least 3 to detect an outlier

        # Extract gating expressions from antecedents
        ant_forms: dict[str, list[str]] = defaultdict(list)
        for c in group:
            # Normalize: sort terms, strip signal names (keep operator structure)
            form = _normalize_antecedent(c.antecedent)
            ant_forms[form].append(c.signal)

        # If there are multiple forms, the minority is suspicious
        if len(ant_forms) > 1:
            total = len(group)
            for form, signals in sorted(ant_forms.items(), key=lambda x: -len(x[1])):
                if len(signals) <= total * 0.3:  # minority
                    majority_form = max(ant_forms, key=lambda f: len(ant_forms[f]))
                    findings.append({
                        "finding_id": f"S-{len(findings)+1:04d}",
                        "title": f"Gating anomaly: {', '.join(signals)} ({suffix}) "
                                 f"differs from convention",
                        "severity": "MEDIUM",
                        "channels": ["structural"],
                        "verdict": "GATING_ANOMALY",
                        "contradiction": (
                            f"Signals {', '.join(signals)} use gating pattern "
                            f"'{form}' while the majority ({len(ant_forms[majority_form])}/{total}) "
                            f"use '{majority_form}'"
                        ),
                        "involved_signals": signals,
                    })

    return findings


def _normalize_antecedent(ant: str) -> str:
    """Normalize an antecedent to a structural form.

    Replaces specific signal names (like addr_hit[N]) with a placeholder.
    """
    # Replace addr_hit\[N\] with addr_hit[i] 
    text = re.sub(r"addr_hit\[\d+\]", "addr_hit[i]", ant)
    # Replace specific register names
    text = re.sub(r"\b(intr_state|intr_enable|cfg|key_\d+|digest_\d+|cmd|wipe_secret)_", r"<reg>_", text)
    return text


# ---------------------------------------------------------------------------
# Phase 2b: Cross-chunk CSBC
# ---------------------------------------------------------------------------

def find_cross_chunk_contradictions(clauses: list[FormalClause]) -> list[dict[str, Any]]:
    """Cross-chunk CSBC: pair G from chunk A vs A from chunk B on same signal."""
    gs = [c for c in clauses if c.kind == "guarantee" and c.formalizable]
    ass = [c for c in clauses if c.kind == "assumption" and c.formalizable]
    return pair_and_check(gs, ass)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(ip: str = "hmac", max_chunks: int | None = None):
    chunks_path = Path(f"output/{ip}_chunks.json")
    if not chunks_path.exists():
        print(f"Error: {chunks_path} not found")
        return

    chunks = load_chunks(str(chunks_path))
    if max_chunks:
        chunks = chunks[:max_chunks]

    print(f"Loading {len(chunks)} chunks for {ip}...")
    print()

    # Phase 1
    print("Phase 1: Extracting formal clauses...")
    clauses = extract_all(chunks)
    print(f"\nTotal: {len(clauses)} clauses")
    print(f"  Formalizable: {sum(1 for c in clauses if c.formalizable)}")
    print(f"  Non-formalizable: {sum(1 for c in clauses if not c.formalizable)}")

    # Phase 2a: Structural anomalies
    print("\nPhase 2a: Structural anomaly detection...")
    anomalies = find_gating_anomalies(clauses)
    print(f"  Found {len(anomalies)} anomalies")
    for a in anomalies:
        print(f"    {a['finding_id']}: {a['title'][:120]}")

    # Phase 2b: Cross-chunk contradictions
    print("\nPhase 2b: Cross-chunk Z3 contradictions...")
    contradictions = find_cross_chunk_contradictions(clauses)
    print(f"  Found {len(contradictions)} contradictions")
    for c in contradictions[:10]:
        print(f"    {c['finding_id']}: {c['title'][:120]}")

    # Summary
    print(f"\n=== Summary ===")
    print(f"Total findings: {len(anomalies) + len(contradictions)}")
    print(f"  Structural anomalies: {len(anomalies)}")
    print(f"  Z3 contradictions: {len(contradictions)}")

    # Save
    output = {
        "ip": ip,
        "clauses": [
            {"signal": c.signal, "kind": c.kind, "antecedent": c.antecedent,
             "consequent": c.consequent, "formalizable": c.formalizable,
             "spec_id": c.spec_id}
            for c in clauses
        ],
        "structural_anomalies": anomalies,
        "z3_contradictions": contradictions,
    }
    out_path = Path(f"output/csbc3_{ip}.json")
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    ip = sys.argv[1] if len(sys.argv) > 1 else "hmac"
    max_c = int(sys.argv[2]) if len(sys.argv) > 2 else None
    run(ip, max_c)
