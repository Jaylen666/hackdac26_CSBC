"""
Z3 Clause Encoder
==================

Translates structured Clauses into Z3 bitvector expressions for
deterministic contradiction checking.

The encoding scheme:
  - Each signal is a Z3 BitVec variable (width from structural facts; default 32)
  - Symbolic constants (e.g. SHA2_256, StIdle) are fresh BitVec variables
    of the same width as the signal they compare against
  - A clause ``G: (cond_G → effect_G)`` becomes:
        Implies(cond_G_expr, effect_G_expr)
  - A contradiction check between G and A:
        z3.solve(cond_G ∧ cond_A ∧ effect_G ∧ ¬effect_A)

The solver never hallucinates — it produces a concrete model when SAT
or reports UNSAT when the specs are consistent.
"""

from __future__ import annotations

import re
from typing import Any

import z3

from rtl_bug_agent.spec.clause import Clause

# ---------------------------------------------------------------------------
# Expression parsing — turn SV-like text into Z3 ASTs
# ---------------------------------------------------------------------------

# Simple SV expression tokenizer: signals, numbers, operators, parens
_SV_TOKEN = re.compile(
    r"[A-Za-z_][A-Za-z0-9_\[\].]*"   # identifiers
    r"|\d+[sSyY]?\'[bBoOdDhH][0-9a-fA-FxXzZ_]+"  # SV literals
    r"|\b\d+\b"                       # plain numbers
    r"|==|!=|>=|<=|>|<|&&|\|\||!"    # operators
    r"|[(){}[\].,;]"                  # brackets/punctuation
)


