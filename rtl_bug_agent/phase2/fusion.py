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

from rtl_bug_agent.phase2.trace import TraceSink, append_trace


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
    formal_verdict: str = "NONE"
    formal_confidence: float = 0.0
    formal_draft: dict[str, Any] = field(default_factory=dict)
    formal_result: dict[str, Any] = field(default_factory=dict)
    formal: dict[str, Any] = field(default_factory=dict)  # Channel B/F solver-ready state

    # Computed
    signal_criticality: float = 0.0
    contradiction_strength: float = 0.0
    embedding_similarity: float = 0.0  # BGE-M3 semantic pairing score (0.66-0.95)
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
            "formal_verdict": self.formal_verdict,
            "formal_confidence": round(self.formal_confidence, 3),
            **({"formal_draft": self.formal_draft} if self.formal_draft else {}),
            **({"formal_result": self.formal_result} if self.formal_result else {}),
            **({"formal": self.formal} if self.formal else {}),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fuse(
    channel_findings: dict[str, list[dict[str, Any]]],
    security_signals: set[str],
    trace_sink: "TraceSink | None" = None,
    cluster: bool = True,
) -> list[Finding]:
    """Fuse findings from all channels into a ranked, deduplicated list.

    When ``cluster`` is True (default), findings that look like they describe
    the same underlying issue are merged via fuzzy signal/spec overlap.  This
    is convenient for human reading but, on modules with highly homogeneous
    signal names (e.g. keymgr's ``key_state_*`` / ``state_*`` family), the
    greedy clustering forms huge clusters that swallow precise findings — see
    the keymgr N-003 audit.  Set ``cluster=False`` to keep every channel
    finding as its own ranked entry: we accept duplicate bug descriptions
    (cheap to skim during manual review) in exchange for never silently
    dropping a real bug behind a longer, unrelated cluster representative.
    """
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

    if cluster:
        # Cluster with fuzzy signal matching (enables cross-channel overlap)
        clusters = _cluster_findings(all_findings)

        # Merge each cluster
        merged: list[Finding] = []
        for c in clusters:
            m = _merge_cluster(c)
            merged.append(m)
    else:
        # No clustering: every finding survives as its own entry.  Still set
        # cross_channel_hits so scoring stays well-defined.
        for f in all_findings:
            f.cross_channel_hits = len(set(f.channels))
        merged = all_findings

    # Compute final scores
    #
    # Rationale (改动①: 给 score 注入区分度 + 打破并列):
    # The previous formula was 0.60*criticality + 0.25*contradiction + 0.15*xchan.
    # Under cluster=False every finding is its own entry so cross_channel_hits is
    # always 1 → the last term collapses to a constant 0.075 and provides zero
    # discrimination.  Combined with a coarse signal_criticality, this drove huge
    # tie bands (e.g. kmac: 119 findings all at score 0.6), so the phase3 top-N
    # cutoff fell *inside* a tie band and was decided by array position, not
    # quality.  HIGH-severity, directly-formalizable findings (KMAC-036 rank 65,
    # N-005 rank 96) were dropped purely because of that.
    #
    # 改动③: 引入 embedding_similarity 作为域无关的质量指标。
    # BGE-M3 语义配对分数（0.66-0.95）衡量 assumption 和 guarantee 的语义贴合度，
    # 高分 = 两个 spec 真的在讨论同一行为 = contradiction 更可能是真实不一致。
    # 新权重：0.30*embedding + 0.30*verdict + 0.20*criticality + 0.15*formal + 0.05*xchan
    # 不依赖"哪些信号容易有bug"的假设，适用于 CTF 场景。
    for f in merged:
        xchan = min(max(f.cross_channel_hits - 1, 0) / 1.0, 1.0)
        # Normalize embedding_similarity from [0.66, 0.95] to [0, 1]
        # (0.66 is typical min_score threshold, 0.95 is near-perfect match)
        emb_norm = max(0.0, min((f.embedding_similarity - 0.66) / 0.29, 1.0))
        f.score = (
            0.30 * emb_norm
            + 0.30 * f.contradiction_strength
            + 0.20 * f.signal_criticality
            + 0.15 * _has_formal_sva(f)
            + 0.05 * xchan
        )
        # Self-referential findings get a penalty
        if f.is_self_ref:
            f.score *= 0.7
        f.severity = _score_to_severity(f.score)

    # Deterministic ordering: sort by score, then by a quality tie-break so two
    # findings with equal score never depend on their original array position.
    # Tie-break priority: stronger verdict → carries an SVA → more security
    # signals → longer (more specific) contradiction text.
    merged.sort(key=_rank_key, reverse=True)

    for idx, f in enumerate(merged):
        f.finding_id = f"F-{idx + 1:04d}"

    # Trace: record the fused outcome per finding (deterministic, no LLM).
    if trace_sink is not None:
        for f in merged:
            d = f.to_dict()
            append_trace(
                d,
                "pair",
                sink=trace_sink,
                finding_id=f.finding_id,
                channels=f.channels,
                verdict=f.verdict,
                score=round(f.score, 3),
                signals=f.involved_signals[:8],
                specs=f.involved_specs[:8],
            )

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

    # Backfill from formal.bind_signals (channel B writes bind_signals but not
    # involved_signals, so the anchor signal is often the only entry above).
    # Clock/reset names are excluded — they appear in every SVA but carry no
    # bug-localization value. Signals already flagged as unknown (hallucinated
    # by the LLM) are also excluded.
    _CLOCK_RESET = {"clk_i", "clk", "rst_ni", "rst_n", "rst", "reset", "clock"}
    _fp_formal = raw.get("formal", {})
    if isinstance(_fp_formal, dict):
        _unknown = set(_fp_formal.get("unknown_signals", []) or [])
        for _s in _fp_formal.get("bind_signals", []):
            _s = str(_s).strip()
            if _s and _s not in _CLOCK_RESET and _s not in _unknown and _s not in signals:
                signals.append(_s)

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

    formal_verdict, formal_confidence = _extract_formal_summary(raw)
    formal_draft = raw.get("formal_draft", {})
    if not isinstance(formal_draft, dict):
        formal_draft = {}
    formal_result = raw.get("formal_result", {})
    if not isinstance(formal_result, dict):
        formal_result = {}
    formal = raw.get("formal", {})
    if not isinstance(formal, dict):
        formal = {}

    # Extract embedding similarity from semantic AG pairing (if present)
    embedding_sim = float(raw.get("_embedding_similarity", 0.0))

    f = Finding(
        finding_id=finding_id,
        title=title[:200],
        severity="MEDIUM",
        channels=[channel],
        contradiction=contradiction,
        involved_signals=signals,
        involved_specs=specs,
        evidence=evidence,
        verdict=verdict,
        formal_verdict=formal_verdict,
        formal_confidence=formal_confidence,
        formal_draft=formal_draft,
        formal_result=formal_result,
        formal=formal,
    )
    f.embedding_similarity = embedding_sim
    return f


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


