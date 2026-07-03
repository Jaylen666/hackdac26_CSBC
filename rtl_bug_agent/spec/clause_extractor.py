from __future__ import annotations

import re
from typing import Any

from rtl_bug_agent.spec.clause import Clause, SignalRef


_SV_LITERAL = re.compile(r"\d*'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ_]+|\b\d+\b")
_SIGNAL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_\[\].]*")
_ASSIGN_OPS = re.compile(
    r"([A-Za-z_][A-Za-z0-9_\[\].]*)\s*"
    r"(==|!=|>=|<=|>|<|=)\s*"
    r"([A-Za-z0-9_'bodhxXzZ.[\]]+)"
)

_ENUM_TYPES = frozenset({"state_transition", "mode_config", "value_domain", "mux_case"})
_COMB_TYPES = frozenset({"assignment", "mux_case", "default_fallback", "error_detection"})
_NEXT_CYCLE_TYPES = frozenset({"reg_update"})

_SV_KW = frozenset({
    "assign", "always", "if", "else", "case", "endcase", "for",
    "begin", "end", "module", "endmodule", "input", "output",
    "inout", "wire", "reg", "logic", "posedge", "negedge",
    "or", "and", "not", "true", "false",
})


def extract_clauses(spec: dict[str, Any]) -> list[Clause]:
    spec_id = str(spec.get("chunk_id", ""))
    clauses: list[Clause] = []
    src = str(spec.get("source_file", ""))
    ls = spec.get("line_start", 0)
    le = spec.get("line_end", 0)

    for item in spec.get("guarantees", []) or []:
        if isinstance(item, str):
            continue
        c = _item_to_clause(item, "guarantee", spec_id, src, ls, le)
        if c: clauses.append(c)
    for item in spec.get("assumptions", []) or []:
        if isinstance(item, str):
            continue
        c = _item_to_clause(item, "assumption", spec_id, src, ls, le)
        if c: clauses.append(c)
    for item in spec.get("uncertain_points", []) or []:
        if isinstance(item, str):
            continue  # old format (string) — skip
        c = _item_to_clause(item, "uncertain", spec_id, src, ls, le)
        if c: clauses.append(c)

    return clauses


def _item_to_clause(
    item: dict[str, Any], kind: str, spec_id: str,
    source_file: str, line_start: int, line_end: int,
) -> Clause | None:
    item_id = str(item.get("id", "") or "")
    if not item_id:
        return None
    claim = str(item.get("claim", "") or "").strip()
    if not claim:
        return None

    cond = str(item.get("cond", "") or "").strip()
    signals = _get_str_list(item, "signals")
    item_type = str(item.get("type", "") or "").strip()

    # --- Subject: prefer the first explicitly-listed driven/used signal ---
    subject, operator, operands = _decompose(claim, signals, item_type, cond)

    temporal = _infer_temporal(item_type, cond, claim)
    formalizability = _infer_formalizability(item_type, claim)
    confidence = _confidence(operator, bool(subject), bool(operands), item_type)

    sig_refs = [SignalRef(name=s) for s in signals if s]
    if subject and subject not in signals:
        sig_refs.append(SignalRef(name=subject))

    return Clause(
        kind=kind, spec_id=spec_id, item_id=item_id,
        subject=subject, operator=operator, operands=operands,
        signals=sig_refs, temporal=temporal,
        condition=cond or None,
        cond_signals=_extract_names(cond) if cond else [],
        formalizability=formalizability,
        formal_confidence=confidence,
        source_text=claim[:500],
        source_file=source_file,
        line_start=line_start, line_end=line_end,
    )


def _decompose(
    claim: str, signals: list[str], item_type: str, cond: str,
) -> tuple[str, str, list[str]]:
    """Decompose a natural-language claim into (subject, operator, operands).

    1. If signals are listed, the primary subject is the first signal.
    2. Look for SV-like expressions (==, !=, >=) in the claim or cond.
    3. For enum types, extract enum-like tokens from cond (not free text).
    4. Fall back to generic assignment.
    """
    # --- Prefer the primary driven signal as subject ---
    primary = signals[0] if signals else ""

    # --- Try to find an SV expression in the claim ---
    parsed = _parse_sv_expr(claim)
    if parsed and parsed[0]:
        return parsed

    # --- Check the cond field for SV expressions ---
    parsed = _parse_sv_expr(cond)
    if parsed and parsed[0]:
        return parsed

    # --- For enum types, extract state names from cond ---
    if primary and item_type in _ENUM_TYPES:
        values = _extract_enum_vals(cond, claim)
        if values:
            return primary, "in_set", values

    # --- For assignment types, try to extract a literal value ---
    if primary:
        val = _extract_val(claim, primary)
        if val:
            return primary, "==", [val]
        # Check for "cleared/asserted/set/driven" patterns
        if re.search(r"\b(cleared|deasserted|driven to 0)\b", claim, re.I):
            return primary, "==", ["1'b0"]
        if re.search(r"\b(set|asserted|raised|driven to 1)\b", claim, re.I):
            return primary, "==", ["1'b1"]
        return primary, "assignment", [claim[:160]]

    # --- Last resort ---
    names = _extract_names(claim)
    if names:
        return names[0], "assignment", [claim[:160]]

    return "", "unknown", []


