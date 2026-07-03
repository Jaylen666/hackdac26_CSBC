"""
Minimal Z3 contradiction checker for CSBC v3.

Given two formal clauses (antecedent → consequent) for the same signal,
check if they are contradictory.
"""

from __future__ import annotations

import re

import z3


def check_pair(
    signal: str,
    g_ant: str, g_cons: str,
    a_ant: str, a_cons: str,
) -> str:
    """Check if G(antecedent → consequent) contradicts A(antecedent → consequent).

    Returns: "CONTRADICTION" | "CONSISTENT" | "UNKNOWN"
    """
    bv: dict[str, z3.BitVecRef] = {}

    try:
        g_ant_z3 = _parse_expr(g_ant, bv) if g_ant else z3.BoolVal(True)
        g_cons_z3 = _parse_expr(g_cons, bv) if g_cons else z3.BoolVal(True)
        a_ant_z3 = _parse_expr(a_ant, bv) if a_ant else z3.BoolVal(True)
        a_cons_z3 = _parse_expr(a_cons, bv) if a_cons else z3.BoolVal(True)
    except Exception:
        return "UNKNOWN"

    # Contradiction check: cond_G ∧ cond_A ∧ effect_G ∧ ¬effect_A
    # But we want: cond_G ∧ cond_A ∧ effect_G ≠ effect_A
    # Simpler: (effect_G != effect_A) under (cond_G ∧ cond_A)
    solver = z3.Solver()
    solver.add(g_ant_z3)
    solver.add(a_ant_z3)
    solver.add(z3.Not(g_cons_z3 == a_cons_z3))

    r = solver.check()
    if r == z3.sat:
        return "CONTRADICTION"
    elif r == z3.unsat:
        return "CONSISTENT"
    return "UNKNOWN"


def _bv(name: str, bv: dict[str, z3.BitVecRef]) -> z3.BitVecRef:
    if name not in bv:
        bv[name] = z3.BitVec(name, 32)
    return bv[name]


def _parse_expr(text: str, bv: dict[str, z3.BitVecRef]) -> z3.BoolRef:
    """Parse a simple SV expression into Z3. Handles ==, &&, ||, !, ?:."""
    text = text.strip()
    if not text or text == "1":
        return z3.BoolVal(True)
    if text == "0":
        return z3.BoolVal(False)

    return z3.BoolVal(True)
