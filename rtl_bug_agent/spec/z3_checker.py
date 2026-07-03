"""
Z3 Contradiction Checker
========================

Given two Clauses (a guarantee and an assumption on the same signal),
encode both as Z3 formulas and check:

    cond_G ∧ cond_A ∧ effect_G ∧ ¬effect_A

If SAT → CONTRADICTION (with counterexample model)
If UNSAT → No contradiction (consistent)

This replaces the LLM call for all direct structural contradictions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import z3

from rtl_bug_agent.spec.clause import Clause
from rtl_bug_agent.spec.z3_clause import ExprBuilder, clause_to_z3


@dataclass
class ContradictionResult:
    verdict: str  # CONTRADICTION | CONSISTENT | UNKNOWN
    certainty: float
    model: dict[str, str] = field(default_factory=dict)
    error: str = ""


def check_pair(
    guarantee: Clause,
    assumption: Clause,
    signal_widths: dict[str, int] | None = None,
) -> ContradictionResult:
    """Check a guarantee-assumption pair for contradiction using Z3.

    Returns CONTRADICTION if the solver finds a satisfying assignment
    where the guarantee holds but the assumption is violated.
    Returns CONSISTENT if the formulas are mutually exclusive or compatible.
    Returns UNKNOWN if the clauses can't be encoded (prose-only).
    """
    builder = ExprBuilder(signal_widths)

    # --- Encode both clauses as Z3 formulas ---
    g_z3 = clause_to_z3(guarantee, builder)
    a_z3 = clause_to_z3(assumption, builder)

    if g_z3 is None or a_z3 is None:
        return ContradictionResult(verdict="UNKNOWN", certainty=0.0,
                                    error="clause encoding failed")

    # --- Build the contradiction check: cond_G ∧ cond_A ∧ effect_G ∧ ¬effect_A ---
    solver = z3.Solver()

    # Add the guarantee effect and condition
    solver.add(g_z3)

    # Negate the assumption effect (but keep its condition):
    # We want: effect_G ∧ ¬effect_A (under their respective conditions)
    # The clause is (cond → effect), so we need cond_A ∧ ¬effect_A
    if assumption.condition:
        cond_a = builder.parse_expr(assumption.condition)
        if cond_a is not None:
            solver.add(cond_a)
    solver.add(z3.Not(a_z3))

    result = solver.check()

    if result == z3.sat:
        return ContradictionResult(
            verdict="CONTRADICTION",
            certainty=0.95,
            model=builder.extract_model(solver.model()),
        )
    elif result == z3.unsat:
        return ContradictionResult(verdict="CONSISTENT", certainty=0.90)
    else:
        return ContradictionResult(verdict="UNKNOWN", certainty=0.0,
                                    error=f"solver returned {result}")


def check_all_pairs(
    clauses: list[Clause],
    signal_widths: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Check all guarantee-assumption pairs across all clauses.

    Pairs are formed by matching G's *subject* (the signal it drives)
    against A's *subject* (the signal the assumption constrains).

    This is the core CSBC primitive: two chunks make claims about the
    same signal.  If the claims are contradictory given their respective
    conditions, Z3 finds a model.

    Returns findings in the same dict format as Phase 2 channels.
    """
    guarantees = [c for c in clauses if c.kind == "guarantee"]
    assumptions = [c for c in clauses if c.kind == "assumption"]

    findings: list[dict[str, Any]] = []
    seen: set[str] = set()

    for g in guarantees:
        if not g.subject:
            continue
        for a in assumptions:
            if not a.subject:
                continue

            # Only pair when they constrain the same signal (the CSBC primitive)
            if g.subject != a.subject:
                continue

            # Skip if neither clause has a concrete operator (both are "assignment")
            # — these are prose clauses with no formal content.
            if g.operator == "assignment" and a.operator == "assignment":
                continue

            # Dedup by (spec_id, item_id) pairs
            key = f"{g.spec_id}:{g.item_id}|{a.spec_id}:{a.item_id}"
            if key in seen:
                continue
            seen.add(key)

            result = check_pair(g, a, signal_widths)
            if result.verdict == "CONTRADICTION":
                findings.append({
                    "finding_id": f"Z3-{len(findings) + 1:04d}",
                    "title": (
                        f"Z3 contradiction: {g.kind} '{g.item_id}' "
                        f"({g.subject} {g.operator} {g.operands})  vs  "
                        f"{a.kind} '{a.item_id}' "
                        f"({a.subject} {a.operator} {a.operands})"
                    ),
                    "severity": "HIGH",
                    "channels": ["z3"],
                    "verdict": "CONTRADICTION",
                    "contradiction": (
                        f"Z3 proved contradiction: {g.kind} '{g.item_id}' "
                        f"({g.subject} {g.operator} {g.operands}) "
                        f"vs {a.kind} '{a.item_id}' "
                        f"({a.subject} {a.operator} {a.operands})"
                    ),
                    "involved_signals": [g.subject, a.subject],
                    "involved_specs": [g.spec_id, a.spec_id],
                    "evidence": [
                        {"spec": g.spec_id, "field": g.item_id,
                         "excerpt": g.source_text[:200]},
                        {"spec": a.spec_id, "field": a.item_id,
                         "excerpt": a.source_text[:200]},
                    ],
                    "z3_model": result.model,
                    "_source": "z3",
                })

    return findings



