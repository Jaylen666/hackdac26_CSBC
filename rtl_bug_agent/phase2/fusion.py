"""
Pass 3: Fusion + Ranking
=========================

Takes findings from all Phase 2 channels, deduplicates, ranks by severity,
and performs cross-channel verification.

Fixes applied (audit v2):
- Verdict extraction from all channel-specific field names
- Fuzzy signal-name clustering for cross-channel overlap
- Signal-criticality weight boosted to 60%
- Self-referential findings down-weighted (score × 0.7)
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Finding:
    """Normalised finding from any channel."""

    finding_id: str
    title: str
    severity: str
    channels: list[str] = field(default_factory=list)
    contradiction: str = ""
    involved_signals: list[str] = field(default_factory=list)
    involved_specs: list[str] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)
    verdict: str = "UNCERTAIN"

    # Computed
    signal_criticality: float = 0.0
    contradiction_strength: float = 0.0
    cross_channel_hits: int = 0
    is_self_ref: bool = False
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "title": self.title,
            "severity": self.severity,
            "verdict": self.verdict,
            "channels": self.channels,
            "cross_channel_hits": self.cross_channel_hits,
            "is_self_ref": self.is_self_ref,
            "score": round(self.score, 3),
            "contradiction": self.contradiction,
            "involved_signals": self.involved_signals,
            "involved_specs": self.involved_specs,
            "evidence": self.evidence[:8],
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fuse(
    channel_findings: dict[str, list[dict[str, Any]]],
    security_signals: set[str],
) -> list[Finding]:
    """Fuse findings from all channels into a ranked, deduplicated list."""
    all_findings: list[Finding] = []
    counter = 0

    for channel_name, raw_list in channel_findings.items():
        for raw in raw_list:
            counter += 1
            f = _normalise(raw, channel_name, f"F-{counter:04d}")
            all_findings.append(f)

    if not all_findings:
        return []

    # Compute per-finding metrics
    for f in all_findings:
        f.signal_criticality = _signal_criticality(
            f.involved_signals, security_signals
        )
        f.contradiction_strength = _verdict_strength(f.verdict)
        f.is_self_ref = len(set(f.involved_specs)) <= 1

    # Cluster with fuzzy signal matching (enables cross-channel overlap)
    clusters = _cluster_findings(all_findings)

    # Merge each cluster
    merged: list[Finding] = []
    for cluster in clusters:
        m = _merge_cluster(cluster)
        merged.append(m)

    # Compute final scores
    for f in merged:
        f.score = (
            0.60 * f.signal_criticality
            + 0.25 * f.contradiction_strength
            + 0.15 * min(f.cross_channel_hits / 2.0, 1.0)
        )
        # Self-referential findings get a penalty
        if f.is_self_ref:
            f.score *= 0.7
        f.severity = _score_to_severity(f.score)

    merged.sort(key=lambda f: f.score, reverse=True)

    for idx, f in enumerate(merged):
        f.finding_id = f"F-{idx + 1:04d}"

    return merged


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _normalise(
    raw: dict[str, Any], channel: str, finding_id: str
) -> Finding:
    """Convert a channel-specific finding dict into a normalised Finding."""

    # ── Signals ──────────────────────────────────────────────────────
    signals: list[str] = []
    if "signal" in raw:
        signals.append(str(raw["signal"]))
    if "signal_pair" in raw:
        for s in raw["signal_pair"]:
            signals.append(str(s))
    if "involved_signals" in raw:
        for s in raw["involved_signals"]:
            if isinstance(s, dict):
                signals.append(str(s.get("signal", s)))
            else:
                signals.append(str(s))
    signals = list(dict.fromkeys(signals))

    # ── Specs ────────────────────────────────────────────────────────
    specs: list[str] = []
    for key in ("consumer_spec", "driver_spec", "consumer_specs",
                "spec_id"):
        val = raw.get(key)
        if isinstance(val, str):
            specs.append(val)
        elif isinstance(val, list):
            specs.extend([str(v) for v in val])
    if "assumption" in raw and isinstance(raw["assumption"], dict):
        specs.append(raw["assumption"].get("spec_id", ""))
    for g in raw.get("relevant_guarantees", []):
        if isinstance(g, dict):
            specs.append(g.get("spec_id", ""))
    for g in raw.get("driver_guarantees", []):
        if isinstance(g, dict):
            specs.append(g.get("spec_id", ""))
    # Channel D: involved_signals may carry spec_id as dict
    if "involved_signals" in raw:
        for s in raw["involved_signals"]:
            if isinstance(s, dict) and "spec_id" in s:
                specs.append(str(s["spec_id"]))
    specs = [s for s in specs if s]
    specs = list(dict.fromkeys(specs))

    # ── Verdict (try multiple field names) ──────────────────────────
    verdict = "UNCERTAIN"
    for key in ("verdict", "verdict_type", "type"):
        v = raw.get(key, "")
        if v and isinstance(v, str) and v != "?":
            verdict = v.upper()
            break
    # Also try to extract from reasoning text
    if verdict == "UNCERTAIN":
        reasoning = raw.get("reasoning", "")
        if reasoning:
            for vword in ["CONTRADICTION", "GAP", "RACE", "DEFENSIVE",
                          "OFFSET", "SATISFIED", "COVERED", "CONSISTENT"]:
                if vword in reasoning.upper():
                    verdict = vword
                    break

    # ── Title ────────────────────────────────────────────────────────
    title = raw.get("title", "")
    if not title:
        constraint = ""
        if "assumption" in raw and isinstance(raw["assumption"], dict):
            constraint = raw["assumption"].get("constraint", "")
        title = (
            raw.get("contradiction", "")
            or raw.get("bug_description", "")
            or raw.get("race_description", "")
            or raw.get("gap_description", "")
            or constraint[:120]
            or f"Finding on {', '.join(signals[:3])}"
        )

    # ── Contradiction text ──────────────────────────────────────────
    contradiction = (
        raw.get("contradiction", "")
        or raw.get("bug_description", "")
        or raw.get("race_description", "")
        or raw.get("gap_description", "")
        or raw.get("reasoning", "")
    )[:500]

    # ── Evidence ────────────────────────────────────────────────────
    evidence: list[dict[str, str]] = []
    if "assumption" in raw and isinstance(raw["assumption"], dict):
        a = raw["assumption"]
        evidence.append({
            "spec": a.get("spec_id", ""),
            "field": "assumption",
            "excerpt": a.get("constraint", "")[:200],
        })
    for g in raw.get("relevant_guarantees", []):
        if isinstance(g, dict):
            evidence.append({
                "spec": g.get("spec_id", ""),
                "field": "guarantee",
                "excerpt": g.get("property", "")[:200],
            })
    for g in raw.get("driver_guarantees", []):
        if isinstance(g, dict):
            evidence.append({
                "spec": g.get("spec_id", ""),
                "field": "guarantee",
                "excerpt": g.get("property", "")[:200],
            })
    raw_evidence = raw.get("evidence", [])
    if isinstance(raw_evidence, list):
        for e in raw_evidence:
            if isinstance(e, dict):
                evidence.append({
                    "spec": str(e.get("spec", e.get("spec_id", ""))),
                    "field": str(e.get("field", "")),
                    "excerpt": str(e.get("excerpt", ""))[:200],
                })

    return Finding(
        finding_id=finding_id,
        title=title[:200],
        severity="MEDIUM",
        channels=[channel],
        contradiction=contradiction,
        involved_signals=signals,
        involved_specs=specs,
        evidence=evidence,
        verdict=verdict,
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _signal_criticality(
    signals: list[str], security_signals: set[str]
) -> float:
    """How security-relevant are the involved signals?  0-1.

    Also checks for crypto-related keywords in signal names as a fallback
    when signals aren't in the structured security set.
    """
    if not signals:
        return 0.05

    crypto_kw = [
        "key", "secret", "wipe", "digest", "hash", "hmac", "sha",
        "alert", "error", "fatal", "pad", "msg", "fifo",
    ]
    sec_count = 0
    for s in signals:
        slow = s.lower()
        if s in security_signals:
            sec_count += 1
        elif any(kw in slow for kw in crypto_kw):
            sec_count += 0.5  # partial credit for keyword match

    return min(sec_count / max(len(signals), 1), 1.0)


def _verdict_strength(verdict: str) -> float:
    """Map verdict string to a 0-1 strength score."""
    mapping = {
        "CONTRADICTION": 1.0,
        "VIOLATION": 1.0,
        "GAP": 0.9,
        "RACE": 0.85,
        "DEFENSIVE": 0.15,
        "OFFSET": 0.1,
        "SATISFIED": 0.0,
        "COVERED": 0.0,
        "CONSISTENT": 0.0,
        "UNCERTAIN": 0.35,
    }
    return mapping.get(verdict.upper(), 0.3)


def _score_to_severity(score: float) -> str:
    if score >= 0.5:
        return "HIGH"
    elif score >= 0.25:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Clustering (with fuzzy signal matching)
# ---------------------------------------------------------------------------


def _cluster_findings(findings: list[Finding]) -> list[list[Finding]]:
    """Group findings that likely describe the same underlying issue.

    Two findings are clustered if they share:
    - at least 1 exact signal name, OR
    - at least 2 specs, OR
    - their signal sets have fuzzy overlap (common prefix/suffix).
    """
    clusters: list[list[Finding]] = []
    assigned: set[int] = set()

    for i, f in enumerate(findings):
        if i in assigned:
            continue
        cluster = [f]
        assigned.add(i)

        for j, other in enumerate(findings):
            if j in assigned:
                continue

            # Exact match
            shared_sigs = set(f.involved_signals) & set(other.involved_signals)
            shared_specs = set(f.involved_specs) & set(other.involved_specs)

            # Fuzzy signal match
            fuzzy = _fuzzy_signal_overlap(
                f.involved_signals, other.involved_signals
            )

            if len(shared_sigs) >= 1 or len(shared_specs) >= 2 or fuzzy:
                cluster.append(other)
                assigned.add(j)

        clusters.append(cluster)

    return clusters


def _fuzzy_signal_overlap(
    sigs_a: list[str], sigs_b: list[str]
) -> bool:
    """Check if two signal lists share a 'fuzzy' match: signals that
    share a meaningful prefix or suffix (e.g. 'hash_done_event' and
    'hmac_done' share 'done')."""
    for sa in sigs_a:
        for sb in sigs_b:
            if sa == sb:
                continue  # exact match handled elsewhere
            # Shared substring of length >= 5
            for i in range(len(sa) - 4):
                sub = sa[i:i + 5]
                if sub in sb and sub not in ("_", "reg_"):
                    return True
    return False


# ---------------------------------------------------------------------------
# Cluster merge
# ---------------------------------------------------------------------------


def _merge_cluster(cluster: list[Finding]) -> Finding:
    """Merge a cluster of related findings into one representative Finding."""
    if len(cluster) == 1:
        f = cluster[0]
        f.cross_channel_hits = len(set(f.channels))
        return f

    merged = cluster[0]
    all_channels: list[str] = []
    all_signals: list[str] = list(merged.involved_signals)
    all_specs: list[str] = list(merged.involved_specs)
    all_evidence: list[dict[str, str]] = list(merged.evidence)
    best_contradiction = merged.contradiction
    best_verdict = merged.verdict

    for other in cluster[1:]:
        all_channels.extend(other.channels)
        for sig in other.involved_signals:
            if sig not in all_signals:
                all_signals.append(sig)
        for spec in other.involved_specs:
            if spec not in all_specs:
                all_specs.append(spec)
        all_evidence.extend(other.evidence)
        if len(other.contradiction) > len(best_contradiction):
            best_contradiction = other.contradiction
        if _verdict_strength(other.verdict) > _verdict_strength(best_verdict):
            best_verdict = other.verdict

    unique_channels = list(dict.fromkeys(all_channels))
    merged.channels = unique_channels
    merged.cross_channel_hits = len(unique_channels)
    merged.involved_signals = all_signals
    merged.involved_specs = all_specs
    merged.evidence = all_evidence[:10]
    merged.contradiction = best_contradiction
    merged.verdict = best_verdict
    # Self-ref is false if any involved spec came from a different source
    merged.is_self_ref = len(set(all_specs)) <= 1

    if merged.cross_channel_hits >= 2:
        merged.title = (
            f"[{'+'.join(unique_channels)}] {merged.title[:170]}"
        )

    return merged


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def write_findings(findings: list[Finding], path: str | Path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            [f.to_dict() for f in findings],
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


def print_summary(findings: list[Finding]) -> None:
    if not findings:
        print("No findings.")
        return

    by_sev = defaultdict(int)
    by_verdict = defaultdict(int)
    cross = 0
    for f in findings:
        by_sev[f.severity] += 1
        by_verdict[f.verdict] += 1
        if f.cross_channel_hits >= 2:
            cross += 1

    print(f"Findings: {len(findings)} total  (cross-channel: {cross})")
    print(f"  HIGH:   {by_sev.get('HIGH', 0)}")
    print(f"  MEDIUM: {by_sev.get('MEDIUM', 0)}")
    print(f"  LOW:    {by_sev.get('LOW', 0)}")
    print(f"  Verdicts: {dict(by_verdict)}")
    print()

    for f in findings[:10]:
        channels = "+".join(f.channels)
        ref = "[SELF]" if f.is_self_ref else ""
        print(
            f"  [{f.severity:6s}] [{channels:15s}] {ref} "
            f"{f.title[:120]}"
        )
        if len(f.involved_signals) <= 5:
            print(f"           signals: {', '.join(f.involved_signals)}")
    if len(findings) > 10:
        print(f"  ... and {len(findings) - 10} more")
