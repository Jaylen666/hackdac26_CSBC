from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rtl_bug_agent.spec.clause import (
    Clause,
    VALUE_OPERATORS,
    value_clause_contradicts,
    temporal_clause_contradicts,
)


# ---------------------------------------------------------------------------
# Structured finding — deterministic output from the clause engine
# ---------------------------------------------------------------------------


@dataclass
class ClauseFinding:
    """A finding produced by the clause engine without an LLM call."""
    kind: str  # CONTRADICTION | GAP | TEMPORAL_MISMATCH | UNMATCHED_SIGNAL
    signal: str
    clauses: list[tuple[str, str, str]]  # (spec_id, item_id, text_snippet)
    description: str
    certainty: float  # 0.0-1.0 — how sure the structural check is


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ClauseEngine:
    """Structural clause matching engine.

    Replaces LLM calls for clear-cut cases:
      - Direct value contradictions (X == 3 vs X == 5)
      - Coverage gaps (assumes state in {A,B} but only A is guaranteed)
      - Temporal mismatches (comb vs next_cycle)
      - Unmatched signals (assumption references signal no one drives)

    Only sends to LLM when the structural check is inconclusive.
    """

    def __init__(self, clauses: list[Clause]):
        self.clauses = clauses
        self._by_kind: dict[str, list[Clause]] = {}
        for c in clauses:
            self._by_kind.setdefault(c.kind, []).append(c)

        self._by_signal: dict[str, list[Clause]] = {}
        for c in clauses:
            sig = c.subject
            if sig:
                self._by_signal.setdefault(sig, []).append(c)
            for ref in c.signals:
                self._by_signal.setdefault(ref.name, []).append(c)

    @property
    def guarantees(self) -> list[Clause]:
        return self._by_kind.get("guarantee", [])

    @property
    def assumptions(self) -> list[Clause]:
        return self._by_kind.get("assumption", [])

    @property
    def uncertain_points(self) -> list[Clause]:
        return self._by_kind.get("uncertain", [])

    # ------------------------------------------------------------------
    # Contradiction detection
    # ------------------------------------------------------------------

    def find_contradictions(self) -> list[ClauseFinding]:
        """Find direct value contradictions between guarantees and assumptions."""
        findings: list[ClauseFinding] = []

        for sig, sig_clauses in self._by_signal.items():
            guarantees = [c for c in sig_clauses if c.kind == "guarantee"]
            assumptions = [c for c in sig_clauses if c.kind == "assumption"]

            for g in guarantees:
                for a in assumptions:
                    if value_clause_contradicts(g, a):
                        findings.append(ClauseFinding(
                            kind="CONTRADICTION",
                            signal=sig,
                            clauses=[
                                (g.spec_id, g.item_id, g.source_text[:120]),
                                (a.spec_id, a.item_id, a.source_text[:120]),
                            ],
                            description=(
                                f"Guarantee '{g.item_id}' ({g.operator} {g.operands}) "
                                f"contradicts assumption '{a.item_id}' "
                                f"({a.operator} {a.operands}) on signal {sig}"
                            ),
                            certainty=0.95,
                        ))
        return findings

    # ------------------------------------------------------------------
    # Temporal mismatch detection
    # ------------------------------------------------------------------

    def find_temporal_mismatches(self) -> list[ClauseFinding]:
        """Find clauses that conflict on timing for the same signal."""
        findings: list[ClauseFinding] = []

        for sig, sig_clauses in self._by_signal.items():
            for i, c1 in enumerate(sig_clauses):
                for c2 in sig_clauses[i + 1:]:
                    if c1.kind == c2.kind:
                        continue  # temporal mismatch is interesting across roles
                    if temporal_clause_contradicts(c1, c2):
                        findings.append(ClauseFinding(
                            kind="TEMPORAL_MISMATCH",
                            signal=sig,
                            clauses=[
                                (c1.spec_id, c1.item_id, c1.temporal),
                                (c2.spec_id, c2.item_id, c2.temporal),
                            ],
                            description=(
                                f"'{c1.item_id}' says {c1.temporal} "
                                f"but '{c2.item_id}' says {c2.temporal} "
                                f"on signal {sig}"
                            ),
                            certainty=0.85,
                        ))
        return findings

    # ------------------------------------------------------------------
    # Coverage gap detection
    # ------------------------------------------------------------------

    def find_coverage_gaps(self) -> list[ClauseFinding]:
        """Find assumptions about signal values not covered by guarantees."""
        findings: list[ClauseFinding] = []

        for sig, sig_clauses in self._by_signal.items():
            gs = [c for c in sig_clauses if c.kind == "guarantee"]
            assumptions = [c for c in sig_clauses if c.kind == "assumption"]

            if not gs or not assumptions:
                continue

            for a in assumptions:
                if a.operator not in VALUE_OPERATORS:
                    continue
                covered = False
                for g in gs:
                    if g.operator not in VALUE_OPERATORS:
                        continue
                    if a.subject != g.subject:
                        continue
                    if a.operator == g.operator == "in_set":
                        if set(a.operands).issubset(set(g.operands)):
                            covered = True
                            break
                    elif a.operator == "==" and g.operator == "in_set":
                        if a.operands[0] in g.operands:
                            covered = True
                            break
                    elif a.operator == g.operator == "==":
                        if a.operands == g.operands:
                            covered = True
                            break

                if not covered:
                    findings.append(ClauseFinding(
                        kind="GAP",
                        signal=sig,
                        clauses=[(a.spec_id, a.item_id, a.source_text[:120])],
                        description=(
                            f"Assumption '{a.item_id}' expects "
                            f"{a.subject} {a.operator} {a.operands} "
                            f"but no guarantee covers this constraint"
                        ),
                        certainty=0.80,
                    ))

        return findings

    # ------------------------------------------------------------------
    # Unmatched assumption signals
    # ------------------------------------------------------------------

    def find_unmatched_signals(self) -> list[ClauseFinding]:
        """Find assumptions referencing signals that no guarantee drives.

        This is a key structural check: if an assumption says 'signal X
        must be < 5' but no guarantee clause mentions X, there is a
        behavioral contract gap.
        """
        findings: list[ClauseFinding] = []
        driven_signals: set[str] = set()
        for g in self.guarantees:
            driven_signals.add(g.subject)
            for ref in g.signals:
                driven_signals.add(ref.name)

        for a in self.assumptions:
            a_signals = {a.subject} | {ref.name for ref in a.signals}
            unmatched = a_signals - driven_signals
            if unmatched:
                findings.append(ClauseFinding(
                    kind="UNMATCHED_SIGNAL",
                    signal=list(unmatched)[0],
                    clauses=[(a.spec_id, a.item_id, a.source_text[:120])],
                    description=(
                        f"Assumption '{a.item_id}' references signal(s) "
                        f"{', '.join(sorted(unmatched))} "
                        f"not covered by any guarantee"
                    ),
                    certainty=0.90,
                ))
        return findings

    # ------------------------------------------------------------------
    # Batch: run all checks
    # ------------------------------------------------------------------

    def run_all(self) -> list[ClauseFinding]:
        raw: list[ClauseFinding] = []
        raw.extend(self.find_contradictions())
        raw.extend(self.find_temporal_mismatches())
        raw.extend(self.find_coverage_gaps())
        raw.extend(self.find_unmatched_signals())
        # Deduplicate by (kind, signal, clause_ids)
        seen: set[str] = set()
        deduped: list[ClauseFinding] = []
        for f in raw:
            key = f"{f.kind}|{f.signal}|{'|'.join(sorted(sid for sid, _, _ in f.clauses))}"
            if key not in seen:
                seen.add(key)
                deduped.append(f)
        return deduped


# ---------------------------------------------------------------------------
# Integration with the rest of the pipeline
# ---------------------------------------------------------------------------


def findings_to_dicts(findings: list[ClauseFinding]) -> list[dict[str, Any]]:
    """Convert ClauseFindings to dicts for the existing fusion pipeline."""
    out: list[dict[str, Any]] = []
    for i, f in enumerate(findings):
        out.append({
            "finding_id": f"C-{i + 1:04d}",
            "title": f.description[:200],
            "severity": "HIGH" if f.certainty >= 0.90 else "MEDIUM",
            "channels": ["clause_engine"],
            "verdict": f.kind,
            "contradiction": f.description[:500],
            "involved_signals": [f.signal],
            "involved_specs": [s_id for s_id, _, _ in f.clauses],
            "evidence": [
                {"spec": s_id, "field": item_id, "excerpt": text[:200]}
                for s_id, item_id, text in f.clauses
            ],
            "formal_verdict": "DIRECT" if f.certainty >= 0.90 else "PARTIAL",
            "formal_confidence": f.certainty,
            "_source": "clause_engine",
        })
    return out
