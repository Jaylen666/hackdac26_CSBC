"""
Pass 0: Signal Dependency Graph
================================

Rule-based engine (no LLM) that builds a signal→driver→consumer
graph from the Phase 1 spec JSONs.

The graph is the scheduler for all Phase 2 channels — it determines
which specs need to be paired for cross-referencing.

Architecture
------------
For each signal ``S`` we track:

* **drivers**: specs whose ``guarantees[].output_signals`` list ``S``
* **consumers**: specs whose ``assumptions[].related_signals`` list ``S``
* **mentioned_in**: specs whose ``behavior`` text mentions ``S``
  but that neither drive nor consume it
* **kind**: inferred role — ``data``, ``control``, ``security``, ``clock_reset``

The extraction is three-pass:

1. **Structured pass** — guarantees.output_signals and assumptions.related_signals
2. **Text pass** — regex scan of behavior / summary / security_implications
3. **Kind inference** — heuristics on signal name and context

Usage::

    from rtl_bug_agent.phase2.signal_graph import build_signal_graph

    graph = build_signal_graph("output/specs")
    drivers = graph.get_drivers("secret_key_d")
    consumers = graph.get_consumers("secret_key_d")

    # Find assumption-guarantee pairs to check
    pairs = graph.find_ag_pairs()
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rtl_bug_agent.phase2.structural_facts import (
    compact_structural_fact,
    index_structural_facts,
    load_structural_facts,
    normalize_structural_signal,
)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SignalInfo:
    """What we know about a single signal."""
    name: str
    kind: str = "unknown"  # data | control | security | clock_reset
    drivers: list[str] = field(default_factory=list)  # spec chunk_ids
    consumers: list[str] = field(default_factory=list)  # spec chunk_ids
    mentioned_in: list[str] = field(default_factory=list)  # spec chunk_ids
    # Excerpts from behavior text that mention this signal
    context_snippets: list[str] = field(default_factory=list)


@dataclass
class SignalGraph:
    """The complete signal dependency graph for a set of spec JSONs."""

    signals: dict[str, SignalInfo] = field(default_factory=dict)
    # Reverse index: spec → its signals
    spec_signals: dict[str, list[str]] = field(default_factory=dict)
    # Spec metadata indexed by chunk_id
    spec_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    # All specs keyed by chunk_id
    specs: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Optional non-LLM structural facts keyed by normalized signal roots.
    structural_facts: list[dict[str, Any]] = field(default_factory=list)
    structural_facts_by_signal: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def get_drivers(self, signal: str) -> list[str]:
        """Return chunk_ids of specs that drive *signal*."""
        info = self.signals.get(signal)
        return info.drivers if info else []

    def get_consumers(self, signal: str) -> list[str]:
        """Return chunk_ids of specs that consume/assume *signal*."""
        info = self.signals.get(signal)
        return info.consumers if info else []

    def get_specs_mentioning(self, signal: str) -> list[str]:
        """Return all chunk_ids that reference *signal* in any way."""
        info = self.signals.get(signal)
        if not info:
            return []
        return sorted(
            set(info.drivers + info.consumers + info.mentioned_in)
        )

    def attach_structural_facts(self, facts: list[dict[str, Any]]) -> None:
        self.structural_facts = facts
        self.structural_facts_by_signal = index_structural_facts(facts)

    def get_structural_facts(
        self,
        signals: list[str],
        *,
        limit: int = 8,
        include_suspect: bool = True,
    ) -> list[dict[str, Any]]:
        """Return compact structural facts related to *signals*.

        The lookup is deliberately signal-root based and top-k bounded so
        generated register glue does not flood downstream LLM prompts.
        """
        seen: set[str] = set()
        scored: list[tuple[int, int, dict[str, Any]]] = []
        roots = {
            normalize_structural_signal(sig)
            for sig in signals
            if normalize_structural_signal(sig)
        }
        for root in roots:
            for fact in self.structural_facts_by_signal.get(root, []):
                fact_id = str(fact.get("fact_id", ""))
                if not fact_id or fact_id in seen:
                    continue
                seen.add(fact_id)
                score = int(fact.get("rank_score", 0) or 0)
                if include_suspect and score == 0:
                    score += 0
                scored.append((score, int(fact.get("line_start", 0) or 0), _compact_structural_fact(fact)))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [fact for _, _, fact in scored[:limit]]

    def find_ag_pairs(
        self, filter_mode: str = "all"
    ) -> list[dict[str, Any]]:
        """Find every (assumption, driver) pair that should be checked.

        Parameters
        ----------
        filter_mode:
            ``"all"`` — no filtering (default).
            ``"behavioral"`` — skip assumptions classified as structural /
            low-signal (bit widths, array sizes, definitional concerns,
            generic timing).  This reduces noise and LLM cost.

        Returns a list of dicts::

            {
                "signal": "cfg_block",
                "consumer_spec": "hmac__always_comb__update_...",
                "assumption": { ... },
                "driver_specs": ["hmac__always_comb__cast_..."],
                "driver_guarantees": [ ... ],
                "classification": "behavioral" | "structural",
            }
        """
        pairs: list[dict[str, Any]] = []

        for signal, info in self.signals.items():
            if not info.drivers or not info.consumers:
                continue

            for consumer_id in info.consumers:
                consumer_spec = self.specs.get(consumer_id)
                if not consumer_spec:
                    continue

                for assumption in consumer_spec.get("assumptions", []):
                    related = (
                        assumption.get("signals") or
                        assumption.get("related_signals") or []
                    )
                    if signal not in related:
                        continue

                    constraint = (
                        assumption.get("claim") or
                        assumption.get("constraint") or ""
                    )
                    bug_rel = (
                        assumption.get("risk") or
                        assumption.get("bug_relevance") or ""
                    )
                    cls = _classify_assumption(constraint + " " + bug_rel)

                    if filter_mode == "behavioral" and cls == "structural":
                        continue

                    # Collect guarantees from all driver specs
                    driver_guarantees: list[dict[str, Any]] = []
                    for driver_id in info.drivers:
                        driver_spec = self.specs.get(driver_id)
                        if not driver_spec:
                            continue
                        for g in driver_spec.get("guarantees", []):
                            g_sigs = (
                                g.get("signals") or
                                g.get("output_signals") or []
                            )
                            if signal in g_sigs:
                                driver_guarantees.append(
                                    {
                                        "spec_id": driver_id,
                                        "guarantee": g,
                                    }
                                )

                    pairs.append(
                        {
                            "signal": signal,
                            "consumer_spec": consumer_id,
                            "assumption": assumption,
                            "driver_specs": info.drivers[:],
                            "driver_guarantees": driver_guarantees,
                            "classification": cls,
                        }
                    )

        return pairs

    def find_coverage_gaps(self) -> list[dict[str, Any]]:
        """Find signals where the set of legal values described by drivers
        potentially exceeds the set handled by consumers.

        Returns candidate (producer, consumer) pairs for Channel C.
        """
        candidates: list[dict[str, Any]] = []

        for signal, info in self.signals.items():
            if not info.drivers or not info.consumers:
                continue

            # Does any driver's guarantee describe an enumeration /
            # set of legal values?
            for driver_id in info.drivers:
                driver_spec = self.specs.get(driver_id)
                if not driver_spec:
                    continue
                behavior = driver_spec.get("behavior", "")
                # Heuristic: the behavior mentions known encodings
                if _mentions_enumeration(behavior):
                    candidates.append(
                        {
                            "signal": signal,
                            "driver_spec": driver_id,
                            "driver_behavior": behavior[:500],
                            "consumer_specs": info.consumers[:],
                        }
                    )
                    break  # one candidate per signal is enough

        return candidates

    def get_security_signals(self) -> list[str]:
        """Return signals that appear in specs with non-empty
        security_implications."""
        sec_signals: set[str] = set()
        for spec_id, spec in self.specs.items():
            if spec.get("security_implications", "").strip():
                for sig in self.spec_signals.get(spec_id, []):
                    sec_signals.add(sig)
        return sorted(sec_signals)

    def search(
        self,
        *,
        signals: list[str] | None = None,
        keywords: list[str] | None = None,
        scope: str | None = None,
        limit: int = 15,
    ) -> list[dict[str, Any]]:
        """Full-text search across ALL spec fields — not just guarantees.

        Returns a ranked list of matching spec excerpts.  Each hit includes
        the chunk_id, a relevance score, matching field values, and
        highlighted snippets showing *why* it matched.

        Matching strategy (OR across all criteria —宽松匹配，宁可多找):

        1. **Structured** — signal name appears in guarantees.output_signals,
           assumptions.related_signals, or the SignalGraph's existing
           driver/consumer/mention edges.
        2. **Text** — any keyword appears in behavior, summary,
           security_implications, uncertain_points, or assumption constraint
           text.
        3. **Scope** — spec's source_file stem contains *scope* (case-insensitive).

        Parameters
        ----------
        signals:
            Signal names to match via the structured index.
        keywords:
            Free-text keywords matched against all textual fields.
        scope:
            Module / file-stem filter (e.g. ``"hmac_core"``).
        limit:
            Max number of results to return.
        """
        signals_lower = [s.lower() for s in (signals or [])]
        keywords_lower = [k.lower() for k in (keywords or [])]
        scope_lower = (scope or "").lower()

        scored: list[tuple[int, str, dict[str, Any]]] = []
        # (score, chunk_id, hit_dict) — sort by score descending

        for chunk_id, spec in self.specs.items():
            # --- scope filter ---
            if scope_lower:
                src = (spec.get("source_file", "") or "").lower()
                mod = (
                    self.spec_meta.get(chunk_id, {}).get("module", "") or ""
                ).lower()
                if scope_lower not in src and scope_lower not in mod:
                    continue

            spec_signals = set(
                s.lower() for s in self.spec_signals.get(chunk_id, [])
            )

            score = 0
            match_reasons: list[str] = []

            # --- structured match ---
            if signals_lower:
                for sig in signals_lower:
                    # Check guarantee signals (new: signals, old: output_signals)
                    for g in spec.get("guarantees", []):
                        g_sigs = g.get("signals") or g.get("output_signals") or []
                        for out_sig in g_sigs:
                            if sig in out_sig.lower():
                                score += 3
                                match_reasons.append(
                                    f"guarantee.signal matches {sig}"
                                )
                    # Check assumption signals (new: signals, old: related_signals)
                    for a in spec.get("assumptions", []):
                        a_sigs = a.get("signals") or a.get("related_signals") or []
                        for rel_sig in a_sigs:
                            if sig in rel_sig.lower():
                                score += 2
                                match_reasons.append(
                                    f"assumption.related_signal matches {sig}"
                                )
                    # Check SignalGraph edges
                    if sig in spec_signals:
                        score += 1
                        match_reasons.append(
                            f"signal_graph edge matches {sig}"
                        )

            # --- text match ---
            if keywords_lower:
                text_fields = {
                    "summary": spec.get("summary", ""),
                    "behavior": spec.get("behavior", ""),
                    "security_implications": spec.get(
                        "security_implications", ""
                    ),
                }
                for field_name, text in text_fields.items():
                    text_lower = text.lower()
                    for kw in keywords_lower:
                        if kw in text_lower:
                            score += 2
                            match_reasons.append(
                                f"keyword '{kw}' in {field_name}"
                            )

                # Also search uncertain_points and assumption.constraint
                for up in spec.get("uncertain_points", []):
                    up_lower = _uncertain_point_text(up).lower()
                    for kw in keywords_lower:
                        if kw in up_lower:
                            score += 3  # uncertain_points are high-signal
                            match_reasons.append(
                                f"keyword '{kw}' in uncertain_point"
                            )

                for a in spec.get("assumptions", []):
                    constraint = a.get("constraint", "").lower()
                    bug_rel = a.get("bug_relevance", "").lower()
                    for kw in keywords_lower:
                        if kw in constraint or kw in bug_rel:
                            score += 2
                            match_reasons.append(
                                f"keyword '{kw}' in assumption"
                            )

            if score == 0:
                continue

            # Build hit dict
            snippets: list[str] = []
            # Highlight snippets around keyword matches in behavior
            behavior = spec.get("behavior", "")
            for kw in keywords_lower:
                start = behavior.lower().find(kw)
                if start >= 0:
                    lo = max(0, start - 40)
                    hi = min(len(behavior), start + len(kw) + 40)
                    snip = behavior[lo:hi].replace("\n", " ")
                    snippets.append(f"...{snip}...")

            hit = {
                "chunk_id": chunk_id,
                "score": score,
                "match_reasons": match_reasons,
                "summary": spec.get("summary", ""),
                "behavior": spec.get("behavior", "")[:800],
                "guarantees": spec.get("guarantees", []),
                "assumptions": spec.get("assumptions", []),
                "uncertain_points": spec.get("uncertain_points", []),
                "security_implications": spec.get("security_implications", ""),
                "evidence_refs": spec.get("evidence_refs", []),
                "snippets": snippets[:5],
                "source_file": spec.get("source_file", ""),
                "line_start": spec.get("line_start", 0),
                "line_end": spec.get("line_end", 0),
            }
            scored.append((score, chunk_id, hit))

        # Sort by score descending, take top-N
        scored.sort(key=lambda x: x[0], reverse=True)
        return [hit for _, _, hit in scored[:limit]]

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """One-paragraph summary for logging."""
        total = len(self.signals)
        driven = sum(1 for s in self.signals.values() if s.drivers)
        consumed = sum(1 for s in self.signals.values() if s.consumers)
        ag = len(self.find_ag_pairs())
        return (
            f"SignalGraph: {total} signals ({driven} driven, {consumed} consumed), "
            f"{ag} A-G pairs to check, "
            f"{len(self.specs)} specs indexed"
        )

    def dump_stats(self) -> None:
        """Print detailed statistics."""
        print(self.summary())
        print()
        # Top signals by reference count
        ranked = sorted(
            self.signals.items(),
            key=lambda kv: len(kv[1].drivers) + len(kv[1].consumers),
            reverse=True,
        )
        print("Top 15 signals by relevance:")
        for name, info in ranked[:15]:
            d = len(info.drivers)
            c = len(info.consumers)
            m = len(info.mentioned_in)
            print(f"  {name:40s}  drivers={d} consumers={c} mentions={m}  [{info.kind}]")


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_signal_graph(
    specs_dir: str | Path,
    structural_facts_path: str | Path | None = None,
) -> SignalGraph:
    """Load all spec JSONs from *specs_dir* and construct the graph."""
    spec_dir = Path(specs_dir)
    graph = SignalGraph()

    # Load all specs
    for json_path in sorted(spec_dir.glob("*.json")):
        spec = json.loads(json_path.read_text(encoding="utf-8"))
        if json_path.name.startswith("_"):
            continue  # skip manifest / stats / sidecar metadata
        if not isinstance(spec, dict):
            continue  # skip batch lists and other non-spec JSON artifacts
        if "error" in spec:
            continue  # skip failed generations
        chunk_id = spec.get("chunk_id", json_path.stem)
        graph.specs[chunk_id] = spec
        # Extract kind from chunk_id (format: module__kind__label__counter)
        cid_parts = chunk_id.split("__")
        inferred_kind = cid_parts[1] if len(cid_parts) >= 2 else ""
        # Infer module from source_file path
        src = spec.get("source_file", "")
        inferred_module = Path(src).stem if src else ""

        graph.spec_meta[chunk_id] = {
            "kind": spec.get("kind", "") or inferred_kind,
            "source_file": src,
            "module": spec.get("module") or inferred_module,
            "line_start": spec.get("line_start", 0),
            "line_end": spec.get("line_end", 0),
        }

    # Pass 1: structured extraction
    for chunk_id, spec in graph.specs.items():
        signals: set[str] = set()

        # From guarantees → driver
        for g in spec.get("guarantees", []):
            g_sigs = g.get("signals") or g.get("output_signals") or []
            for sig_name in g_sigs:
                sig_name = _normalize_signal(sig_name)
                signals.add(sig_name)
                _ensure_signal(graph, sig_name).drivers.append(chunk_id)

        # From assumptions → consumer
        for a in spec.get("assumptions", []):
            a_sigs = a.get("signals") or a.get("related_signals") or []
            for sig_name in a_sigs:
                sig_name = _normalize_signal(sig_name)
                signals.add(sig_name)
                _ensure_signal(graph, sig_name).consumers.append(chunk_id)

        graph.spec_signals[chunk_id] = sorted(signals)

    # Pass 2: text extraction from behavior / summary / security_implications
    for chunk_id, spec in graph.specs.items():
        text_fields = [
            spec.get("behavior", ""),
            spec.get("summary", ""),
            spec.get("security_implications", ""),
        ]
        for text in text_fields:
            for sig_name in _extract_signals_from_text(text):
                sig_name = _normalize_signal(sig_name)
                info = _ensure_signal(graph, sig_name)
                if chunk_id not in info.drivers and chunk_id not in info.consumers:
                    if chunk_id not in info.mentioned_in:
                        info.mentioned_in.append(chunk_id)
                # Store a snippet of context
                snippet = _extract_snippet(text, sig_name)
                if snippet and snippet not in info.context_snippets:
                    info.context_snippets.append(snippet[:200])

                # Register in spec_signals if not already
                if chunk_id in graph.spec_signals:
                    if sig_name not in graph.spec_signals[chunk_id]:
                        graph.spec_signals[chunk_id].append(sig_name)
                else:
                    graph.spec_signals[chunk_id] = [sig_name]

    # Pass 3: deduplicate driver/consumer lists (preserving order)
    for info in graph.signals.values():
        info.drivers = list(dict.fromkeys(info.drivers))
        info.consumers = list(dict.fromkeys(info.consumers))
        info.mentioned_in = list(dict.fromkeys(info.mentioned_in))

    # Kind inference
    _infer_signal_kinds(graph)

    if structural_facts_path:
        graph.attach_structural_facts(load_structural_facts(structural_facts_path))

    return graph


def _uncertain_point_text(point: Any) -> str:
    """Normalize uncertain-point values to plain text.

    Older specs store uncertain points as strings; newer structured specs
    store dicts with claim/cond/risk fields.  This helper keeps graph search
    and downstream collectors compatible with both.
    """
    if isinstance(point, dict):
        parts = [
            str(point.get("claim", "")).strip(),
            str(point.get("cond", "")).strip(),
            str(point.get("risk", "")).strip(),
            str(point.get("property", "")).strip(),
            str(point.get("constraint", "")).strip(),
            str(point.get("bug_relevance", "")).strip(),
        ]
        return " ".join(part for part in parts if part)
    return str(point).strip()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Matches SystemVerilog identifiers, hierarchical refs, and array indices.
# Examples: secret_key, reg2hw.wipe_secret.qe, key[31-i], secret_key_d[32*i+:32]
_SV_IDENT = r"[a-zA-Z_][a-zA-Z0-9_$]*(?:\.[a-zA-Z_][a-zA-Z0-9_$]*)*(?:\[[^\]]+\])*"

_SIGNAL_RE = re.compile(_SV_IDENT)

# Known non-signal tokens to exclude
_NON_SIGNAL = {
    "begin", "end", "if", "else", "for", "case", "endcase",
    "always_comb", "always_ff", "always_latch", "always",
    "posedge", "negedge", "assign", "module", "endmodule",
    "input", "output", "inout", "wire", "reg", "logic",
    "int", "integer", "bit", "generate", "endgenerate",
    "genvar", "parameter", "localparam", "typedef", "enum",
    "struct", "package", "endpackage", "import",
    "default", "unique", "casez", "casex",
    "prim_alert_sender", "prim_intr_hw",  # instance/module names
    "SHA2_256", "SHA2_384", "SHA2_512", "SHA2_None",  # enum values
    "SelIPadMsg", "SelOPadMsg",  # enum values
    "StIdle", "Inner", "BlockSizeSHA256in64", "BlockSizeSHA512in64",  # constants
    "NumAlerts", "NumShares", "Width",  # parameters
    "clk_i", "rst_ni",  # clock/reset (handled specially)
    "hmac_en_i", "sha_rready_i", "sha_rvalid_o",  # module ports
    "tl_o_pre", "tl_reg_h2d",  # TL-UL bus signals
    "conv_endian32", "key_length_e", "digest_size_e",  # function/type names
    "done_state_q", "done_state_d",  # state register pair
    "cool_down_ct_q", "cool_down_ct_d",
    "digest_size_started_q", "digest_size_started_d",
    "message_length", "message_length_d",
    "txcount", "txcount_d",
    "st_q", "st_d",
    "secret_key", "secret_key_d",
    "reg_hash_stop_q", "reg_hash_stop_d",
    "round_q",
    "hash_start_or_continue",
}

# Signals that should NOT be excluded (override the non-signal set)
_KEEP_SIGNALS = {
    "secret_key", "secret_key_d",
    "st_q", "st_d",
    "done_state_q", "done_state_d",
    "cool_down_ct_q", "cool_down_ct_d",
    "digest_size_started_q", "digest_size_started_d",
    "message_length", "message_length_d",
    "txcount", "txcount_d",
    "reg_hash_stop_q", "reg_hash_stop_d",
    "round_q",
    "hash_start_or_continue",
    "clk_i", "rst_ni",
}

# Common SystemVerilog keywords that are definitely not signals
_SV_KEYWORDS = {
    "begin", "end", "if", "else", "for", "case", "endcase",
    "always_comb", "always_ff", "always_latch", "always",
    "posedge", "negedge", "assign", "module", "endmodule",
    "input", "output", "inout", "wire", "reg", "logic",
    "int", "integer", "bit", "generate", "endgenerate",
    "genvar", "parameter", "localparam", "typedef", "enum",
    "struct", "package", "endpackage", "import",
    "default", "unique", "casez", "casex", "function", "endfunction",
    "task", "endtask", "return", "automatic", "signed", "unsigned",
    "and", "or", "negedge", "assert", "property",
}


def _ensure_signal(graph: SignalGraph, name: str) -> SignalInfo:
    """Get or create a SignalInfo for *name*."""
    if name not in graph.signals:
        graph.signals[name] = SignalInfo(name=name)
    return graph.signals[name]


def _compact_structural_fact(fact: dict[str, Any]) -> dict[str, Any]:
    return compact_structural_fact(fact)


def _normalize_signal(raw: str) -> str:
    """Strip whitespace and trailing array indices for cleaner matching."""
    return raw.strip()


def _extract_signals_from_text(text: str) -> list[str]:
    """Extract plausible signal names from free-form text.

    Filters out common English words, SV keywords, and known non-signal tokens.
    """
    if not text:
        return []
    candidates = _SIGNAL_RE.findall(text)
    results: list[str] = []
    for c in candidates:
        # Skip pure numbers
        if c.isdigit():
            continue
        # Skip SV keywords
        if c in _SV_KEYWORDS:
            continue
        # Skip single characters (likely noise from regex)
        if len(c) <= 1:
            continue
        # Keep if it looks like a signal (has _ or is camelCase, etc.)
        if _looks_like_signal(c):
            results.append(c)
    return results


def _looks_like_signal(name: str) -> bool:
    """Heuristic: does *name* look like a hardware signal?

    Real signals typically contain underscores, or are compound names
    with dots/brackets.  English words rarely contain underscores.
    """
    # Has hierarchy or array access → definitely a signal
    if "." in name or "[" in name:
        return True
    # Contains underscore → very likely a signal
    if "_" in name:
        return True
    # Single word, no underscore — likely an English word or a constant
    # Only keep if it appears in structured signal lists
    return False


def _extract_snippet(text: str, signal: str, window: int = 60) -> str:
    """Extract a short context snippet around *signal* in *text*."""
    idx = text.find(signal)
    if idx == -1:
        return ""
    start = max(0, idx - window)
    end = min(len(text), idx + len(signal) + window)
    snippet = text[start:end].replace("\n", " ")
    return f"...{snippet}..."


def _mentions_enumeration(behavior: str) -> bool:
    """Heuristic: does the behavior text describe an enumeration or
    set of legal values for some signal?"""
    enum_hints = [
        "取值", "合法值", "枚举", "只有", "只允许",
        "inside {", "case", "SHA2_256", "SHA2_384", "SHA2_512",
        "one of", "valid values", "enum",
    ]
    return any(hint in behavior for hint in enum_hints)


def _infer_signal_kinds(graph: SignalGraph) -> None:
    """Infer the kind of each signal (data, control, security, clock_reset)."""
    security_signals: set[str] = set()
    for spec in graph.specs.values():
        if spec.get("security_implications", "").strip():
            for sig in graph.spec_signals.get(spec.get("chunk_id", ""), []):
                security_signals.add(sig)

    for name, info in graph.signals.items():
        lower = name.lower()
        if "clk" in lower or "rst" in lower or "reset" in lower:
            info.kind = "clock_reset"
        elif name in security_signals:
            info.kind = "security"
        elif any(
            kw in lower
            for kw in ("_en", "_we", "_sel", "_ctrl", "cfg_", "start", "stop",
                       "process", "done", "valid", "ready", "qe", "trigger")
        ):
            info.kind = "control"
        else:
            info.kind = "data"


# ---------------------------------------------------------------------------
# Assumption classifier (pre-filter for A-G channels)
# ---------------------------------------------------------------------------

# Patterns that indicate a *structural / low-signal* assumption.
# These are rarely actionable bugs — they describe mechanical consistency
# (bit widths match, arrays have N elements, ports are connected) rather
# than behavioural semantics.
_LOW_SIGNAL_PATTERNS: list[tuple[str, str]] = [
    # Structural / mechanical
    (r"必须.*(?:一致|匹配|对齐|等于|相同)", "consistency"),
    (r"(?:数量|长度|位宽|宽度|深度|大小).*必须", "dimension"),
    (r"(?:数组|参数|寄存器).*(?:元素|个数|数目|项)", "count"),
    (r"(?:索引|下标|偏移).*合法|越界", "index"),
    (r"位定义|字节使能", "bit_def"),
    (r"(?:位宽|编码|类型|宽度).*(?:兼容|一致|匹配|合法)", "compat"),
    (r"NumRegs|HMAC_PERMIT|addrmiss|addr_hit|reg_be\b", "param_name"),
    # Generic timing meta (not actionable bugs)
    (r"(?:建立|保持|采样|时序稳定性|时序要求)", "timing_meta"),
    (r"应编码为|应被编码", "encoding_req"),
    (r"复位值.*(?:无歧义|明确|表示)", "reset_val"),
    # Generic correctness
    (r"(?:端口|接口).*(?:连接|绑定)", "port"),
    (r"必须.*满足.*时序", "timing_generic"),
]

# Patterns that indicate a *behavioural / high-signal* assumption.
_HIGH_SIGNAL_PATTERNS: list[tuple[str, str]] = [
    (r"当.*时|在.*下|若.*则|只要|一旦", "conditional"),
    (r"(?:语义|含义|设计意图|注释.*[称指])", "semantic"),
    (r"(?:不允许|禁止|只允许|只能|不可|不得)", "prohibition"),
    (r"(?:引擎|空闲|忙碌|处理中|运行中|活动|非空闲)", "engine"),
    (r"(?:同时.*有效|同时.*为\s*1|冲突|互斥)", "mutual_exclusion"),
    (r"(?:优先|先于|后于|之前|之后|等待|延迟)", "temporal_order"),
    (
        r"(?:cfg_block|digest_size|key_length|secret_key|"
        r"wipe_secret|in_process|hmac_done|hash_done|alert)",
        "sec_signal",
    ),
    (r"(?:密钥|擦除|清除|泄露|残留|安全)", "security_chinese"),
    (r"(?:非法|未授权|未预期|意外)", "illegal_state"),
]


def _classify_assumption(text: str) -> str:
    """Classify an assumption text as ``"structural"`` or ``"behavioral"``.

    Structural assumptions describe mechanical / definitional constraints
    (bit-width matching, array sizing, port wiring, generic timing) that
    are rarely actionable bugs.  Behavioural assumptions describe semantic
    or temporal constraints on hardware behaviour (conditional rules,
    mutual exclusion, state semantics).

    If both patterns match or neither matches, the assumption is
    classified as ``"behavioral"`` (keep — err on the side of not
    filtering out a real signal).
    """
    is_low = any(re.search(p, text) for p, _ in _LOW_SIGNAL_PATTERNS)
    is_high = any(re.search(p, text) for p, _ in _HIGH_SIGNAL_PATTERNS)

    if is_low and not is_high:
        return "structural"
    return "behavioral"
