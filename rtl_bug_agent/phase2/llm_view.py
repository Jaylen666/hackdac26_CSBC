"""
LLM view projection — the single allow-list for finding fields sent to an LLM
(Formal CSBC v2.0 §5.2, Gate 1).

Every code path that builds an LLM payload from a finding MUST go through
:func:`finding_for_llm`. Direct ``json.dumps(finding)`` or ad-hoc field picking
is forbidden, because that is how internal fields (trace, solver workdir, logs,
counterexample paths) silently leak into prompts and inflate token cost.

The allow-list is *positive*: a newly added finding field is invisible to the
LLM by default and only becomes visible when explicitly added here. ``tests/
test_llm_view.py`` pins this behaviour so the boundary cannot regress.
"""

from __future__ import annotations

from typing import Any

# Finding fields the LLM is allowed to see. ``trace_ref`` and any ``formal``
# internal fields (workdir/log/clock/bind_*) are intentionally absent.
_LLM_VISIBLE_FINDING_FIELDS = (
    "title",
    "severity",
    "verdict",
    "channels",
    "contradiction",
    "involved_signals",
)

# From formal_result, expose only the tool conclusion and the proven property —
# not workdir, log_excerpt, counterexample_path, solver, depth, backend, etc.
_LLM_VISIBLE_FORMAL_RESULT_FIELDS = ("verdict", "sva")


def finding_for_llm(finding: dict[str, Any]) -> dict[str, Any]:
    """Project *finding* down to the only fields allowed into an LLM prompt.

    Trace records, trace_ref, and formal internal/debug fields are never
    included. Returns a fresh dict; the input is not mutated.
    """
    out: dict[str, Any] = {k: finding.get(k) for k in _LLM_VISIBLE_FINDING_FIELDS}

    formal_result = finding.get("formal_result") or {}
    if isinstance(formal_result, dict) and formal_result.get("verdict"):
        projected = {
            k: formal_result.get(k)
            for k in _LLM_VISIBLE_FORMAL_RESULT_FIELDS
            if formal_result.get(k) is not None
        }
        # Prefer the SVA actually checked; fall back to the proposed property.
        if "sva" not in projected:
            formal = finding.get("formal") or {}
            if isinstance(formal, dict) and formal.get("sva"):
                projected["sva"] = formal["sva"]
        out["formal_result"] = projected

    return out