class ExprBuilder:
    """Build Z3 expressions from SV-like text.

    Maintains a symbol table of signal names → Z3 BitVec variables.
    Symbolic constants (ALL_CAPS names not in the signal table) are
    allocated as fresh variables.
    """

    def __init__(self, signal_widths: dict[str, int] | None = None):
        self._sigs: dict[str, z3.BitVecRef] = {}
        self._consts: dict[str, z3.BitVecRef] = {}
        self._widths: dict[str, int] = dict(signal_widths or {})
        self._default_width = 32

    def _bv(self, name: str) -> z3.BitVecRef:
        if name in self._sigs:
            return self._sigs[name]
        w = self._widths.get(name, self._default_width)
        v = z3.BitVec(name, w)
        self._sigs[name] = v
        return v

    def _const(self, name: str, width: int = 32) -> z3.BitVecRef:
        if name not in self._consts:
            self._consts[name] = z3.BitVec(f"sym_{name}", width)
        return self._consts[name]

    def parse_expr(self, text: str) -> z3.BoolRef | z3.BitVecRef | None:
        """Parse an SV-like boolean expression into a Z3 AST.

        Handles: ==, !=, &&, ||, !, parens, numeric/SV literals,
        signal names, and symbolic constants.

        Returns None if the expression is too ambiguous (prose-like).
        """
        tokens = self._tokenize(text)
        if not tokens:
            return None
        try:
            ast = self._parse_or(tokens, 0)
            return ast[0] if ast else None
        except (ValueError, IndexError, KeyError, TypeError):
            return None

    # ------------------------------------------------------------------
    # Recursive descent parser for SV boolean expressions
    # Grammar:
    #   or_expr  := and_expr ('||' and_expr)*
    #   and_expr := cmp_expr ('&&' cmp_expr)*
    #   cmp_expr := ('!')? primary (('=='|'!='|'>='|'<='|'>'|'<') primary)?
    #   primary  := '(' or_expr ')' | signal | literal
    # ------------------------------------------------------------------

    def _parse_or(self, tokens: list[str], pos: int) -> tuple[z3.BoolRef, int] | None:
        left, pos = self._parse_and(tokens, pos) or (None, pos)
        if left is None:
            return None
        while pos < len(tokens) and tokens[pos] == "||":
            pos += 1
            right, pos = self._parse_and(tokens, pos)
            if right is None:
                break
            left = z3.Or(left, right)
        return left, pos

    def _parse_and(self, tokens: list[str], pos: int) -> tuple[z3.BoolRef, int] | None:
        left, pos = self._parse_cmp(tokens, pos) or (None, pos)
        if left is None:
            return None
        while pos < len(tokens) and tokens[pos] == "&&":
            pos += 1
            right, pos = self._parse_cmp(tokens, pos)
            if right is None:
                break
            left = z3.And(left, right)
        return left, pos

    def _parse_cmp(self, tokens: list[str], pos: int) -> tuple[z3.BoolRef, int] | None:
        negate = False
        if pos < len(tokens) and tokens[pos] == "!":
            negate = True
            pos += 1
        prim, pos = self._parse_primary(tokens, pos)
        if prim is None:
            return None
        if negate:
            if isinstance(prim, z3.BoolRef):
                prim = z3.Not(prim)
            else:
                prim = (prim == 0)  # bitvector ! → compare to zero
        # Check for comparison operator
        if pos < len(tokens) and tokens[pos] in ("==", "!=", ">=", "<=", ">", "<"):
            op = tokens[pos]
            pos += 1
            rhs, pos2 = self._parse_primary(tokens, pos)
            if rhs is None:
                return prim, pos  # no RHS — return the primary as-is
            pos = pos2
            if isinstance(prim, z3.BitVecRef) and isinstance(rhs, z3.BitVecRef):
                # Make widths match
                prim, rhs = self._align_widths(prim, rhs)
                if op == "==":   return (prim == rhs), pos
                elif op == "!=": return (prim != rhs), pos
                elif op == ">=": return (prim >= rhs), pos
                elif op == "<=": return (prim <= rhs), pos
                elif op == ">":  return (prim > rhs), pos
                elif op == "<":  return (prim < rhs), pos
                else:
                    return prim, pos
            elif isinstance(prim, z3.BoolRef) and isinstance(rhs, z3.BoolRef):
                if op == "==": return (prim == rhs), pos
                elif op == "!=": return (prim != rhs), pos
                else:
                    return prim, pos
        # No comparison operator — interpret the expression as a boolean.
        # For symbolic BitVec variables, create a non-zero check.
        # For concrete values (BitVecVal), check if non-zero to produce a Z3 BoolRef.
        if isinstance(prim, z3.BitVecRef):
            return prim != z3.BitVecVal(0, prim.size()), pos
        return prim, pos

    def _parse_primary(self, tokens: list[str], pos: int) -> tuple[z3.ExprRef, int] | None:
        if pos >= len(tokens):
            return None
        t = tokens[pos]
        if t == "(":
            expr, pos = self._parse_or(tokens, pos + 1)
            if expr is None:
                return None
            if pos < len(tokens) and tokens[pos] == ")":
                pos += 1
            return expr, pos
        if t in ("true", "TRUE", "1'b1"):
            return z3.BoolVal(True), pos + 1
        if t in ("false", "FALSE", "1'b0"):
            return z3.BoolVal(False), pos + 1
        # Signal or constant
        val = self._parse_value(t, tokens, pos)
        if val is not None:
            return val, pos + 1
        return None

    def _parse_value(self, t: str, tokens: list[str], pos: int) -> z3.ExprRef | None:
        """Parse a single value token (SV literal, number, signal, or constant)."""
        # SV literal: 8'd0, 1'b1, 'b0, 32'hff, etc.
        m = re.match(r"(\d+)[sSyY]?\'[bBoOdDhH]([0-9a-fA-FxXzZ_]+)", t)
        if m:
            width = int(m.group(1))
            val_str = m.group(2)
            val_int = _sv_val_to_int(val_str)
            return z3.BitVecVal(val_int, width) if val_int is not None else None

        # SV literal without size: 'b0, 'hff, 'd5
        m = re.match(r"\'[bBoOdDhH]([0-9a-fA-FxXzZ_]+)", t)
        if m:
            val_str = m.group(1)
            val_int = _sv_val_to_int(val_str)
            return z3.BitVecVal(val_int, self._default_width) if val_int is not None else None

        # Plain number
        m = re.match(r"\d+", t)
        if m:
            return z3.BitVecVal(int(m.group(0)), self._default_width)

        # Signal name: starts with lowercase/underscore (RTL convention)
        if t[0].islower() or t[0] == "_":
            return self._bv(t)

        # ALL_CAPS or CamelCase — could be a symbolic constant or a signal
        # If it contains a dot or bracket, it's likely a signal
        if "." in t or "[" in t:
            return self._bv(t)
        # Treat as a symbolic constant
        w = self._widths.get(t, self._default_width)
        return self._const(t, w)

    def _align_widths(self, a: z3.BitVecRef, b: z3.BitVecRef) -> tuple[z3.BitVecRef, z3.BitVecRef]:
        """Zero-extend the narrower bitvector to match the wider one."""
        w_a = a.size()
        w_b = b.size()
        if w_a == w_b:
            return a, b
        if w_a < w_b:
            return z3.ZeroExt(w_b - w_a, a), b
        return a, z3.ZeroExt(w_a - w_b, b)

    def _tokenize(self, text: str) -> list[str]:
        if not text:
            return []
        return _SV_TOKEN.findall(text)

    def extract_model(self, model: z3.ModelRef) -> dict[str, str]:
        """Extract signal values from a Z3 model."""
        out: dict[str, str] = {}
        for name, var in self._sigs.items():
            try:
                val = model.eval(var, model_completion=True)
                out[name] = str(val)
            except Exception:
                pass
        for name, var in self._consts.items():
            try:
                val = model.eval(var, model_completion=True)
                out[name] = str(val)
            except Exception:
                pass
        return out


