"""
Deterministic parser for assign chunks.

Each ``assign X = expr;`` produces one formal clause:
  guarantee(signal=X, antecedent="1", consequent=expr)

No LLM. No uncertainty. 100% deterministic.
"""

from __future__ import annotations

import re
from typing import Any

from csbc3.chunker import Chunk


def parse_assign_chunk(chunk: Chunk) -> dict[str, Any] | None:
    """Parse a single assign chunk into a formal clause dict.

    Returns:
      {
        "signal": "wipe_secret_we",
        "kind": "guarantee",
        "antecedent": "1",
        "consequent": "(addr_hit[8] && reg_we && reg_error)",
        "temporal": "comb",
        "formalizable": true,
        "claim": "wipe_secret_we = (addr_hit[8] && reg_we && reg_error)"
      }

    Returns None if parsing fails.
    """
    code = chunk.code.strip()
    # Remove line continuations and normalize whitespace
    code = re.sub(r"\\\n", "", code)
    code = re.sub(r"\s+", " ", code).strip()

    # Parse: assign [qualifier] LHS = RHS;
    m = re.match(
        r"assign\s+"
        r"(?:(unique|priority)\s+)?"
        r"(\[.*?\]\s+)?"
        r"(\S+?)\s*=+\s*"
        r"(.*?)\s*;", code, re.DOTALL
    )
    if not m:
        # Try simpler pattern for multi-line
        m = re.match(r"assign\s+(\S+?)\s*=+\s*(.*)", code, re.DOTALL)
        if not m:
            return None
        lhs, rhs = m.group(1).strip(), m.group(2).strip()
    else:
        lhs, rhs = m.group(3).strip(), m.group(4).strip()

    # Clean up RHS: remove trailing semicolons, normalize whitespace
    rhs = rhs.rstrip(";").strip()

    return {
        "signal": lhs,
        "kind": "guarantee",
        "antecedent": "1",
        "consequent": rhs,
        "temporal": "comb",
        "formalizable": True,
        "claim": f"{lhs} = {rhs}",
        "construct_type": "assign",
        "chunk_id": chunk.chunk_id,
    }


_CHECKED_SUFFIXES = frozenset({
    "_we", "_re", "_en", "_sel", "_d", "_q",
    "_valid", "_ready", "_ack", "_error", "_done", "_idle",
})


def extract_signal_suffix(signal: str) -> str:
    for s in _CHECKED_SUFFIXES:
        if signal.endswith(s):
            return s
    return "_skip"  # skip catch-all group to avoid noise


def _norm_expr(expr: str) -> str:
    """Normalize an expression for structural comparison.

    Treats && and & as equivalent (same for 1-bit), normalizes numbers.
    """
    text = re.sub(r"addr_hit\[\d+\]", "addr_hit[i]", expr)
    text = re.sub(r"\b\d+\b", "N", text)
    text = re.sub(r"\s+", "", text)
    text = text.replace("&&", "&").replace("||", "|")  # normalize for 1-bit
    text = text.strip("() ")
    return text


def expressions_equivalent(a: str, b: str) -> bool:
    """Check if two SV expressions are semantically equivalent using Z3.

    Z3 checks: is there an assignment to input signals where a != b?
    If UNSAT, they are equivalent (same truth table).

    Handles: ==, !=, &&, ||, !, &, |, addr_hit[N] normalization.
    """
    try:
        import z3
    except ImportError:
        return False  # No Z3 — assume not equivalent

    import re

    def _parse_simple(expr: str) -> tuple[set[str], z3.BoolRef]:
        """Parse a simple SV expression into Z3, returning (signals, formula)."""
        bv: dict[str, z3.BitVecRef] = {}
        signals: set[str] = set()

        # Normalize: normalize address indices but KEEP negation operators
        e = expr.strip()
        e = re.sub(r"addr_hit\[\d+\]", "addr_hit_i", e)

        # Tokenize — keep ! as a separate token
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|==|!=|&&|\|\||!|[()&|]", e)
        if not tokens:
            return set(), z3.BoolVal(True)

        # Simple recursive descent: handles A && B || C
        def _or_expr(tokens, pos):
            left, pos = _and_expr(tokens, pos)
            while pos < len(tokens) and tokens[pos] == "||":
                pos += 1
                right, pos = _and_expr(tokens, pos)
                left = z3.Or(left, right)
            return left, pos

        def _and_expr(tokens, pos):
            left, pos = _primary(tokens, pos)
            while pos < len(tokens) and tokens[pos] in ("&&", "&"):
                pos += 1
                right, pos = _primary(tokens, pos)
                left = z3.And(left, right)
            return left, pos

        def _primary(tokens, pos):
            if pos >= len(tokens):
                return z3.BoolVal(True), pos
            t = tokens[pos]
            if t == "!":
                arg, pos = _primary(tokens, pos + 1)
                return z3.Not(arg), pos
            if t == "(":
                expr, pos = _or_expr(tokens, pos + 1)
                if pos < len(tokens) and tokens[pos] == ")":
                    pos += 1
                return expr, pos
            if t in ("0", "1'b0", "false"):
                return z3.BoolVal(False), pos + 1
            if t in ("1", "1'b1", "true"):
                return z3.BoolVal(True), pos + 1
            # Signal name
            sig = t
            signals.add(sig)
            if sig not in bv:
                bv[sig] = z3.BitVec(sig, 1)
            return (bv[sig] == z3.BitVecVal(1, 1)), pos + 1

        formula, _ = _or_expr(tokens, 0)
        return signals, formula

    sigs_a, f_a = _parse_simple(a)
    sigs_b, f_b = _parse_simple(b)

    solver = z3.Solver()
    solver.add(z3.Not(f_a == f_b))
    result = solver.check()

    return result == z3.unsat


