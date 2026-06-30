"""
Channel D: Temporal Consistency (v3 — Anchor-Supervised)

Uses SignalGraph causal paths + event anchors to generate
focused candidate pairs, then asks the LLM to judge whether
two timing descriptions of the same hardware event conflict.

Design principle:
  Script discovers relationships.  LLM judges consistency.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from rtl_bug_agent.llm.client import OpenAICompatibleClient
from rtl_bug_agent.phase2.signal_graph import SignalGraph

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = _PROJECT_ROOT / "config/prompts/phase2/channel_d_temporal.md"

# ------------------------------------------------------------------
# Stage 1: Timing Atom proxies (from existing spec fields)
# ------------------------------------------------------------------

_EVENT_MAP = {
    "done": "done", "idle": "idle", "complete": "done", "finish": "done",
    "process": "busy", "busy": "busy", "running": "busy",
    "start": "start", "stop": "stop", "ready": "ready", "valid": "valid",
    "initiated": "start", "flush": "done", "error": "error",
    "clear": "done", "wipe": "done", "load": "start", "reset": "start",
}


_TIMING_TEXT_HINTS = (
    "done", "idle", "complete", "finish", "process", "busy", "running",
    "ready", "valid", "start", "stop", "initiated", "flush", "clear",
    "wipe", "load", "reset", "完成", "空闲", "忙", "运行", "处理中",
    "开始", "启动", "停止", "等待", "延迟", "周期", "计数", "状态机",
    "握手", "有效", "就绪", "拉高", "拉低", "置位", "清零", "清除",
)


_TIMING_NAME_HINTS = (
    "done", "idle", "complete", "finish", "process", "busy", "running",
    "ready", "valid", "start", "stop", "initiated", "flush", "clear",
    "wipe", "load", "event", "state", "cnt", "count", "counter",
    "cool_down", "in_process", "full", "empty", "blocked", "block",
)

_STRUCTURAL_SUFFIXES = (
    "_offset", "_size", "_permit", "_we", "_re", "_wd",
)

_STRUCTURAL_NAMES = {
    "prim_intr_hw",
    "prim_mubi_pkg",
    "mubi4_t",
    "mubi4_test_true_strict",
    "mubi4_test_false_strict",
    "mubi4true",
    "mubi4false",
}

_COMPLETION_SIGNALS = ("done", "complete", "finish", "idle")
_BUSY_SIGNALS = ("busy", "process", "running", "in_process")
_START_STOP_SIGNALS = ("start", "stop", "continue", "initiated")
_HANDSHAKE_SIGNALS = ("ready", "valid")
_COUNTER_SIGNALS = ("cnt", "count", "counter", "cool_down", "txcount")
_RESET_TEXT_HINTS = ("复位", "reset", "rst")


def _has_english_word(text: str, words: tuple[str, ...]) -> bool:
    return re.search(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", text) is not None


def _is_structural_signal(signal: str) -> bool:
    lower = signal.lower()
    if lower in _STRUCTURAL_NAMES:
        return True
    if len(signal) <= 1:
        return True
    if lower.endswith(".sv") or "/" in signal:
        return True
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", signal):
        return True
    if signal[0].isupper() and "_" not in signal and "." not in signal and "[" not in signal:
        return True
    if any(ch in signal for ch in "[]:"):
        return True
    if "." in signal and not (
        "status" in lower or "intr" in lower or lower.endswith((".d", ".q", ".de", ".qe"))
    ):
        return True
    if lower.endswith(_STRUCTURAL_SUFFIXES):
        return True
    if re.search(r"_\d+_(?:we|re)$", lower):
        return True
    return False


def _is_timing_signal(signal: str, text: str) -> bool:
    if _is_structural_signal(signal):
        return False

    lower = signal.lower()
    text_lower = text.lower()
    if _name_has_timing_hint(signal):
        return True
    if re.search(r"_(?:q|d)$", lower) and any(
        h in text_lower
        for h in (
            "状态", "状态机", "state", "fsm", "计数", "counter",
            "空闲", "idle", "busy", "done", "complete",
        )
    ):
        return True
    return False


def _name_has_timing_hint(signal: str) -> bool:
    lower = signal.lower()

    # Avoid classifying invalid_* as a valid handshake signal.
    if "invalid" not in lower and re.search(r"(?:^|_)(?:[rw]?valid)(?:_|$)", lower):
        return True
    if re.search(r"(?:^|_)(?:[rw]?ready)(?:_|$)", lower):
        return True

    for hint in _TIMING_NAME_HINTS:
        if hint in {"valid", "ready"}:
            continue
        if hint in lower:
            return True
    return False


def _classify_transition(text: str) -> str:
    tl = text.lower()
    assert_hit = any(w in tl for w in (
        "置位", "置为 1", "置为1", "拉高", "拉起", "assert",
        "set to 1", "变为 1", "变为1", "为 1", "为1", "驱动为 1",
    ))
    deassert_hit = any(w in tl for w in (
        "清零", "清除", "置为 0", "置为0", "拉低", "clear",
        "deassert", "set to 0", "为 0", "为0", "驱动为 0",
    ))
    if assert_hit and deassert_hit:
        return "mixed"
    if deassert_hit:
        return "deassert"
    if assert_hit:
        return "assert"
    if any(w in tl for w in ("装载", "更新", "load", "update", "<= ", "被赋值", "驱动为")):
        return "load"
    if any(w in tl for w in ("保持", "不变", "hold", "remain", "keep")):
        return "hold"
    return "unknown"


def _classify_timing(text: str, chunk_kind: str, transition: str) -> str:
    tl = text.lower()
    if any(w in tl for w in ("计数", "等待", "延迟", "额外等待")) or _has_english_word(
        tl, ("counter", "wait", "delay")
    ):
        return "delayed_counter"
    if any(w in tl for w in ("立即", "同时", "same cycle", "combin", "立即生效")):
        return "same_cycle"
    if chunk_kind in ("always_comb", "assign") and transition not in ("hold", "unknown"):
        return "same_cycle"
    if any(w in tl for w in ("下一拍", "时钟沿", "next cycle", "next-cycle")):
        return "next_ff"
    if chunk_kind == "always_ff":
        return "next_ff"
    if any(w in tl for w in ("状态机", "fsm", "阶段", "phase")):
        return "fsm_phase"
    return "unknown"


def _classify_event(signal: str, text: str) -> str:
    sl = signal.lower()
    tl = text.lower()
    for kw, ec in _EVENT_MAP.items():
        if kw in sl:
            return ec
    for kw, ec in _EVENT_MAP.items():
        if kw in tl:
            return ec
    return "unknown"


def _has_timing_relevance(signal: str, text: str) -> bool:
    tl = f"{signal} {text}".lower()
    return any(h in tl for h in _TIMING_TEXT_HINTS)


def _is_reset_atom(atom: dict[str, Any]) -> bool:
    text = atom.get("property_snippet", "").lower()
    return any(h in text for h in _RESET_TEXT_HINTS)


def _atom_rank_for_pair(
    atom: dict[str, Any],
    peer_atoms: list[dict[str, Any]],
    anchor: str,
) -> tuple[float, int, int, int, int]:
    """Rank atom evidence for an already-selected pair.

    Prefer facts connected to the shared anchor and avoid reset/default facts
    unless there is no better statement for the signal.
    """
    signal = atom.get("signal", "")
    signal_lower = signal.lower()
    conditions = set(atom.get("condition_signals", []))
    peer_events = {a.get("event_class") for a in peer_atoms}
    peer_timings = {a.get("timing_class") for a in peer_atoms}
    rank = 0.0

    if anchor in conditions:
        rank += 8
    if atom.get("source_field") == "guarantee":
        rank += 4
    else:
        rank -= 2
    if _is_reset_atom(atom):
        rank -= 8
    if atom.get("timing_class") in ("same_cycle", "next_ff", "delayed_counter", "fsm_phase"):
        rank += 2
    if atom.get("event_class") in peer_events:
        rank += 2
    if _is_busy_done_semantic(signal_lower, peer_events):
        rank += 3
    if _timing_conflicts(atom.get("timing_class"), peer_timings):
        rank += 2

    condition_count = len(conditions)
    non_reset = 0 if _is_reset_atom(atom) else 1
    high_conf = 1 if atom.get("source_field") == "guarantee" else 0
    snippet_len = len(atom.get("property_snippet", ""))
    return (rank, non_reset, high_conf, condition_count, snippet_len)


def _is_busy_done_semantic(signal_lower: str, peer_events: set[str]) -> bool:
    if any(h in signal_lower for h in _BUSY_SIGNALS) and peer_events & {"done", "idle"}:
        return True
    if any(h in signal_lower for h in _COMPLETION_SIGNALS) and peer_events & {"busy", "process"}:
        return True
    return False


def _timing_conflicts(timing: str | None, peer_timings: set[str]) -> bool:
    if timing in {"same_cycle", "next_ff"} and peer_timings & {"delayed_counter", "fsm_phase"}:
        return True
    if timing in {"delayed_counter", "fsm_phase"} and peer_timings & {"same_cycle", "next_ff"}:
        return True
    return False


def _select_atom_for_pair(
    atoms: list[dict[str, Any]],
    peer_atoms: list[dict[str, Any]],
    anchor: str,
) -> dict[str, Any]:
    return max(atoms, key=lambda a: _atom_rank_for_pair(a, peer_atoms, anchor))


def _rank_atoms_for_pair(
    atoms: list[dict[str, Any]],
    peer_atoms: list[dict[str, Any]],
    anchor: str,
) -> list[dict[str, Any]]:
    return sorted(
        atoms,
        key=lambda a: _atom_rank_for_pair(a, peer_atoms, anchor),
        reverse=True,
    )


def _mentions_signal(text: str, signal: str) -> bool:
    """Return true when *signal* appears as an identifier, not a substring."""
    if not text or not signal:
        return False

    aliases = [signal]
    if "[" in signal:
        aliases.append(signal.split("[", 1)[0])

    for alias in dict.fromkeys(aliases):
        pattern = rf"(?<![A-Za-z0-9_$]){re.escape(alias)}(?![A-Za-z0-9_$])"
        if re.search(pattern, text):
            return True
    return False


def _condition_signals(
    graph: SignalGraph,
    spec_id: str,
    text: str,
    output_signals: list[str],
) -> list[str]:
    """Extract likely input/condition signals for one spec statement."""
    outputs = set(output_signals)
    candidates = list(graph.spec_signals.get(spec_id, []))

    # Some Phase 1 specs mention a condition in guarantee text even if the
    # SignalGraph text pass missed it.  Fall back to the global signal list
    # while preserving spec-local candidates first.
    if text:
        for sig in graph.signals:
            if sig not in candidates:
                candidates.append(sig)

    conditions: list[str] = []
    for sig in candidates:
        if sig in outputs:
            continue
        if _is_structural_signal(sig):
            continue
        if _mentions_signal(text, sig):
            conditions.append(sig)
    return list(dict.fromkeys(conditions))


def _extract_timing_atoms(graph: SignalGraph) -> list[dict[str, Any]]:
    """Extract compact timing facts from Phase 1 specs.

    Guarantees are high-confidence producer facts.  Assumptions are included
    only when timing-related and are marked lower confidence so they can help
    LLM context without creating hard causal edges.
    """
    atoms: list[dict[str, Any]] = []
    seen: set[str] = set()

    for spec_id, spec in graph.specs.items():
        meta = graph.spec_meta.get(spec_id, {})
        chunk_kind = meta.get("kind", "")
        module = meta.get("module", "")

        behavior = spec.get("behavior", "")

        for g in spec.get("guarantees", []):
            prop = g.get("property", "")
            sigs = g.get("output_signals", [])
            if not sigs:
                continue

            conditions = _condition_signals(graph, spec_id, prop, sigs)

            for signal in sigs:
                if not _is_timing_signal(signal, f"{prop}\n{behavior}"):
                    continue
                key = f"guarantee::{spec_id}::{signal}::{prop[:80]}"
                if key in seen:
                    continue
                seen.add(key)

                transition = _classify_transition(prop)
                timing_class = _classify_timing(prop, chunk_kind, transition)
                event_class = _classify_event(signal, prop)

                atoms.append({
                    "signal": signal,
                    "spec_id": spec_id,
                    "module": module,
                    "transition": transition,
                    "timing_class": timing_class,
                    "event_class": event_class,
                    "chunk_kind": chunk_kind,
                    "source_field": "guarantee",
                    "confidence": 1.0,
                    "property_snippet": prop[:300],
                    "condition_signals": conditions,
                    "output_signals": sigs,
                    "source_refs": g.get("source_refs", []),
                })

        for a in spec.get("assumptions", []):
            constraint = a.get("constraint", "")
            bug_rel = a.get("bug_relevance", "")
            text = f"{constraint}\n{bug_rel}"
            related = a.get("related_signals", [])
            if not related:
                continue

            for signal in related:
                if not _is_timing_signal(signal, text) or not _has_timing_relevance(signal, text):
                    continue
                key = f"assumption::{spec_id}::{signal}::{constraint[:80]}"
                if key in seen:
                    continue
                seen.add(key)

                other_related = [s for s in related if s != signal]
                conditions = _condition_signals(
                    graph, spec_id, text, [signal]
                )
                for rel in other_related:
                    if rel not in conditions:
                        conditions.append(rel)

                transition = _classify_transition(text)
                timing_class = _classify_timing(text, chunk_kind, transition)
                event_class = _classify_event(signal, text)
                atoms.append({
                    "signal": signal,
                    "spec_id": spec_id,
                    "module": module,
                    "transition": transition,
                    "timing_class": timing_class,
                    "event_class": event_class,
                    "chunk_kind": chunk_kind,
                    "source_field": "assumption",
                    "confidence": 0.5,
                    "property_snippet": constraint[:300],
                    "condition_signals": conditions,
                    "output_signals": [],
                    "source_refs": a.get("source_refs", []),
                })

    return atoms


# ------------------------------------------------------------------
# Stage 2: Causal index
# ------------------------------------------------------------------


def _build_signal_adjacency(
    atoms: list[dict[str, Any]],
) -> dict[str, dict[str, list[str]]]:
    """Build direct signal edges from Phase 1 condition→output facts.

    This deliberately ignores SignalInfo.drivers/consumers because those are
    spec chunk ids, not signal ids.  Only high-confidence guarantee atoms form
    hard causal edges; assumption atoms remain context for the LLM.
    """
    forward: dict[str, set[str]] = defaultdict(set)
    backward: dict[str, set[str]] = defaultdict(set)

    for atom in atoms:
        if atom.get("source_field") != "guarantee":
            continue
        dst = atom.get("signal")
        if not dst:
            continue
        for src in atom.get("condition_signals", []):
            if not src or src == dst:
                continue
            forward[src].add(dst)
            backward[dst].add(src)

    signals = set(forward) | set(backward)
    return {
        sig: {
            "forward": sorted(forward.get(sig, set())),
            "backward": sorted(backward.get(sig, set())),
        }
        for sig in signals
    }


def _build_causal_index(
    graph: SignalGraph,
    atoms: list[dict[str, Any]],
    max_depth: int = 4,
) -> dict[str, dict[str, list[str]]]:
    """For each signal, compute direct edges and bounded causal cones."""
    index: dict[str, dict[str, list[str]]] = {}
    adjacency = _build_signal_adjacency(atoms)

    def _traverse(start: str, direction: str) -> list[str]:
        """direction = 'forward' (condition→output) or reverse."""
        visited: set[str] = set()
        queue = deque([(start, 0)])
        result: list[str] = []
        while queue:
            node, depth = queue.popleft()
            if depth > max_depth:
                continue
            if node in visited:
                continue
            visited.add(node)
            if node != start:
                result.append(node)
            neighbours = adjacency.get(node, {}).get(direction, [])
            for nb in neighbours:
                if nb not in visited:
                    queue.append((nb, depth + 1))
        return result

    for sig in graph.signals:
        direct = adjacency.get(sig, {})
        index[sig] = {
            "forward_edges": direct.get("forward", []),
            "backward_edges": direct.get("backward", []),
            "forward": _traverse(sig, "forward"),
            "backward": _traverse(sig, "backward"),
        }

    return index


# ------------------------------------------------------------------
# Stage 3: Anchor generation
# ------------------------------------------------------------------

_ANCHOR_HIGH_PRI = {
    "done", "complete", "finish", "stop", "start", "continue",
    "process", "hash_done", "hash_start", "hash_stop", "hash_process",
}
_ANCHOR_PENALTY = {"clk", "rst", "reset", "enable"}


def _is_low_value_anchor_name(signal: str) -> bool:
    lower = signal.lower()
    if any(p in lower for p in ("clk", "rst", "reset")):
        return True
    if lower in {"en", "enable"}:
        return True
    if re.search(r"(?:^|_)[a-z0-9]+_en(?:_i|_o|_q|_d)?$", lower):
        return True
    if lower.endswith(("_en_i", "_en_o", "_en_q", "_en_d")):
        return True
    return lower.endswith("_en")


def _generate_anchors(
    graph: SignalGraph,
    atoms: list[dict[str, Any]],
    causal_index: dict[str, dict[str, list[str]]],
) -> list[dict[str, Any]]:
    """Score signals as event anchors based on their causal role."""
    # Index atoms by signal
    atom_by_signal: dict[str, list[dict]] = defaultdict(list)
    condition_hits: dict[str, list[dict]] = defaultdict(list)
    for a in atoms:
        atom_by_signal[a["signal"]].append(a)
        if a.get("source_field") == "guarantee":
            for cond in a.get("condition_signals", []):
                condition_hits[cond].append(a)

    anchors: list[dict[str, Any]] = []

    for sig, info in graph.signals.items():
        score = 0.0
        reasons: list[str] = []
        lower = sig.lower()

        # Penalise global/clock/reset signals
        if _is_low_value_anchor_name(sig) or any(p in lower for p in _ANCHOR_PENALTY):
            continue

        ci = causal_index.get(sig, {})
        fwd = ci.get("forward", [])
        direct_fwd = ci.get("forward_edges", [])

        # +4: appears as a condition/source for multiple timing facts
        hits = condition_hits.get(sig, [])
        if len(hits) >= 2:
            score += min(len(hits), 10) * 4
            reasons.append(f"condition_hits={len(hits)}")

        # +2: the signal itself has temporal specs
        my_atoms = atom_by_signal.get(sig, [])
        if len(my_atoms) >= 2:
            score += min(len(my_atoms), 6) * 2
            reasons.append(f"timing_atoms={len(my_atoms)}")

        # +3: reaches multiple temporal signals in forward cone
        temporal_reached = [s for s in fwd if s in atom_by_signal]
        if len(temporal_reached) >= 2:
            score += min(len(temporal_reached), 10) * 3
            reasons.append(f"reaches_temporal={len(temporal_reached)}")

        # +2: direct fanout into multiple timing outputs
        direct_temporal = [s for s in direct_fwd if s in atom_by_signal]
        if len(direct_temporal) >= 2:
            score += min(len(direct_temporal), 8) * 2
            reasons.append(f"direct_temporal={len(direct_temporal)}")

        # +2: name matches high-priority anchor keyword
        if any(kw in lower for kw in _ANCHOR_HIGH_PRI):
            score += 2
            reasons.append("anchor_keyword")

        # +2: feeds sw-visible status or interrupt
        if "intr_" in lower or "status_" in lower or "hw2reg" in lower:
            score += 2
            reasons.append("sw_visible")

        # +2: is an FSM state or counter compare
        if sig.endswith("_q") or "state" in lower or "cnt" in lower:
            score += 2
            reasons.append("fsm_or_counter")

        # +1: moderate real fanout
        if 2 <= len(direct_fwd) <= 20:
            score += 1

        # -2: assertion-only or lint signal
        if "assert" in lower or "unused" in lower:
            score -= 5

        if score >= 4:
            anchors.append({
                "signal": sig,
                "score": score,
                "reasons": reasons,
                "temporal_reached": temporal_reached[:10],
                "direct_temporal": direct_temporal[:10],
                "module": atom_by_signal.get(sig, [{}])[0].get("module", ""),
            })

    anchors.sort(key=lambda a: -a["score"])
    return anchors[:30]  # top 30 anchors


# ------------------------------------------------------------------
# Stage 4: Anchor-supervised pair generation
# ------------------------------------------------------------------


def _generate_pairs(
    anchors: list[dict[str, Any]],
    atoms: list[dict[str, Any]],
    causal_index: dict[str, dict[str, list[str]]],
    max_pairs: int = 150,
    max_neighbourhood: int = 24,
) -> list[dict[str, Any]]:
    """Generate candidate (signal_a, signal_b) pairs around each anchor."""
    atom_by_signal: dict[str, list[dict]] = defaultdict(list)
    for a in atoms:
        atom_by_signal[a["signal"]].append(a)

    pairs: list[dict[str, Any]] = []
    best_by_pair: dict[tuple[str, str], dict[str, Any]] = {}

    for anchor in anchors:
        anchor_sig = anchor["signal"]
        ci = causal_index.get(anchor_sig, {})
        fwd = ci.get("forward", [])
        bwd = ci.get("backward", [])

        # Collect temporal signals in the anchor's causal neighbourhood
        neighbourhood = [s for s in fwd + bwd if s in atom_by_signal]
        # Also include the anchor itself if it has atoms
        if anchor_sig in atom_by_signal:
            neighbourhood.append(anchor_sig)

        neighbourhood = list(dict.fromkeys(neighbourhood))  # dedup
        neighbourhood.sort(
            key=lambda sig: _signal_relevance_to_anchor(
                sig, anchor_sig, atom_by_signal.get(sig, []), causal_index
            ),
            reverse=True,
        )
        neighbourhood = neighbourhood[:max_neighbourhood]

        for i in range(len(neighbourhood)):
            for j in range(i + 1, len(neighbourhood)):
                sa = neighbourhood[i]
                sb = neighbourhood[j]
                if sa == sb:
                    continue

                atoms_a = atom_by_signal.get(sa, [])
                atoms_b = atom_by_signal.get(sb, [])

                if not atoms_a or not atoms_b:
                    continue

                ranked_atoms_a = _rank_atoms_for_pair(atoms_a, atoms_b, anchor_sig)
                ranked_atoms_b = _rank_atoms_for_pair(atoms_b, atoms_a, anchor_sig)
                atom_a = ranked_atoms_a[0]
                atom_b = ranked_atoms_b[0]

                # Build direct-edge path evidence from anchor to each signal.
                path_a = _path_between(anchor_sig, sa, causal_index)
                path_b = _path_between(anchor_sig, sb, causal_index)

                # Risk score considers alternate timing facts too.  A signal
                # can have both immediate and delayed guarantees for different
                # paths of the same event; this is exactly what Channel D must
                # surface to the LLM.
                score = _score_pair_set(
                    ranked_atoms_a[:5],
                    ranked_atoms_b[:5],
                    anchor_sig,
                    path_a,
                    path_b,
                )

                if score >= 1:
                    pair = {
                        "anchor": anchor_sig,
                        "anchor_score": anchor["score"],
                        "anchor_reasons": anchor.get("reasons", []),
                        "signal_a": sa,
                        "signal_b": sb,
                        "path_a": path_a or [sa],
                        "path_b": path_b or [sb],
                        "atoms_a": atom_a,
                        "atoms_b": atom_b,
                        "all_atoms_a": ranked_atoms_a[:5],
                        "all_atoms_b": ranked_atoms_b[:5],
                        "risk_score": score,
                        "module": anchor.get("module", ""),
                    }
                    key = tuple(sorted((sa, sb)))
                    prev = best_by_pair.get(key)
                    if (
                        prev is None
                        or pair["risk_score"] > prev["risk_score"]
                        or (
                            pair["risk_score"] == prev["risk_score"]
                            and pair["anchor_score"] > prev["anchor_score"]
                        )
                    ):
                        best_by_pair[key] = pair

    pairs = list(best_by_pair.values())
    pairs.sort(key=lambda p: -p["risk_score"])
    return pairs[:max_pairs]


def _signal_relevance_to_anchor(
    signal: str,
    anchor: str,
    atoms: list[dict[str, Any]],
    causal_index: dict[str, dict[str, list[str]]],
) -> tuple[int, int, int, int, int]:
    ci = causal_index.get(anchor, {})
    direct = set(ci.get("forward_edges", [])) | set(ci.get("backward_edges", []))
    conditions_hit = any(anchor in a.get("condition_signals", []) for a in atoms)
    high_conf = any(a.get("source_field") == "guarantee" for a in atoms)
    name = signal.lower()
    event_name = any(
        h in name
        for h in (
            _COMPLETION_SIGNALS
            + _BUSY_SIGNALS
            + _START_STOP_SIGNALS
            + _HANDSHAKE_SIGNALS
            + _COUNTER_SIGNALS
        )
    )
    return (
        1 if signal in direct else 0,
        1 if conditions_hit else 0,
        1 if high_conf else 0,
        1 if event_name else 0,
        len(atoms),
    )


def _shortest_path(
    start: str, target: str, causal_index: dict[str, dict[str, list[str]]]
) -> list[str]:
    """BFS shortest path from start to target through direct causal edges."""
    if start == target:
        return []
    visited: set[str] = set()
    queue = deque([(start, [start])])
    while queue:
        node, path = queue.popleft()
        if node == target:
            return path
        if node in visited:
            continue
        visited.add(node)
        ci = causal_index.get(node, {})
        neighbours = ci.get("forward_edges", []) + ci.get("backward_edges", [])
        for nb in neighbours:
            if nb not in visited:
                queue.append((nb, path + [nb]))
    return []  # no path found


def _path_between(
    anchor: str,
    signal: str,
    causal_index: dict[str, dict[str, list[str]]],
) -> list[str]:
    """Prefer anchor→signal path, fall back to signal→anchor if reversed."""
    if anchor == signal:
        return [signal]
    forward = _shortest_path(anchor, signal, causal_index)
    if forward:
        return forward
    backward = _shortest_path(signal, anchor, causal_index)
    if backward:
        return list(reversed(backward))
    return []


def _score_pair(
    atom_a: dict, atom_b: dict, anchor: str,
    path_a: list[str], path_b: list[str],
) -> float:
    """Compute risk score for a candidate pair."""
    score = 0.0

    ta = atom_a.get("timing_class", "unknown")
    tb = atom_b.get("timing_class", "unknown")
    ea = atom_a.get("event_class", "unknown")
    eb = atom_b.get("event_class", "unknown")

    # +3: immediate vs delayed
    if ta in ("same_cycle", "next_ff") and tb in (
        "delayed_counter", "hold_until", "fsm_phase",
    ):
        score += 3
    elif tb in ("same_cycle", "next_ff") and ta in (
        "delayed_counter", "hold_until", "fsm_phase",
    ):
        score += 3

    # +3: busy/process vs done/complete
    busy_done_pairs = (
        (ea in ("busy", "process") and eb in ("done", "idle"))
        or (eb in ("busy", "process") and ea in ("done", "idle"))
    )
    if busy_done_pairs:
        score += 3

    # +2: shared anchor
    if anchor:
        score += 2

    # +2: asymmetric path lengths
    if abs(len(path_a) - len(path_b)) >= 2:
        score += 2

    # +2: one path has counter delay, the other doesn't
    if ("counter" in str(atom_a) or "counter" in str(path_a)) != (
        "counter" in str(atom_b) or "counter" in str(path_b)
    ):
        score += 2

    # -2: both are same-cycle (likely normal pipelining)
    if ta == "same_cycle" and tb == "same_cycle":
        score -= 2

    # -2: both are valid/ready (handshake pair)
    if ea == "valid" and eb == "ready":
        score -= 2

    return score


def _score_pair_set(
    atoms_a: list[dict[str, Any]],
    atoms_b: list[dict[str, Any]],
    anchor: str,
    path_a: list[str],
    path_b: list[str],
) -> float:
    """Score a pair using the strongest relevant atom combination."""
    best = 0.0
    for atom_a in atoms_a:
        for atom_b in atoms_b:
            score = _score_pair(atom_a, atom_b, anchor, path_a, path_b)
            if anchor in atom_a.get("condition_signals", []):
                score += 1
            if anchor in atom_b.get("condition_signals", []):
                score += 1
            if _is_reset_atom(atom_a):
                score -= 2
            if _is_reset_atom(atom_b):
                score -= 2
            best = max(best, score)
    return best


# ------------------------------------------------------------------
# Main runner
# ------------------------------------------------------------------


def run_channel_d(
    graph: SignalGraph,
    client: OpenAICompatibleClient,
    prompt_path: str | Path = DEFAULT_PROMPT,
    max_tokens: int = 10000,
    top_n_pairs: int = 50,
) -> list[dict[str, Any]]:
    """Run anchor-supervised temporal consistency detection."""
    prompt_template = Path(prompt_path).read_text(encoding="utf-8")

    print("  Channel D [stage1] extracting timing atoms ...")
    atoms = _extract_timing_atoms(graph)
    high_conf = sum(1 for a in atoms if a.get("source_field") == "guarantee")
    low_conf = len(atoms) - high_conf
    print(f"    {len(atoms)} timing atoms ({high_conf} guarantees, {low_conf} assumptions)")

    print("  Channel D [stage2] building causal index ...")
    causal_index = _build_causal_index(graph, atoms, max_depth=6)

    print("  Channel D [stage3] generating anchors ...")
    anchors = _generate_anchors(graph, atoms, causal_index)
    print(f"    {len(anchors)} anchors (top: "
          f"{', '.join(a['signal'] for a in anchors[:5])})")

    print("  Channel D [stage4] generating pairs ...")
    pairs = _generate_pairs(anchors, atoms, causal_index, max_pairs=top_n_pairs * 3)
    pairs = pairs[:top_n_pairs]
    print(f"    {len(pairs)} candidate pairs (top risk={pairs[0]['risk_score']:.1f})"
          if pairs else "    0 pairs generated")

    if not pairs:
        print("  Channel D done: 0 findings (no candidate pairs)")
        return []

    all_findings: list[dict[str, Any]] = []
    races = 0

    for idx, pair in enumerate(pairs[:top_n_pairs]):
        sigs = f"{pair['signal_a']}↔{pair['signal_b']}"
        print(
            f"  Channel D [{idx + 1}/{min(len(pairs), top_n_pairs)}] "
            f"anchor={pair['anchor']} {sigs} (risk={pair['risk_score']:.1f}) ... ",
            end="", flush=True,
        )
        try:
            findings = _check_pair(
                pair, graph, client, prompt_template, max_tokens
            )
        except Exception as exc:
            print(f"ERROR ({exc})")
            continue

        r = sum(1 for f in findings if f.get("verdict") == "RACE")
        races += r
        print(f"{len(findings)} RACE findings")
        all_findings.extend(findings)

    print(
        f"  Channel D done: {len(all_findings)} findings ({races} races)"
    )
    return all_findings


def _check_pair(
    pair: dict[str, Any],
    graph: SignalGraph,
    client: OpenAICompatibleClient,
    prompt_template: str,
    max_tokens: int,
) -> list[dict[str, Any]]:
    """Ask LLM to verify one candidate pair."""
    # Gather spec contexts for both signals
    contexts: list[dict] = []
    for sig_key in ("signal_a", "signal_b"):
        sig = pair[sig_key]
        info = graph.signals.get(sig)
        if not info:
            continue
        related: list[dict] = []
        seen: set[str] = set()
        for spec_id in info.drivers + info.consumers:
            if spec_id in seen:
                continue
            seen.add(spec_id)
            spec = graph.specs.get(spec_id)
            if spec:
                related.append({
                    "spec_id": spec_id,
                    "chunk_kind": graph.spec_meta.get(spec_id, {}).get("kind", ""),
                    "summary": spec.get("summary", ""),
                    "behavior": spec.get("behavior", "")[:600],
                    "guarantees": spec.get("guarantees", []),
                })
        contexts.append({
            "signal": sig,
            "atom": pair.get(f"atoms_{sig_key[-1]}", {}),
            "all_atoms": pair.get(f"all_atoms_{sig_key[-1]}", []),
            "path": pair.get(f"path_{sig_key[-1]}", []),
            "related_specs": related,
        })

    anchor_context: list[dict] = []
    anchor = pair["anchor"]
    anchor_info = graph.signals.get(anchor)
    if anchor_info:
        seen_anchor_specs: set[str] = set()
        for spec_id in anchor_info.drivers + anchor_info.consumers + anchor_info.mentioned_in:
            if spec_id in seen_anchor_specs:
                continue
            seen_anchor_specs.add(spec_id)
            spec = graph.specs.get(spec_id)
            if spec:
                anchor_context.append({
                    "spec_id": spec_id,
                    "chunk_kind": graph.spec_meta.get(spec_id, {}).get("kind", ""),
                    "summary": spec.get("summary", ""),
                    "behavior": spec.get("behavior", "")[:500],
                    "guarantees": spec.get("guarantees", []),
                    "assumptions": spec.get("assumptions", []),
                })

    payload = {
        "anchor": pair["anchor"],
        "anchor_score": pair.get("anchor_score"),
        "anchor_reasons": pair.get("anchor_reasons", []),
        "anchor_context": anchor_context[:6],
        "risk_score": pair["risk_score"],
        "causal_hypothesis": (
            f"{pair['anchor']} is a shared timing source/context for "
            f"{pair['signal_a']} and {pair['signal_b']}."
        ),
        "signals": contexts,
    }

    content = client.chat(
        messages=[
            {"role": "system", "content": prompt_template},
            {"role": "user",
             "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ],
        max_tokens=max_tokens,
    )

    parsed = _parse_llm_response(content)
    findings = parsed.get("findings", [])
    if not isinstance(findings, list):
        return []

    race_findings = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if finding.get("verdict") != "RACE":
            continue
        finding.setdefault("anchor", pair["anchor"])
        finding.setdefault("signal_pair", [pair["signal_a"], pair["signal_b"]])
        finding["candidate"] = {
            "risk_score": pair.get("risk_score"),
            "anchor_score": pair.get("anchor_score"),
            "path_a": pair.get("path_a", []),
            "path_b": pair.get("path_b", []),
            "atoms_a": pair.get("atoms_a", {}),
            "atoms_b": pair.get("atoms_b", {}),
            "all_atoms_a": pair.get("all_atoms_a", []),
            "all_atoms_b": pair.get("all_atoms_b", []),
        }
        race_findings.append(finding)
    return race_findings


def _parse_llm_response(content: str) -> dict[str, Any]:
    import re
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))