# ---------------------------------------------------------------------------
# Clause → Z3 formula
# ---------------------------------------------------------------------------


def clause_to_z3(
    clause: Clause,
    builder: ExprBuilder,
) -> z3.ExprRef | None:
    """Convert a Clause into a Z3 expression: (condition → effect).

    Returns None if neither the condition nor the effect can be parsed.
    """
    # Build the effect expression: subject operator operands
    effect = _build_effect(clause, builder)
    if effect is None:
        return None

    # Build the condition (antecedent)
    cond_expr = None
    if clause.condition:
        cond_expr = builder.parse_expr(clause.condition)

    if cond_expr is not None:
        return z3.Implies(cond_expr, effect)
    # No condition: the effect should always hold
    return effect


def _parse_literal(text: str) -> z3.BitVecVal | None:
    """Parse an SV literal into a Z3 BitVecVal."""
    if not text:
        return None
    # Sized literal: 8'd0, 1'b1, 32'hff
    m = re.match(r"(\d+)[sSyY]?\'[bBoOdDhH]([0-9a-fA-FxXzZ_]+)", text)
    if m:
        w = int(m.group(1))
        v = _sv_val_to_int(m.group(2))
        return z3.BitVecVal(v, w) if v is not None else None
    # Unsized literal: 'b0, 'hff, 'd5
    m = re.match(r"\'[bBoOdDhH]([0-9a-fA-FxXzZ_]+)", text)
    if m:
        v = _sv_val_to_int(m.group(1))
        return z3.BitVecVal(v, 32) if v is not None else None
    # Plain number
    m = re.match(r"\d+", text)
    if m:
        return z3.BitVecVal(int(m.group(0)), 32)
    return None