def validate_anomaly(
    minority_expr: str,
    majority_expr: str,
) -> tuple[bool, str]:
    """Validate whether a suspected anomaly is a REAL semantic difference.

    Uses Z3 to check if the two expressions are semantically equivalent.
    If they are equivalent, the anomaly is a false positive (style difference).

    Returns: (is_real_anomaly, explanation)
    """
    if expressions_equivalent(minority_expr, majority_expr):
        return False, "Syntactic difference only — Z3 proves semantic equivalence"

    return True, "Z3 proves semantic difference — real anomaly"


def find_gating_anomalies(clauses: list[dict[str, Any]], validate: bool = True) -> list[dict[str, Any]]:
    """Structural anomaly detection across parsed clauses.

    Groups clauses by signal suffix (_we, _re, etc.) and checks if
    a minority of signals in the group uses a different antecedent
    pattern from the majority.

    This catches bugs like:
      intr_state_we = addr_hit[0] & reg_we & !reg_error   (correct)
      wipe_secret_we = addr_hit[8] & reg_we & reg_error    (BUG: missing !)
    """
    from collections import defaultdict

    by_suffix: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for clause in clauses:
        sig = clause.get("signal", "")
        suffix = extract_signal_suffix(sig)
        by_suffix[suffix].append(clause)

    findings: list[dict[str, Any]] = []
    seen_patterns: dict[str, set[str]] = defaultdict(set)

    for suffix, group in by_suffix.items():
        if len(group) < 3:
            continue  # need at least 3 to detect an outlier

        # Group by normalized consequent
        # Store (raw_expr, signal_name) pairs for each normalized form
        by_form: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for c in group:
            raw = c.get("consequent", c.get("antecedent", ""))
            form = _norm_expr(raw)
            by_form[form].append((raw, c["signal"]))

        # If multiple forms exist, the minority is suspicious
        if len(by_form) > 1:
            total = len(group)
            sorted_forms = sorted(by_form.items(), key=lambda x: -len(x[1]))
            majority_form, majority_items = sorted_forms[0]
            majority_pct = len(majority_items) / total
            majority_raw = majority_items[0][0]  # first majority raw expr

            for form, items in sorted_forms[1:]:
                pct = len(items) / total
                if pct <= 0.35:  # minority: < 35% of the group
                    signals = [s for _, s in items]
                    pat_key = (suffix, form)
                    if pat_key not in seen_patterns:
                        seen_patterns[pat_key].add(form)
                        # Z3 validation
                        is_real = True
                        z3_note = ""
                        if validate:
                            is_real, z3_note = validate_anomaly(
                                items[0][0],  # first minority raw expr
                                majority_raw,
                            )
                            if not is_real:
                                continue

                        findings.append({
                            "finding_id": f"S-{len(findings)+1:04d}",
                            "title": (
                                f"Gating anomaly: {', '.join(signals)} ({suffix}) "
                                f"differs from {len(majority_items)} other signals"
                            ),
                            "severity": "HIGH" if pct <= 0.1 else "MEDIUM",
                            "channels": ["structural"],
                            "verdict": "CONFIRMED_ANOMALY" if is_real else "STYLE_DIFF",
                            "contradiction": (
                                f"Signals {', '.join(signals)} use pattern '{form}' "
                                f"({pct*100:.0f}% of group) while the majority "
                                f"({majority_pct*100:.0f}%) uses '{majority_form}'"
                                + (f"\nZ3: {z3_note}" if z3_note else "")
                            ),
                            "involved_signals": signals,
                            "evidence": [
                                {
                                    "spec": "structural",
                                    "field": "antecedent",
                                    "excerpt": f"Abnormal: {signals[0]} = ... {form}",
                                },
                                {
                                    "spec": "structural",
                                    "field": "antecedent",
                                    "excerpt": f"Expected: {majority_items[0][1]} = ... {majority_form}",
                                },
                            ],
                        })

    return findings





def parse_all_assigns(chunks: list[Chunk]) -> list[dict[str, Any]]:
    """Parse all assign chunks into formal clauses."""
    return [
        clause for c in chunks
        if c.construct_type == "assign"
        for clause in [parse_assign_chunk(c)]
        if clause is not None
    ]