_CRIT_TIER1 = (  # direct secret material → 1.0
    "key", "secret", "seed", "share", "mask", "entropy",
)
_CRIT_TIER2 = (  # security output / wipe / fatal → 0.75
    "digest", "hash", "hmac", "sha", "wipe", "zeroize", "clear",
    "alert", "fatal",
)
_CRIT_TIER3 = (  # access-control / FSM / ECC → 0.5
    "lc_", "lifecycle", "sparse_fsm", "integrity", "ecc",
)


def _signal_criticality(
    signals: list[str], security_signals: set[str]
) -> float:
    """How security-relevant are the involved signals?  0-1.

    改动②: security_signals 在 crypto 模块（kmac/keymgr）几乎包含所有信号（314个），
    flat 1.0 导致91%的findings criticality饱和，失去区分度。
    现在按关键词分4档：Tier1(key/secret)=1.0, Tier2(digest/alert)=0.75,
    Tier3(lc_/ecc)=0.5, security_signals成员=0.4, 普通加密相关=0.25, 其他=0.0。
    """
    if not signals:
        return 0.05

    per_signal: list[float] = []
    for s in signals:
        slow = s.lower()
        if any(kw in slow for kw in _CRIT_TIER1):
            per_signal.append(1.0)
        elif any(kw in slow for kw in _CRIT_TIER2):
            per_signal.append(0.75)
        elif any(kw in slow for kw in _CRIT_TIER3):
            per_signal.append(0.5)
        elif s in security_signals:
            per_signal.append(0.4)   # 原来是 1.0，现在降为通用安全信号的底线
        elif any(kw in slow for kw in ("error", "msg", "fifo", "pad", "state")):
            per_signal.append(0.25)
        else:
            per_signal.append(0.0)

    mean = sum(per_signal) / len(per_signal)
    best = max(per_signal)
    return min(0.6 * mean + 0.4 * best, 1.0)


def _has_formal_sva(f: "Finding") -> float:
    """1.0 if the finding carries a concrete (non-empty) SVA, else 0.0.

    A finding that already has a formalizable assertion is the cheapest to
    verify downstream, so it earns a ranking bonus.  We look in the Channel B/F
    ``formal`` state (``sva``) and the older ``formal_draft`` (``assertion`` /
    ``sva``).  Note: carrying an SVA signals *verifiability*, not mechanism
    correctness — an SVA can encode the wrong property.  This only helps the
    finding reach phase3; correctness is judged there.
    """
    formal = f.formal if isinstance(f.formal, dict) else {}
    sva = str(formal.get("sva", "") or "").strip()
    if sva:
        return 1.0
    draft = f.formal_draft if isinstance(f.formal_draft, dict) else {}
    draft_sva = str(draft.get("assertion", draft.get("sva", "")) or "").strip()
    return 1.0 if draft_sva else 0.0