def _build_effect(clause: Clause, builder: ExprBuilder) -> z3.BoolRef | None:
    """Build the effect expression for a clause: subject OP operands."""
    if not clause.subject:
        return None

    subj = builder._bv(clause.subject)
    op = clause.operator
    ops = clause.operands

    # Try to parse operand as a literal first (avoids eager evaluation)
    rhs_bv = _parse_literal(ops[0]) if ops else None

    if op == "==":
        if rhs_bv is not None:
            subj, rhs_bv = builder._align_widths(subj, rhs_bv)
            return subj == rhs_bv
        if ops:
            rhs = _resolve_operand(ops[0], builder, subj.size())
            if isinstance(rhs, z3.BitVecRef):
                subj, rhs = builder._align_widths(subj, rhs)
                return subj == rhs
        return subj != 0

    if op == "!=":
        if rhs_bv is not None:
            subj, rhs_bv = builder._align_widths(subj, rhs_bv)
            return subj != rhs_bv
        if ops:
            rhs = _resolve_operand(ops[0], builder, subj.size())
            if isinstance(rhs, z3.BitVecRef):
                subj, rhs = builder._align_widths(subj, rhs)
                return subj != rhs
        return subj == 0

    if op == "<":
        rhs_bv = _parse_literal(ops[0]) if ops else z3.BitVecVal(0, subj.size())
        if isinstance(rhs_bv, z3.BitVecRef):
            subj, rhs_bv = builder._align_widths(subj, rhs_bv)
            return subj < rhs_bv
        if ops:
            rhs = _resolve_operand(ops[0], builder, subj.size())
            if isinstance(rhs, z3.BitVecRef):
                subj, rhs = builder._align_widths(subj, rhs)
                return subj < rhs
        return None

    if op == ">":
        rhs_bv = _parse_literal(ops[0]) if ops else z3.BitVecVal(0, subj.size())
        if isinstance(rhs_bv, z3.BitVecRef):
            subj, rhs_bv = builder._align_widths(subj, rhs_bv)
            return subj > rhs_bv
        if ops:
            rhs = _resolve_operand(ops[0], builder, subj.size())
            if isinstance(rhs, z3.BitVecRef):
                subj, rhs = builder._align_widths(subj, rhs)
                return subj > rhs
        return None

    if op == "<=":
        rhs_bv = _parse_literal(ops[0]) if ops else z3.BitVecVal(0, subj.size())
        if isinstance(rhs_bv, z3.BitVecRef):
            subj, rhs_bv = builder._align_widths(subj, rhs_bv)
            return subj <= rhs_bv
        if ops:
            rhs = _resolve_operand(ops[0], builder, subj.size())
            if isinstance(rhs, z3.BitVecRef):
                subj, rhs = builder._align_widths(subj, rhs)
                return subj <= rhs
        return None

    if op == ">=":
        rhs_bv = _parse_literal(ops[0]) if ops else z3.BitVecVal(0, subj.size())
        if isinstance(rhs_bv, z3.BitVecRef):
            subj, rhs_bv = builder._align_widths(subj, rhs_bv)
            return subj >= rhs_bv
        if ops:
            rhs = _resolve_operand(ops[0], builder, subj.size())
            if isinstance(rhs, z3.BitVecRef):
                subj, rhs = builder._align_widths(subj, rhs)
                return subj >= rhs
        return None

    if op == "in_set":
        if not ops:
            return None
        disjuncts = []
        for val in ops:
            rhs_bv = _parse_literal(val)
            if rhs_bv is not None:
                subj_a, rhs_a = builder._align_widths(subj, rhs_bv)
                disjuncts.append(subj_a == rhs_a)
            else:
                rhs = _resolve_operand(val, builder, subj.size())
                if isinstance(rhs, z3.BitVecRef):
                    subj_a, rhs_a = builder._align_widths(subj, rhs)
                    disjuncts.append(subj_a == rhs_a)
        if not disjuncts:
            return None
        return z3.Or(*disjuncts) if len(disjuncts) > 1 else disjuncts[0]

    if op == "not_in_set":
        if not ops:
            return None
        conjuncts = []
        for val in ops:
            rhs_bv = _parse_literal(val)
            if rhs_bv is not None:
                subj_a, rhs_a = builder._align_widths(subj, rhs_bv)
                conjuncts.append(subj_a != rhs_a)
            else:
                rhs = _resolve_operand(val, builder, subj.size())
                if isinstance(rhs, z3.BitVecRef):
                    subj_a, rhs_a = builder._align_widths(subj, rhs)
                    conjuncts.append(subj_a != rhs_a)
        if not conjuncts:
            return None
        return z3.And(*conjuncts) if len(conjuncts) > 1 else conjuncts[0]

    # assignment
    return subj != 0  # weakest constraint: signal is non-zero


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_operand(text: str, builder: ExprBuilder, width: int) -> z3.BitVecRef | None:
    """Resolve a non-literal operand (symbolic constant or signal name) to Z3 BitVec."""
    if not text:
        return None
    # Symbolic constant or signal name
    if text[0].islower() or text[0] == "_":
        return builder._bv(text)
    # Symbolic constant (ALL_CAPS or CamelCase)
    w = builder._widths.get(text, width)
    return builder._const(text, w)


def _sv_val_to_int(val_str: str) -> int | None:
    """Convert an SV literal value string to an integer."""
    val_str = val_str.replace("_", "")
    if not val_str or "x" in val_str.lower() or "z" in val_str.lower():
        return None
    try:
        return int(val_str, 16) if any(c in val_str.lower() for c in "abcdef") else int(val_str)
    except ValueError:
        return None
