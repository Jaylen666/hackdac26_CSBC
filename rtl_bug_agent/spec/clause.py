from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SignalRef:
    """Typed reference to a signal in a clause."""
    name: str
    width: int = 1
    kind: str = "unknown"  # wire, reg, logic, interface


@dataclass
class Clause:
    """Normalized structured clause — the core IR of the clauses-format spec.

    Each Clause represents one atomic behavioral constraint extracted from RTL.
    Instead of freeform natural language, it encodes the constraint as
    (subject operator [operands...]) with temporal qualification, enabling
    structural matching without an LLM call.

    The clause format is inspired by SMT-LIB / SVA: a clause is a
    machine-analyzable constraint, not prose.
    """

    # Identity
    kind: str  # guarantee | assumption | uncertain
    spec_id: str
    item_id: str  # G1, A2, U3 within the spec

    # Core constraint:  subject operator operands
    # e.g.  key_valid == 1'b1
    #       state in_set {IDLE, ACTIVE}
    #       data_out stable after next_cycle
    subject: str               # primary signal being constrained
    operator: str              # ==, !=, <, >, <=, >=, in_set, not_in_set,
                               # stable, rose, fell, changed, onehot,
                               # isunknown, countones, assignment
    operands: list[str] = field(default_factory=list)

    # Signals referenced in this clause (for cross-referencing)
    signals: list[SignalRef] = field(default_factory=list)

    # Temporal shape
    temporal: str = "always"   # comb, next_cycle, always, eventually, never

    # Guard / antecedent (condition under which this clause applies)
    condition: str | None = None
    cond_signals: list[str] = field(default_factory=list)

    # Formalizability
    formalizability: str = "partial"  # direct, partial, none
    formal_confidence: float = 0.0

    # Original source text (for LLM fallback)
    source_text: str = ""

    # Synthesis auxiliary
    clock: str = ""
    reset: str = ""

    # Provenance
    source_file: str = ""
    line_start: int = 0
    line_end: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "spec_id": self.spec_id,
            "item_id": self.item_id,
            "subject": self.subject,
            "operator": self.operator,
            "operands": list(self.operands),
            "signals": [s.name for s in self.signals],
            "temporal": self.temporal,
            "condition": self.condition,
            "formalizability": self.formalizability,
            "formal_confidence": round(self.formal_confidence, 3),
            "source_text": self.source_text[:200],
            "clock": self.clock,
            "reset": self.reset,
        }


# ---------------------------------------------------------------------------
# Operator semantics — used for structural contradiction detection
# ---------------------------------------------------------------------------

# Operators that constrain a signal to a specific value / set
VALUE_OPERATORS = frozenset({"==", "!=", "in_set", "not_in_set", "<", ">", "<=", ">="})

# Operators that describe signal transitions
EDGE_OPERATORS = frozenset({"rose", "fell", "changed", "stable"})

# Operators that describe properties of a multi-bit value
PROPERTY_OPERATORS = frozenset({"onehot", "onehot0", "isunknown", "countones", "assignment"})

# Operators that are contradictory when they disagree on the same subject
CONTRADICTORY_PAIRS: list[tuple[str, str]] = [
    ("==", "!="),
    ("==", "<"),
    ("==", ">"),
    ("in_set", "not_in_set"),
    ("stable", "changed"),
    ("rose", "stable"),
    ("fell", "stable"),
]

# Temporal pairs that are incompatible
TEMPORAL_CONTRADICTIONS: list[tuple[str, str]] = [
    ("comb", "next_cycle"),
    ("always", "never"),
]


def clause_signals_match(a: Clause, b: Clause) -> bool:
    """Return True if both clauses reference a common signal."""
    a_sigs = {s.name for s in a.signals}
    a_sigs.add(a.subject)
    b_sigs = {s.name for s in b.signals}
    b_sigs.add(b.subject)
    return bool(a_sigs & b_sigs)


def value_clause_contradicts(a: Clause, b: Clause) -> bool:
    """Check if two value clauses on the same signal contradict.

    Both clauses must share the same subject signal.
    """
    if a.subject != b.subject:
        return False

    # Direct operator contradiction
    for op1, op2 in CONTRADICTORY_PAIRS:
        if (a.operator == op1 and b.operator == op2) or \
           (a.operator == op2 and b.operator == op1):
            return True

    # Same operator, different operands
    if a.operator == b.operator and a.operator in VALUE_OPERATORS:
        if a.operands and b.operands:
            a_set = set(a.operands)
            b_set = set(b.operands)
            if a.operator == "in_set":
                # One says "must be in {X}" and other says "must be in {Y}"
                # If there is no overlap, it's a contradiction
                if a_set.isdisjoint(b_set):
                    return True
            elif a.operator == "not_in_set":
                # Both exclude a value: not contradictory
                return False
            elif a.operator in ("==", "!="):
                a_val = a_set
                b_val = b_set
                if a_val and b_val and a_val != b_val:
                    return True

    # == in one vs in_set (negation check)
    if a.operator == "in_set" and b.operator == "not_in_set":
        for val in a.operands:
            if val in b.operands:
                return True  # value required AND excluded
    if b.operator == "in_set" and a.operator == "not_in_set":
        for val in b.operands:
            if val in a.operands:
                return True

    return False


def temporal_clause_contradicts(a: Clause, b: Clause) -> bool:
    """Check if two clauses have incompatible temporal shapes."""
    for t1, t2 in TEMPORAL_CONTRADICTIONS:
        if (a.temporal == t1 and b.temporal == t2) or \
           (a.temporal == t2 and b.temporal == t1):
            return True
    return False


def clause_coverage_gap(
    assumptions: list[Clause],
    guarantees: list[Clause],
) -> list[tuple[Clause, str]]:
    """Find assumptions for which no guarantee provides coverage.

    Returns list of (unmatched_assumption, reason) tuples.
    """
    gaps: list[tuple[Clause, str]] = []
    for a in assumptions:
        matched = False
        for g in guarantees:
            if clause_signals_match(a, g):
                if a.subject == g.subject:
                    if a.operator == g.operator == "in_set":
                        g_vals = set(g.operands)
                        a_vals = set(a.operands)
                        if a_vals.issubset(g_vals):
                            matched = True
                            break
                    elif a.operator == g.operator and a.operator in ("==", "!="):
                        if a.operands == g.operands:
                            matched = True
                            break
                    elif g.operator == "in_set" and a.operator == "==":
                        if a.operands[0] in g.operands:
                            matched = True
                            break
                    elif a.operator == "in_set" and g.operator == "==":
                        if g.operands[0] in a.operands:
                            matched = True
                            break
                else:
                    # Different subjects but same signal overlap — weak match
                    matched = True
                    break
        if not matched:
            gaps.append((a, "no guarantee covers this assumption"))
    return gaps
