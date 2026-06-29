from __future__ import annotations

import re
from typing import Any

_DEFAULT_FORMALIZABILITY = "partial"
_DEFAULT_TEMPORAL = "always"
_CLOCK_HINTS = ("clk", "clock", "posedge", "negedge")
_RESET_HINTS = ("rst", "reset", "por", "rst_ni", "rst_n")
_TEMPORAL_HINTS = (
    ("next_cycle", ("next cycle", "next-cycle", "one cycle later", "cycle later", "posedge",
                    "下一周期", "下一拍", "时钟沿后", "一拍", "时钟上升沿")),
    ("comb", ("combin", "same cycle", "immediately", "immediate", "concurrent",
              "组合", "同一周期", "立即", "立刻", "同拍")),
    ("always", ("always", "whenever", "must", "should", "every time",
                "始终", "总是", "每当", "必须", "应当", "恒", "任何时候")),
)
_FORMALIZABILITY_HINTS = {
    "direct": ("assert", "must", "always", "when", "if",
               "必须", "一定", "当", "如果", "恒为"),
    "partial": ("may", "could", "potential", "should", "risk", "uncertain",
                "可能", "也许", "潜在", "应该", "风险", "不确定"),
    "none": ("todo", "unknown", "unclear", "needs context", "cannot",
             "未知", "不清楚", "需要上下文", "无法", "待确认"),
}

# Chinese antecedent connectives → split point markers
_CN_ANTECEDENT_PREFIXES = ("当", "若", "如果", "一旦", "在")
_CN_CONSEQUENT_MARKERS = ("则", "时", "那么", "就")


def build_formal_sketch(
    item: dict[str, Any],
    *,
    spec: dict[str, Any] | None = None,
    role: str = "claim",
) -> dict[str, Any]:
    """Build a formal sketch for a spec item.

    Prefers LLM-provided ``formal`` fields (Phase 1 now emits a ``formal``
    sub-object per A/G/U item). Falls back to conservative heuristic
    extraction from the claim text when the LLM did not supply them.
    """
    spec = spec or {}
    signals = _unique_strings(
        item.get("signals")
        or item.get("output_signals")
        or item.get("related_signals")
        or []
    )
    text = _item_text(item)
    cond = str(item.get("cond", "") or "").strip()
    scope = _infer_scope(spec, item)
    clock = _infer_clock(signals, spec, text)
    reset = _infer_reset(signals, spec, text)

    # LLM-provided formal hints take precedence over heuristics.
    llm_formal = item.get("formal") if isinstance(item.get("formal"), dict) else {}
    llm_temporal = str(llm_formal.get("temporal_shape", "") or "").strip().lower()
    llm_antecedent = str(llm_formal.get("antecedent", "") or "").strip()
    llm_consequent = str(llm_formal.get("consequent", "") or "").strip()
    llm_formalizability = str(llm_formal.get("formalizability", "") or "").strip().lower()

    temporal_shape = (
        llm_temporal if llm_temporal in ("comb", "next_cycle", "always")
        else _infer_temporal_shape(text, cond)
    )
    antecedent = _clean_clause(llm_antecedent) if llm_antecedent else _extract_antecedent(text, cond, role=role)
    consequent = _clean_clause(llm_consequent) if llm_consequent else _extract_consequent(text, antecedent, role=role)
    formalizability = (
        llm_formalizability if llm_formalizability in ("direct", "partial", "none")
        else _infer_formalizability(text, cond, role=role)
    )
    llm_supplied = bool(llm_antecedent or llm_consequent or llm_formalizability or llm_temporal)
    confidence = _confidence_score(
        text=text,
        signals=signals,
        antecedent=antecedent,
        consequent=consequent,
        formalizability=formalizability,
        llm_supplied=llm_supplied,
    )
    if formalizability == "none":
        consequent = consequent or text[:200]

    return {
        "scope": scope,
        "clock": clock,
        "reset": reset,
        "signals": signals[:8],
        "temporal_shape": temporal_shape,
        "antecedent": antecedent,
        "consequent": consequent,
        "formalizability": formalizability,
        "confidence": confidence,
        "source": "llm" if llm_supplied else "heuristic",
    }


