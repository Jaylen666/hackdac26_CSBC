"""
Phase 2: Cross-reference guarantees against assumptions via Z3.
"""

from __future__ import annotations

import re
from typing import Any

import z3

from csbc2.phase1 import FormalClause


def clause_to_z3(c: FormalClause, bv: dict[str, z3.BitVecRef]) -> z3.BoolRef | None:
    """Convert a FormalClause's (antecedent → consequent) into a Z3 formula.

    Returns None if the expressions can't be parsed.
    """
    ant = _parse_expr(c.antecedent, bv)
    cons = _parse_expr(c.consequent, bv)
    if ant is None or cons is None:
        return None
    # (condition → effect)
    return z3.Implies(ant, cons)


def check_pair(
    guarantee: FormalClause,
    assumption: FormalClause,
) -> tuple[str, dict[str, str] | None]:
    """Check G vs A for contradiction using Z3.

    Returns (verdict, model_or_None).
    Verdict: "CONTRADICTION" | "CONSISTENT" | "UNKNOWN"
    """
    bv: dict[str, z3.BitVecRef] = {}
    g_z3 = clause_to_z3(guarantee, bv)
    a_z3 = clause_to_z3(assumption, bv)

    if g_z3 is None or a_z3 is None:
        return "UNKNOWN", None

    solver = z3.Solver()
    solver.add(g_z3)
    solver.add(z3.Not(a_z3))

    r = solver.check()
    if r == z3.sat:
        m = solver.model()
        model = {}
        for name, var in bv.items():
            try:
                model[name] = str(m.eval(var, model_completion=True))
            except Exception:
                pass
        return "CONTRADICTION", model
    elif r == z3.unsat:
        return "CONSISTENT", None
    return "UNKNOWN", None


def pair_and_check(
    guarantees: list[FormalClause],
    assumptions: list[FormalClause],
) -> list[dict[str, Any]]:
    """Pair G and A by shared signal name, check with Z3.

    Returns findings in Phase 2 dict format.
    """
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()

    for g in guarantees:
        if not g.formalizable:
            continue
        for a in assumptions:
            if not a.formalizable:
                continue
            if g.signal != a.signal:
                continue

            key = f"{g.spec_id}:{g.signal}:{a.spec_id}:{a.signal}"
            if key in seen:
                continue
            seen.add(key)

            verdict, model = check_pair(g, a)
            if verdict == "CONTRADICTION":
                findings.append({
                    "finding_id": f"Z-{len(findings)+1:04d}",
                    "title": (
                        f"Z3 contradiction: '{g.spec_id}' G({g.signal}) "
                        f"vs '{a.spec_id}' A({a.signal})"
                    ),
                    "severity": "HIGH",
                    "channels": ["z3"],
                    "verdict": "CONTRADICTION",
                    "contradiction": (
                        f"G: ante=({g.antecedent}) → cons=({g.consequent})\n"
                        f"A: ante=({a.antecedent}) → cons=({a.consequent})\n"
                        f"Model: {model}"
                    ),
                    "involved_signals": [g.signal, a.signal],
                    "involved_specs": [g.spec_id, a.spec_id],
                    "evidence": [
                        {"spec": g.spec_id, "field": "guarantee", "excerpt": g.claim or g.consequent},
                        {"spec": a.spec_id, "field": "assumption", "excerpt": a.claim or a.consequent},
                    ],
                    "z3_model": model,
                })

    return findings


# ---------------------------------------------------------------------------
# Minimal SV expression parser for antecedent/consequent
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_\[\].]*"        # identifiers
    r"|\d*\'[sSyY]?[bBoOdDhH][0-9a-fA-FxXzZ_]+"  # SV literals
    r"|\b\d+\b"                              # plain numbers
    r"|==|!=|>=|<=|>|<|&&|\|\||!"           # operators
    r"|[()?:]"                               # ternary
)


def _parse_expr(text: str, bv: dict[str, z3.BitVecRef]) -> z3.BoolRef | None:
    """Parse an SV expression into a Z3 Boolean expression.

    Handles ===, !=, &&, ||, !, ternary (?:), numeric/SV literals.
    """
    tokens = _TOKEN_RE.findall(text)
    if not tokens:
        return None
    try:
        result, _ = _parse_or(tokens, 0, bv)
        return result
    except Exception:
        return None


def _parse_or(tokens: list[str], pos: int, bv: dict[str, z3.BitVecRef]) -> tuple[z3.BoolRef, int]:
    left, pos = _parse_and(tokens, pos, bv)
    while pos < len(tokens) and tokens[pos] == "||":
        pos += 1
        right, pos = _parse_and(tokens, pos, bv)
        left = z3.Or(left, right)
    return left, pos


def _parse_and(tokens: list[str], pos: int, bv: dict[str, z3.BitVecRef]) -> tuple[z3.BoolRef, int]:
    left, pos = _parse_cmp(tokens, pos, bv)
    while pos < len(tokens) and tokens[pos] == "&&":
        pos += 1
        right, pos = _parse_cmp(tokens, pos, bv)
        left = z3.And(left, right)
    return left, pos