def _rank_key(f: "Finding") -> tuple:
    """Deterministic sort key: score first, then quality tie-breakers.

    Without an explicit tie-break, two findings with equal score keep their
    original array position — which is exactly how HIGH-severity findings fell
    just below the phase3 top-N cutoff inside a large tie band.  Tie-break
    priority after score: stronger verdict → carries an SVA → more involved
    security-relevant signals → longer (more specific) contradiction text.
    """
    return (
        round(f.score, 6),
        _verdict_strength(f.verdict),
        _has_formal_sva(f),
        len(f.involved_signals),
        len(f.contradiction or ""),
    )


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
    best_formal_verdict = merged.formal_verdict
    best_formal_confidence = merged.formal_confidence
    best_formal_draft = dict(merged.formal_draft)

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
        best_formal_verdict, best_formal_confidence, best_formal_draft = _merge_formal_summary(
            best_formal_verdict,
            best_formal_confidence,
            best_formal_draft,
            other.formal_verdict,
            other.formal_confidence,
            other.formal_draft,
        )

    unique_channels = list(dict.fromkeys(all_channels))
    merged.channels = unique_channels
    merged.cross_channel_hits = len(unique_channels)
    merged.involved_signals = all_signals
    merged.involved_specs = all_specs
    merged.evidence = all_evidence[:10]
    merged.contradiction = best_contradiction
    merged.verdict = best_verdict
    merged.formal_verdict = best_formal_verdict
    merged.formal_confidence = best_formal_confidence
    merged.formal_draft = best_formal_draft
    # Self-ref is false if any involved spec came from a different source
    merged.is_self_ref = len(set(all_specs)) <= 1

    if merged.cross_channel_hits >= 2:
        merged.title = (
            f"[{'+'.join(unique_channels)}] {merged.title[:170]}"
        )

    return merged


def _extract_formal_summary(raw: dict[str, Any]) -> tuple[str, float]:
    verdict = str(raw.get("formal_verdict", "") or "").strip().upper()
    conf = raw.get("formal_confidence", 0.0)
    try:
        confidence = float(conf or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if verdict in {"DIRECT", "PARTIAL", "NONE"}:
        return verdict, round(max(0.0, min(confidence, 1.0)), 3)

    verdict, confidence = _formal_summary_from_items(raw)
    return verdict, confidence


def _formal_summary_from_items(raw: dict[str, Any]) -> tuple[str, float]:
    sketches: list[dict[str, Any]] = []
    assumption = raw.get("assumption")
    if isinstance(assumption, dict):
        sketch = assumption.get("formal_sketch")
        if isinstance(sketch, dict):
            sketches.append(sketch)
    for key in ("relevant_guarantees", "driver_guarantees"):
        for item in raw.get(key, []) or []:
            if isinstance(item, dict):
                sketch = item.get("formal_sketch")
                if isinstance(sketch, dict):
                    sketches.append(sketch)
    if not sketches:
        return "NONE", 0.0

    best = max(float(sketch.get("confidence", 0.0) or 0.0) for sketch in sketches)
    direct = any(str(sketch.get("formalizability", "")).lower() == "direct" for sketch in sketches)
    partial = any(str(sketch.get("formalizability", "")).lower() == "partial" for sketch in sketches)
    if direct:
        verdict = "DIRECT"
    elif partial:
        verdict = "PARTIAL"
    else:
        verdict = "NONE"
    return verdict, round(best, 3)


def _merge_formal_summary(
    current_verdict: str,
    current_confidence: float,
    current_draft: dict[str, Any],
    other_verdict: str,
    other_confidence: float,
    other_draft: dict[str, Any],
) -> tuple[str, float, dict[str, Any]]:
    def rank(verdict: str) -> int:
        v = verdict.upper()
        if v == "DIRECT":
            return 2
        if v == "PARTIAL":
            return 1
        return 0

    if rank(other_verdict) > rank(current_verdict) or (
        rank(other_verdict) == rank(current_verdict)
        and other_confidence > current_confidence
    ):
        return other_verdict, other_confidence, _merge_draft(current_draft, other_draft)
    return current_verdict, current_confidence, _merge_draft(current_draft, other_draft)


def _merge_draft(
    current: dict[str, Any],
    other: dict[str, Any],
) -> dict[str, Any]:
    if not current:
        return dict(other or {})
    if not other:
        return dict(current)
    merged = dict(current)
    for key, value in other.items():
        if key not in merged or not merged.get(key):
            merged[key] = value
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