def attach_formal_sketches(spec: dict[str, Any]) -> dict[str, Any]:
    """Return *spec* with conservative formal sketches attached to items."""
    spec = dict(spec)
    for field, role in (
        ("assumptions", "assumption"),
        ("guarantees", "guarantee"),
        ("uncertain_points", "uncertain"),
    ):
        items = spec.get(field, []) or []
        normalised: list[dict[str, Any]] = []
        for idx, item in enumerate(items):
            normalised.append(_normalise_item(item, spec=spec, role=role, index=idx))
        spec[field] = normalised
    return spec


def merge_formal_sketch(
    existing: dict[str, Any] | None,
    generated: dict[str, Any],
) -> dict[str, Any]:
    """Overlay a generated sketch onto an existing one without losing fields."""
    base = dict(existing or {})
    for key, value in generated.items():
        if key == "signals":
            cur = _unique_strings(base.get(key, []))
            for sig in value:
                if sig not in cur:
                    cur.append(sig)
            base[key] = cur
        elif key in ("scope", "clock", "reset", "temporal_shape", "antecedent", "consequent", "formalizability"):
            if not base.get(key):
                base[key] = value
        elif key == "confidence":
            base[key] = round(max(float(base.get(key, 0.0) or 0.0), float(value or 0.0)), 3)
        else:
            base[key] = value
    return base