def _parse_cmp(tokens: list[str], pos: int, bv: dict[str, z3.BitVecRef]) -> tuple[z3.BoolRef, int]:
    # Check for ternary: cond ? true_expr : false_expr
    cond_pos = pos
    cond, pos = _parse_primary(tokens, pos, bv)
    if pos < len(tokens) and tokens[pos] == "?":
        pos += 1  # skip ?
        true_val, pos = _parse_expr_ternary(tokens, pos, bv, "?: true branch")
        if pos < len(tokens) and tokens[pos] == ":":
            pos += 1  # skip :
            false_val, pos = _parse_expr_ternary(tokens, pos, bv, "?: false branch")
            # cond ? true_val : false_val — but need both as same type
            # For now, simplify: treat ternary as (cond & true_val) | (!cond & false_val)
            return z3.Or(z3.And(cond, true_val), z3.And(z3.Not(cond), false_val)), pos

    # No ternary — check for comparison
    if pos < len(tokens) and tokens[pos] in ("==", "!=", ">=", "<=", ">", "<"):
        op = tokens[pos]
        pos += 1
        rhs, pos = _parse_primary(tokens, pos, bv)
        if isinstance(cond, (z3.BitVecRef, z3.BitVecNumRef)) and isinstance(rhs, (z3.BitVecRef, z3.BitVecNumRef)):
            a, b = _align(cond, rhs)
            if op == "==":  return (a == b), pos
            if op == "!=":  return (a != b), pos
            if op == ">=":  return (a >= b), pos
            if op == "<=":  return (a <= b), pos
            if op == ">":   return (a > b), pos
            if op == "<":   return (a < b), pos
        return _to_bool(cond, bv), pos

    return _to_bool(cond, bv), pos


def _parse_expr_ternary(tokens, pos, bv, ctx):
    """Parse an expression inside a ternary (?:). Could be a primary or a comparison result."""
    left, pos = _parse_primary(tokens, pos, bv)
    if pos < len(tokens) and tokens[pos] in ("==", "!=", ">=", "<=", ">", "<"):
        op = tokens[pos]
        pos += 1
        rhs, pos = _parse_primary(tokens, pos, bv)
        if isinstance(left, (z3.BitVecRef, z3.BitVecNumRef)) and isinstance(rhs, (z3.BitVecRef, z3.BitVecNumRef)):
            a, b = _align(left, rhs)
            if op == "==":  return (a == b), pos
            if op == "!=":  return (a != b), pos
            return (a == b), pos
    return left, pos


def _parse_primary(tokens: list[str], pos: int, bv: dict[str, z3.BitVecRef]) -> tuple[z3.ExprRef, int]:
    if pos >= len(tokens):
        return _bv_val("0", bv), pos
    t = tokens[pos]
    if t == "(":
        expr, pos = _parse_or(tokens, pos + 1, bv)
        if pos < len(tokens) and tokens[pos] == ")":
            pos += 1
        return expr, pos
    if t in ("1", "1'b1", "true"):
        return z3.BoolVal(True), pos + 1
    if t in ("0", "1'b0", "false"):
        return z3.BoolVal(False), pos + 1
    if t == "!":
        arg, pos = _parse_primary(tokens, pos + 1, bv)
        return z3.Not(arg), pos
    # Identifier or literal
    val = _to_bv_val(t, bv)
    return val, pos + 1


def _to_bv_val(t: str, bv: dict[str, z3.BitVecRef]) -> z3.ExprRef:
    """Convert a token to a Z3 expression."""
    # SV literal
    m = re.match(r"(\d*)\'[sSyY]?[bBoOdDhH](.+)", t)
    if m:
        w = int(m.group(1)) if m.group(1) else 32
        s = m.group(2).replace("_", "")
        if not s or "x" in s.lower() or "z" in s.lower():
            return z3.BitVecVal(0, w)
        return z3.BitVecVal(int(s, {"b": 2, "o": 8, "d": 10, "h": 16}.get(t[0].lower(), 10)), w)
    # Number
    if t.isdigit():
        return z3.BitVecVal(int(t), 32)
    # Signal name — create or retrieve BitVec variable
    if t not in bv:
        bv[t] = z3.BitVec(t, 32)
    return bv[t]


def _to_bool(expr: z3.ExprRef, bv: dict[str, z3.BitVecRef]) -> z3.BoolRef:
    """Convert any expression to a BoolRef (BitVec → != 0)."""
    if isinstance(expr, z3.BoolRef):
        return expr
    if isinstance(expr, (z3.BitVecRef, z3.BitVecNumRef)):
        return expr != z3.BitVecVal(0, expr.size())
    return z3.BoolVal(bool(expr))


def _align(a: z3.BitVecRef, b: z3.BitVecRef) -> tuple[z3.BitVecRef, z3.BitVecRef]:
    if a.size() == b.size():
        return a, b
    if a.size() < b.size():
        return z3.ZeroExt(b.size() - a.size(), a), b
    return a, z3.ZeroExt(a.size() - b.size(), b)