def _parse_sv_expr(text: str) -> tuple[str, str, list[str]] | None:
    """Find a SystemVerilog-like expression (signal OP value) in text."""
    if not text:
        return None
    for m in _ASSIGN_OPS.finditer(text):
        lhs, op, rhs = m.group(1), m.group(2), m.group(3)
        if _is_keyword(lhs) or _is_keyword(rhs):
            continue
        return lhs, op, [rhs]
    # Check for bit-range: signal[i][j:0] = value
    m = re.search(r"([A-Za-z_][A-Za-z0-9_\[\]]+)\s*<=\s*([^;,.]+)", text)
    if m and not _is_keyword(m.group(1)):
        return m.group(1), "assignment", [m.group(2).strip()[:80]]
    return None


def _extract_enum_vals(cond: str, claim: str) -> list[str]:
    """Extract actual enum values — only from cond, or from very clear patterns.

    Enum values are ALL_CAPS identifiers that appear in the cond field.
    Free text like 'This block assumes' produces false positives, so we
    only trust cond or very specific claim patterns.
    """
    source = cond
    if not source:
        # Fall back to very specific patterns in claim: "in {X, Y, Z}" or
        # "one of X, Y, Z"
        m = re.search(r"(?:in|one of)\s*[{(]?\s*([A-Za-z_][A-Za-z0-9_]*(?:\s*[,|]\s*[A-Za-z_][A-Za-z0-9_]*)*)", claim)
        if m:
            tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", m.group(1))
            return [t for t in tokens if not _is_keyword(t)]
        return []

    # Extract ALL_CAPS / CamelCase tokens that look like identifiers
    tokens = re.findall(r"\b([A-Z][A-Za-z0-9_]*)\b", source)
    return [t for t in tokens if len(t) > 1 and not _is_keyword(t)]


def _extract_val(claim: str, primary: str) -> str | None:
    """Extract a literal value assigned to *primary*."""
    # SV literal like 8'd0, 1'b1, 'b0
    m = _SV_LITERAL.search(claim)
    if m:
        return m.group(0)
    # Numeric value near the primary signal
    m = re.search(rf"{re.escape(primary)}.*?\b(?:to|==)\s+(\d+)", claim)
    if m:
        return m.group(1)
    return None


def _infer_temporal(item_type: str, cond: str, claim: str) -> str:
    if item_type in _NEXT_CYCLE_TYPES:
        return "next_cycle"
    if "posedge" in cond or "negedge" in cond or "clock edge" in cond.lower():
        return "next_cycle"
    if item_type in _COMB_TYPES:
        return "comb"
    return "always"


def _infer_formalizability(item_type: str, claim: str) -> str:
    if item_type in ("state_transition", "mux_case", "error_detection", "assignment",
                      "default_fallback", "reg_update"):
        return "direct"
    if "may" in claim.lower() or "might" in claim.lower() or "could" in claim.lower():
        return "partial"
    return "partial"


def _confidence(op: str, has_subj: bool, has_ops: bool, item_type: str) -> float:
    score = 0.35
    if has_subj: score += 0.25
    if op not in ("unknown", "assignment"): score += 0.15
    if has_ops: score += 0.15
    if item_type in _ENUM_TYPES: score += 0.05
    if item_type in _COMB_TYPES: score += 0.05
    return round(max(0.0, min(score, 1.0)), 3)


def _is_keyword(name: str) -> bool:
    return name.lower() in _SV_KW or name.isdigit()


def _extract_names(text: str) -> list[str]:
    if not text:
        return []
    text = _SV_LITERAL.sub(" ", text)
    names = _SIGNAL_NAME.findall(text)
    out: list[str] = []
    for n in names:
        if n not in out and not _is_keyword(n):
            out.append(n)
    return out


def _get_str_list(item: dict[str, Any], key: str) -> list[str]:
    raw = item.get(key, [])
    if isinstance(raw, list):
        return [str(s) for s in raw if s]
    return []