def summarise_formal_context(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise a set of A/G/U items into a coarse formal view."""
    sketches = [
        item.get("formal_sketch", {})
        for item in items
        if isinstance(item, dict) and isinstance(item.get("formal_sketch", {}), dict)
    ]
    if not sketches:
        return {"formal_verdict": "NONE", "formal_confidence": 0.0}

    best = max(float(sketch.get("confidence", 0.0) or 0.0) for sketch in sketches)
    direct = any(str(sketch.get("formalizability", "")).lower() == "direct" for sketch in sketches)
    partial = any(str(sketch.get("formalizability", "")).lower() == "partial" for sketch in sketches)
    if direct:
        verdict = "DIRECT"
    elif partial:
        verdict = "PARTIAL"
    else:
        verdict = "NONE"
    return {
        "formal_verdict": verdict,
        "formal_confidence": round(best, 3),
    }


def pick_property_draft(
    finding: dict[str, Any],
    graph: Any,
    *,
    min_confidence: float = 0.75,
) -> dict[str, Any] | None:
    """Select a sketch that can be turned into a bounded assertion draft."""
    involved_signals = set(str(s) for s in finding.get("involved_signals", []) or [])
    best: tuple[float, dict[str, Any], dict[str, Any], str] | None = None

    for spec_id in finding.get("involved_specs", []) or []:
        spec = getattr(graph, "specs", {}).get(spec_id, {})
        if not isinstance(spec, dict):
            continue
        for role, field in (("assumption", "assumptions"), ("guarantee", "guarantees"), ("uncertain", "uncertain_points")):
            for item in spec.get(field, []) or []:
                if not isinstance(item, dict):
                    continue
                sketch = item.get("formal_sketch") or build_formal_sketch(item, spec=spec, role=role)
                score = _draft_score(sketch, item, involved_signals, role)
                if best is None or score > best[0]:
                    best = (score, sketch, item, spec_id)

    if not best:
        return None

    _, sketch, item, spec_id = best
    if float(sketch.get("confidence", 0.0) or 0.0) < min_confidence:
        return None
    if not _looks_executable_clause(str(sketch.get("antecedent", ""))) and not _looks_executable_clause(str(sketch.get("consequent", ""))):
        return None

    module = _infer_scope(getattr(graph, "specs", {}).get(spec_id, {}), item)
    spec = getattr(graph, "specs", {}).get(spec_id, {}) if hasattr(graph, "specs") else {}
    assertion = render_property_assertion(sketch)
    if not assertion:
        return None

    return {
        "spec_id": spec_id,
        "module": module,
        "source_file": str(spec.get("source_file", "") or ""),
        "line_start": spec.get("line_start"),
        "line_end": spec.get("line_end"),
        "sketch": sketch,
        "assertion": assertion,
    }


def render_property_assertion(sketch: dict[str, Any]) -> str:
    """Render a conservative SystemVerilog assertion from a sketch."""
    clock = str(sketch.get("clock") or "clk_i").strip() or "clk_i"
    reset = str(sketch.get("reset") or "").strip()
    antecedent = str(sketch.get("antecedent") or "").strip()
    consequent = str(sketch.get("consequent") or "").strip()
    temporal_shape = str(sketch.get("temporal_shape") or "").strip()

    body = consequent or antecedent
    if antecedent and consequent:
        relation = "|=> " if temporal_shape == "next_cycle" else "|-> "
        body = f"({antecedent}) {relation}({consequent})"
    if not body:
        return ""

    disable = ""
    if reset:
        disable_expr = _reset_disable_expression(reset)
        disable = f" disable iff ({disable_expr})" if disable_expr else ""
    return f"assert property (@(posedge {clock}){disable} {body});"


# SystemVerilog reserved words / literals that look like identifiers but are not
# signal names — excluded from name validation to avoid false "unknown" flags.
# Includes sampled-value system functions ($past/$rose/...) whose bare name
# leaks through identifier extraction once the leading '$' is dropped.
_SV_KEYWORDS = frozenset({
    "posedge", "negedge", "disable", "iff", "assert", "property", "always",
    "if", "else", "begin", "end", "and", "or", "not", "1", "0",
    "past", "rose", "fell", "stable", "changed", "onehot", "onehot0",
    "isunknown", "countones", "sampled",
})


def render_sva_bind(
    sketch: dict[str, Any],
    module: str,
    *,
    property_name: str = "p_csbc",
    signal_widths: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Render an SVA wrapped in a ``bind``-able checker module.

    Produces a self-contained checker that can be bound to *module* by
    JasperGold (`bind`) or sby. The checker takes the clock, reset and the
    referenced signals as ports so it never reaches into DUT internals by name
    collision.

    *signal_widths* maps signal name → bit width (from RTL declarations). When a
    signal's width is known and > 1, its port is declared ``logic [W-1:0]`` so a
    multi-bit / enum / vector comparison is not silently truncated to the LSB.
    Signals absent from the map default to scalar ``logic`` (back-compat). The
    authoritative width source is wired in Step 6; callers without it still get
    a syntactically valid checker, just scalar ports.

    Returns a dict with::

        {
          "sva":         the bare assert property (...) statement,
          "checker":     full `module <name>_chk (...); ... endmodule` text,
          "bind_stmt":   `bind <module> <name>_chk i_<name>_chk (.*);`,
          "bind_module": module,
          "bind_signals": [referenced signal names],
          "port_widths": {sig: width used},
          "clock": ..., "reset": ...,
        }

    Returns ``{}`` when the sketch has no usable property body.
    """
    sva = render_property_assertion(sketch)
    if not sva:
        return {}

    widths = {str(k): int(v) for k, v in (signal_widths or {}).items() if v}
    clock = str(sketch.get("clock") or "clk_i").strip() or "clk_i"
    reset = str(sketch.get("reset") or "").strip()
    antecedent = str(sketch.get("antecedent") or "")
    consequent = str(sketch.get("consequent") or "")

    # Collect referenced signals: explicit sketch signals + names appearing in
    # the antecedent/consequent expressions, minus clock/reset/keywords.
    refs: list[str] = []
    declared = set()
    for sig in _unique_strings(sketch.get("signals", []) or []):
        bare = sig.lstrip("!~")
        if bare and bare not in declared:
            declared.add(bare)
            refs.append(bare)
    for name in _extract_names(f"{antecedent} {consequent}"):
        bare = name.split("[", 1)[0].split(".", 1)[0]
        if not bare or bare in declared:
            continue
        if bare in _SV_KEYWORDS or bare == clock or bare == reset:
            continue
        if re.fullmatch(r"\d+", bare) or "'" in name:
            continue
        declared.add(bare)
        refs.append(bare)

    chk_name = f"{module}_{property_name}_chk" if module else f"{property_name}_chk"
    ports = [_port_decl(clock, widths)]
    if reset:
        ports.append(_port_decl(reset, widths))
    ports.extend(_port_decl(sig, widths) for sig in refs)
    port_decl = ",\n    ".join(ports)

    checker = (
        f"module {chk_name} (\n    {port_decl}\n);\n"
        f"  {property_name}: {sva}\n"
        f"endmodule"
    )
    bind_stmt = f"bind {module} {chk_name} i_{chk_name} (.*);" if module else ""

    return {
        "sva": sva,
        "checker": checker,
        "bind_stmt": bind_stmt,
        "bind_module": module,
        "bind_signals": refs,
        "port_widths": {s: widths[s] for s in refs if s in widths},
        "clock": clock,
        "reset": reset,
        "property_name": property_name,
    }


def _port_decl(sig: str, widths: dict[str, int]) -> str:
    """Render one checker input port, vector-aware.

    Uses ``logic [W-1:0]`` when *sig*'s width is known and > 1, else scalar
    ``logic``. Prevents multi-bit comparisons from being truncated to the LSB.
    """
    w = widths.get(sig, 1)
    if w > 1:
        return f"input logic [{w - 1}:0] {sig}"
    return f"input logic {sig}"


def validate_signal_names(
    sketch: dict[str, Any],
    known_signals: set[str] | dict[str, Any] | Any,
) -> dict[str, Any]:
    """Deterministically check sketch signal names against known RTL signals.

    *known_signals* may be a set/iterable of names or a SignalGraph-like object
    exposing a ``signals`` mapping. Returns::

        {"ok": bool, "unknown_signals": [...], "checked_signals": [...]}

    A signal is "unknown" if neither it nor its base name (stripping bit-selects
    and hierarchy) appears in the known set. SV keywords, the clock and reset,
    and numeric/sized literals are never counted as unknown.

    Used by Channel B / F after emitting an SVA: any unknown signal means the
    LLM may have hallucinated a name, so the property is marked
    ``status="NAME_UNVERIFIED"`` instead of being sent to the solver.
    """
    known = _coerce_known_signals(known_signals)
    clock = str(sketch.get("clock") or "").strip()
    reset = str(sketch.get("reset") or "").strip()

    candidates: list[str] = []
    seen = set()
    for sig in _unique_strings(sketch.get("signals", []) or []):
        bare = sig.lstrip("!~").split("[", 1)[0].split(".", 1)[0]
        if bare and bare not in seen:
            seen.add(bare)
            candidates.append(bare)
    expr = f"{sketch.get('antecedent', '')} {sketch.get('consequent', '')}"
    for name in _extract_names(expr):
        bare = name.split("[", 1)[0].split(".", 1)[0]
        if not bare or bare in seen:
            continue
        if bare in _SV_KEYWORDS or bare in (clock, reset):
            continue
        if re.fullmatch(r"\d+", bare) or "'" in name:
            continue
        seen.add(bare)
        candidates.append(bare)

    unknown = [s for s in candidates if s not in known and s.lower() not in known]
    return {
        "ok": not unknown,
        "unknown_signals": unknown,
        "checked_signals": candidates,
    }


def _coerce_known_signals(known: set[str] | dict[str, Any] | Any) -> set[str]:
    """Normalise various 'known signals' inputs into a lower-friendly set."""
    if known is None:
        return set()
    if hasattr(known, "signals") and isinstance(getattr(known, "signals"), dict):
        names = getattr(known, "signals").keys()
    elif isinstance(known, dict):
        names = known.keys()
    else:
        try:
            names = list(known)
        except TypeError:
            return set()
    out: set[str] = set()
    for n in names:
        s = str(n).strip()
        if s:
            out.add(s)
            out.add(s.lower())
    return out


def sketch_to_prompt_text(sketch: dict[str, Any] | None) -> str:
    if not sketch:
        return ""
    parts = []
    for key in ("scope", "clock", "reset", "signals", "temporal_shape", "antecedent", "consequent", "formalizability", "confidence"):
        value = sketch.get(key)
        if value in (None, "", [], {}):
            continue
        parts.append(f"{key}: {value}")
    return "\n".join(parts)


def _item_text(item: dict[str, Any]) -> str:
    return str(
        item.get("claim")
        or item.get("property")
        or item.get("constraint")
        or ""
    ).strip()


def _normalise_item(
    item: Any,
    *,
    spec: dict[str, Any],
    role: str,
    index: int,
) -> dict[str, Any]:
    if isinstance(item, dict):
        out = dict(item)
    else:
        out = {"claim": str(item).strip()}
    if not out.get("claim"):
        out["claim"] = str(out.get("property") or out.get("constraint") or "").strip()
    out.setdefault("cond", "")
    out.setdefault("risk", "")
    out.setdefault("refs", [])
    out.setdefault("signals", [])
    out.setdefault("formal", {})
    if role == "uncertain":
        out.setdefault("priority", "medium")
    out.setdefault("type", role)
    out.setdefault("id", f"{role[:1].upper()}{index + 1}")
    generated = build_formal_sketch(out, spec=spec, role=role)
    out["formal_sketch"] = merge_formal_sketch(out.get("formal_sketch"), generated)
    return out


def _infer_scope(spec: dict[str, Any], item: dict[str, Any]) -> str:
    scope = str(spec.get("module") or "").strip()
    if scope:
        return scope
    src = str(spec.get("source_file") or "").strip()
    if src:
        stem = src.rsplit("/", 1)[-1]
        return stem.rsplit(".", 1)[0]
    text = _item_text(item)
    m = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", text)
    return m.group(1) if m else ""


def _infer_clock(signals: list[str], spec: dict[str, Any], text: str) -> str:
    candidates = _unique_strings(
        signals
        + _extract_names(str(spec.get("context_summary", "")))
        + _extract_names(text)
    )
    for sig in candidates:
        lower = sig.lower()
        if any(h in lower for h in _CLOCK_HINTS):
            return sig
    return ""


def _infer_reset(signals: list[str], spec: dict[str, Any], text: str) -> str:
    candidates = _unique_strings(
        signals
        + _extract_names(str(spec.get("context_summary", "")))
        + _extract_names(text)
    )
    for sig in candidates:
        lower = sig.lower()
        if any(h in lower for h in _RESET_HINTS):
            return sig
    return ""


def _infer_temporal_shape(text: str, cond: str) -> str:
    joined = f"{text} {cond}".lower()
    for shape, hints in _TEMPORAL_HINTS:
        if any(h in joined for h in hints):
            return shape
    return _DEFAULT_TEMPORAL


def _extract_antecedent(text: str, cond: str, *, role: str) -> str:
    cond = cond.strip()
    if cond:
        return _clean_clause(cond)
    lower = text.lower()
    for prefix in ("when ", "if ", "once ", "after ", "whenever ", "provided that "):
        if lower.startswith(prefix):
            return _clean_clause(text[len(prefix):].split(",", 1)[0].split(" then ", 1)[0])
    if role == "assumption" and lower.startswith("requires "):
        return _clean_clause(text[len("requires "):])
    # Chinese: 当X时/则Y, 若X则Y, 如果X那么Y
    for prefix in _CN_ANTECEDENT_PREFIXES:
        if text.startswith(prefix):
            rest = text[len(prefix):]
            for marker in _CN_CONSEQUENT_MARKERS:
                pos = rest.find(marker)
                if pos > 0:
                    return _clean_clause(rest[:pos])
    return ""


def _extract_consequent(text: str, antecedent: str, *, role: str) -> str:
    body = text.strip()
    if antecedent:
        lowered = body.lower()
        if lowered.startswith("when ") or lowered.startswith("if "):
            sep = body.find(",")
            if sep > 0:
                body = body[sep + 1:].strip()
            else:
                parts = re.split(r"\bthen\b", body, flags=re.IGNORECASE, maxsplit=1)
                if len(parts) == 2:
                    body = parts[1].strip()
        else:
            # Chinese consequent: text after 则/时/那么/就
            for prefix in _CN_ANTECEDENT_PREFIXES:
                if body.startswith(prefix):
                    for marker in _CN_CONSEQUENT_MARKERS:
                        pos = body.find(marker)
                        if pos > 0:
                            body = body[pos + len(marker):].strip()
                            break
                    break
    if role == "uncertain":
        return _clean_clause(body[:240])
    if role == "assumption" and not antecedent:
        return _clean_clause(body[:240])
    return _clean_clause(body[:240])


def _infer_formalizability(text: str, cond: str, *, role: str) -> str:
    joined = f"{text} {cond}".lower()
    if role == "uncertain":
        return "none" if any(h in joined for h in _FORMALIZABILITY_HINTS["none"]) else "partial"
    if any(h in joined for h in _FORMALIZABILITY_HINTS["none"]):
        return "none"
    if any(h in joined for h in _FORMALIZABILITY_HINTS["direct"]):
        return "direct"
    if any(h in joined for h in _FORMALIZABILITY_HINTS["partial"]):
        return "partial"
    return _DEFAULT_FORMALIZABILITY


def _confidence_score(
    *,
    text: str,
    signals: list[str],
    antecedent: str,
    consequent: str,
    formalizability: str,
    llm_supplied: bool = False,
) -> float:
    score = 0.35
    if signals:
        score += 0.2
    if antecedent:
        score += 0.15
    if consequent:
        score += 0.15
    if formalizability == "direct":
        score += 0.15
    elif formalizability == "none":
        score -= 0.15
    if len(text) < 180:
        score += 0.05
    # LLM-supplied formal fields are more reliable than text heuristics,
    # especially when the clauses look like real executable expressions.
    if llm_supplied:
        if _looks_executable_clause(antecedent) or _looks_executable_clause(consequent):
            score += 0.1
        else:
            score += 0.03
    return round(max(0.0, min(score, 1.0)), 3)


def _draft_score(
    sketch: dict[str, Any],
    item: dict[str, Any],
    involved_signals: set[str],
    role: str,
) -> float:
    score = float(sketch.get("confidence", 0.0) or 0.0)
    if role == "guarantee":
        score += 0.12
    elif role == "assumption":
        score += 0.06
    if sketch.get("formalizability") == "direct":
        score += 0.15
    elif sketch.get("formalizability") == "partial":
        score += 0.05
    if sketch.get("clock"):
        score += 0.05
    if sketch.get("reset"):
        score += 0.05
    sigs = set(str(s) for s in sketch.get("signals", []) or [])
    overlap = len(sigs & involved_signals)
    if overlap:
        score += min(0.2, 0.05 * overlap)
    text = _item_text(item)
    if _looks_executable_clause(text):
        score += 0.08
    return score


# SystemVerilog sized/based literals: 2'b10, 8'hff, 16'sd5, 'b0, 'h3f, etc.
# The radix+value part (b10, hff, sd5) otherwise leaks through identifier
# extraction as a fake signal name, so we strip whole literals first. Size is
# optional ('b0 has none) so no leading \b which would reject the size-less form.
_SV_LITERAL_RE = re.compile(r"\d*'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ_]+")


def _extract_names(text: str) -> list[str]:
    if not text:
        return []
    text = _SV_LITERAL_RE.sub(" ", text)
    names = re.findall(r"[A-Za-z_][A-Za-z0-9_.$\[\]:]*", text)
    return _unique_strings(names)


def _looks_executable_clause(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    if any(op in text for op in ("==", "!=", "&&", "||", "|->", "->", "<=", ">=", "!", "(", ")")):
        return True
    if re.search(r"\b(?:posedge|negedge|assert|disable iff)\b", lower):
        return True
    if re.search(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", text):
        return True
    return False


def _unique_strings(values: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        s = str(value).strip()
        if s and s not in out:
            out.append(s)
    return out


def _clean_clause(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    # Strip leading/trailing ASCII and CJK punctuation.
    text = text.strip(".,;:")
    text = text.strip("，。；：、")
    return text.strip()


def _reset_disable_expression(reset: str) -> str:
    reset = str(reset).strip()
    if not reset:
        return ""
    lower = reset.lower()
    if re.search(r"(_ni|_n|_b)$", lower):
        return f"!{reset}"
    return reset
