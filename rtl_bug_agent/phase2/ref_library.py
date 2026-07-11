"""
Reference-library retrieval augmentation (finding → Phase 3)
============================================================

Builds a small library of *official design intent* references from two
sources and attaches the most relevant ones to each finding **before**
Phase 3 verification:

1. **hjson countermeasures** — parsed by rule (``hjson`` lib), zero-LLM.
   OpenTitan IP ``data/<ip>.hjson`` files carry a ``countermeasures[]``
   block naming each SEC_CM (``DM_EN.CTRL.LC_GATED``, ``SW_KEY.KEY.MASKING``,
   ...) with a human-written ``desc``.  These are exactly the design
   intents that deletion-type planted bugs remove — and that Phase 3, reading
   only local RTL, cannot recover on its own.

2. **theory_of_operation.md** — extracted by LLM, reusing the existing
   ``layer2.extract_claims`` prompt/schema.

Both sources produce the same ref record shape.  Each finding is embedded
with BGE-M3 (reusing ``semantic_ag._encode``) and matched against the ref
library by cosine similarity + signal-overlap boost.  Top-k hits land in
``finding["ref_clues"]`` for Phase 3 to consume.

This is fully decoupled from the shelved Layer 2 (Channel G) path: refs are
*clues*, not findings, and never enter scoring or fusion.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from rtl_bug_agent.phase2.layer2 import extract_claims, DEFAULT_OFFICIAL_DOCS
from rtl_bug_agent.phase2.semantic_ag import (
    SemanticAgConfig,
    _encode,
    _l2_normalize,
    _normalize_signal,
)
from rtl_bug_agent.phase2.signal_graph import SignalGraph

# IP → data directory holding the IP's hjson intent files.  ALL *.hjson under
# this directory are scanned (main hjson with countermeasures[], plus testplan
# files with testpoints[]).  Modules without a data dir (e.g. tlul as a shared
# block) fall back to doc-only ref libraries.
DEFAULT_DATA_DIR: dict[str, str] = {
    "kmac": "/home/smy/opentitan/hw/ip/kmac/data",
    "rv_dm": "/home/smy/opentitan/hw/ip/rv_dm/data",
    "hmac": "/home/smy/opentitan/hw/ip/hmac/data",
    "aes": "/home/smy/opentitan/hw/ip/aes/data",
    "keymgr": "/home/smy/opentitan/hw/ip/keymgr/data",
}

# Back-compat: some callers/tests still reference single-file paths.
DEFAULT_SEC_CM_HJSON: dict[str, str] = {
    "kmac": "/home/smy/opentitan/hw/ip/kmac/data/kmac.hjson",
    "rv_dm": "/home/smy/opentitan/hw/ip/rv_dm/data/rv_dm.hjson",
    "hmac": "/home/smy/opentitan/hw/ip/hmac/data/hmac.hjson",
    "aes": "/home/smy/opentitan/hw/ip/aes/data/aes.hjson",
    "keymgr": "/home/smy/opentitan/hw/ip/keymgr/data/keymgr.hjson",
}

# Tokens that appear in SEC_CM names/descs but are never RTL signal names.
_TOKEN_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "are", "via", "not", "any",
    "all", "from", "into", "when", "set", "used", "use", "uses", "which",
    "against", "order", "attack", "attacks", "signal", "signals", "value",
    "mubi", "intersig", "ctrl", "fsm", "redun", "sparse", "config", "sw",
    "integrity", "bus", "key", "reg", "true", "false", "off", "type",
}


# ---------------------------------------------------------------------------
# Signal extraction / normalization
# ---------------------------------------------------------------------------


def _graph_signal_set(graph: SignalGraph | None) -> set[str]:
    """Lower-cased set of real signal names in the graph (hallucination filter)."""
    if graph is None:
        return set()
    return {str(s).lower() for s in graph.signals.keys()}


def _candidate_tokens(name: str, desc: str) -> set[str]:
    """Signal-name candidates from a countermeasure name + desc.

    From ``name`` we split the dotted taxonomy (``DM_EN.CTRL.LC_GATED`` →
    ``dm_en``, ``lc_gated``) and from ``desc`` we pull identifier-like tokens.
    """
    cands: set[str] = set()
    # name: dotted segments, lower-cased
    for seg in name.split("."):
        seg = seg.strip().lower()
        if seg and seg not in _TOKEN_STOPWORDS:
            cands.add(seg)
    # desc: identifier-like tokens (allow trailing _i/_o etc. as written)
    for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", desc):
        low = tok.lower()
        if low not in _TOKEN_STOPWORDS:
            cands.add(low)
    return cands


def _signals_from_cm(
    name: str, desc: str, graph: SignalGraph | None
) -> list[str]:
    """Real signal names implicated by a countermeasure.

    Intersects candidate tokens with the graph's real signal names.  Without a
    graph, falls back to the dotted name segments (still useful as keywords).
    """
    cands = _candidate_tokens(name, desc)
    real = _graph_signal_set(graph)
    if not real:
        # No graph: keep only the name-derived segments (avoid desc noise).
        return sorted(
            seg.strip().lower()
            for seg in name.split(".")
            if seg.strip() and seg.strip().lower() not in _TOKEN_STOPWORDS
        )
    hits = sorted(cands & real)
    return hits


def _normalize_signals(
    signals: list[str], graph: SignalGraph | None
) -> list[str]:
    """Filter LLM-provided doc signals down to real graph signals (lower-cased)."""
    low = [str(s).lower() for s in signals if s]
    real = _graph_signal_set(graph)
    if not real:
        return sorted(set(low))
    return sorted(set(low) & real)


def _keywords_from_cm(name: str) -> list[str]:
    """Keywords from the dotted countermeasure taxonomy."""
    return [seg.strip() for seg in name.split(".") if seg.strip()]


# ---------------------------------------------------------------------------
# Ref library construction
# ---------------------------------------------------------------------------


def _infer_ip_from_path(path: Path) -> str:
    """Infer IP name from a data-dir hjson path (…/hw/ip/<ip>/data/x.hjson).

    Walks up until it finds a ``data`` directory; its parent is the IP dir.
    """
    p = path
    while p.parent != p:
        if p.name == "data":
            return p.parent.name
        p = p.parent
    return ""


def extract_hjson_refs(
    hjson_path: str | Path,
    graph: SignalGraph | None = None,
    ip: str | None = None,
) -> list[dict[str, Any]]:
    """Parse one hjson file into ref records.

    Handles both intent shapes found under an IP's ``data/`` directory:
    - ``countermeasures[]`` (name + desc) → ``kind="sec_cm"``
    - ``testpoints[]`` (name + desc)       → ``kind="testpoint"``

    A single file may contain either or both.  ``ip`` is taken from the doc's
    top-level ``name`` when present, else inferred from the path, else the
    passed-in override.
    """
    import hjson

    path = Path(hjson_path)
    if not path.exists():
        return []
    doc = hjson.load(path.open(encoding="utf-8"))
    if not isinstance(doc, dict):
        return []

    ip_name = str(doc.get("name", "") or "") or (ip or "") or _infer_ip_from_path(path)
    fname = path.name
    refs: list[dict[str, Any]] = []

    # (a) countermeasures[]  — security countermeasure declarations
    for cm in doc.get("countermeasures", []) or []:
        name = str(cm.get("name", "") or "").strip()
        desc = str(cm.get("desc", "") or "").strip()
        if not name or not desc:
            continue
        refs.append({
            "ref_id": f"SECCM-{ip_name}-{name}",
            "text": desc,
            "source": f"SEC_CM: {name}",
            "origin": "hjson",
            "kind": "sec_cm",
            "src_file": fname,
            "signals": _signals_from_cm(name, desc, graph),
            "keywords": _keywords_from_cm(name),
        })

    # (b) testpoints[]  — functional / sec_cm testplan entries. The desc states
    #     the intended behaviour a test must confirm, which encodes design intent.
    for tp in doc.get("testpoints", []) or []:
        name = str(tp.get("name", "") or "").strip()
        desc = str(tp.get("desc", "") or "").strip()
        if not name or not desc:
            continue
        refs.append({
            "ref_id": f"TP-{ip_name}-{fname}-{name}",
            "text": desc,
            "source": f"testpoint: {name} ({fname})",
            "origin": "hjson",
            "kind": "testpoint",
            "src_file": fname,
            "signals": _signals_from_cm(name, desc, graph),
            "keywords": _keywords_from_cm(name),
        })

    return refs


def extract_data_dir_refs(
    data_dir: str | Path,
    graph: SignalGraph | None = None,
    ip: str | None = None,
) -> list[dict[str, Any]]:
    """Scan ALL ``*.hjson`` under a data directory into ref records.

    De-duplicates by ``ref_id`` (a countermeasure named in both the main hjson
    and a sec_cm testplan yields two distinct refs — different phrasing — but
    identical entries in the same file are collapsed).
    """
    d = Path(data_dir)
    if not d.is_dir():
        return []
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hj in sorted(d.glob("*.hjson")):
        try:
            for r in extract_hjson_refs(hj, graph, ip=ip):
                if r["ref_id"] in seen:
                    continue
                seen.add(r["ref_id"])
                refs.append(r)
        except Exception as exc:  # one malformed file must not sink the rest
            print(f"  Ref library: skip {hj.name} ({exc})")
    return refs


def extract_doc_refs(
    doc_path: str | Path,
    client: Any,
    graph: SignalGraph | None = None,
) -> list[dict[str, Any]]:
    """Extract theory_of_operation claims (LLM) and wrap them as ref records."""
    path = Path(doc_path)
    if not path.exists():
        return []
    claims = extract_claims(path, client)
    refs: list[dict[str, Any]] = []
    for i, c in enumerate(claims):
        text = str(c.get("claim", "") or "").strip()
        if not text:
            continue
        refs.append({
            "ref_id": f"DOC-{i:04d}",
            "text": text,
            "source": f"doc: {c.get('source', '')}",
            "origin": "doc",
            "kind": "doc_claim",
            "src_file": Path(doc_path).name,
            "signals": _normalize_signals(c.get("signals", []) or [], graph),
            "keywords": [str(k) for k in (c.get("keywords", []) or [])],
        })
    return refs


def build_ref_library(
    ip: str,
    client: Any,
    graph: SignalGraph | None = None,
    data_dirs: dict[str, str] | None = None,
    doc_paths: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build the combined (all-hjson + doc) ref library for an IP.

    hjson side scans EVERY ``*.hjson`` under the IP's ``data/`` directory
    (main countermeasures + all testplan testpoints).  doc side extracts
    theory-of-operation claims via LLM (best-effort).
    """
    data_map = data_dirs or DEFAULT_DATA_DIR
    doc_map = doc_paths or DEFAULT_OFFICIAL_DOCS
    refs: list[dict[str, Any]] = []
    if ip in data_map:
        refs += extract_data_dir_refs(data_map[ip], graph, ip=ip)
    if ip in doc_map:
        try:
            refs += extract_doc_refs(doc_map[ip], client, graph)
        except Exception as exc:  # doc extraction is best-effort
            print(f"  Ref library: doc extraction failed ({exc}); hjson-only")
    return refs


# ---------------------------------------------------------------------------
# Retrieval: finding → top-k refs
# ---------------------------------------------------------------------------


def _finding_query_text(f: dict[str, Any]) -> str:
    parts = [
        str(f.get("title", "") or ""),
        str(f.get("contradiction", "") or ""),
        " ".join(str(s) for s in f.get("involved_signals", []) or []),
    ]
    return "\n".join(p for p in parts if p)


def attach_refs(
    findings: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    config: SemanticAgConfig | None = None,
    top_k: int = 3,
    min_score: float = 0.5,
    signal_boost: float = 0.3,
) -> list[dict[str, Any]]:
    """Attach top-k relevant refs to each finding as ``ref_clues``.

    Score = BGE-M3 cosine(finding, ref) + ``signal_boost`` * |shared signals|.
    Signals are compared in NORMALIZED space (``_normalize_signal`` strips
    ``_i``/``_o``/``_q``/``_d`` port-direction suffixes) so that an RTL port
    name like ``lc_hw_debug_en_i`` matches the bare ``lc_hw_debug_en`` written
    in an hjson countermeasure desc.  Refs below ``min_score`` are dropped.
    Mutates and returns *findings*.
    """
    if not refs or not findings:
        for f in findings:
            f.setdefault("ref_clues", [])
        return findings

    cfg = config or SemanticAgConfig()

    ref_texts = [r["text"] for r in refs]
    ref_vecs = _l2_normalize(
        _encode(cfg.model_name, ref_texts, cfg.batch_size,
                cfg.use_fp16, cfg.hf_home, cfg.offline)
    )

    q_texts = [_finding_query_text(f) for f in findings]
    q_vecs = _l2_normalize(
        _encode(cfg.model_name, q_texts, cfg.batch_size,
                cfg.use_fp16, cfg.hf_home, cfg.offline)
    )

    ref_sig_sets = [
        {_normalize_signal(str(s).lower()) for s in r.get("signals", []) or []}
        for r in refs
    ]

    for f, qv in zip(findings, q_vecs):
        f_sigs = {
            _normalize_signal(str(s).lower())
            for s in f.get("involved_signals", []) or []
        }
        scored: list[tuple[float, float, int, dict[str, Any]]] = []
        for r, rv, r_sigs in zip(refs, ref_vecs, ref_sig_sets):
            cos = float(qv @ rv)
            overlap = len(f_sigs & r_sigs)
            score = cos + signal_boost * overlap
            if score >= min_score:
                scored.append((score, cos, overlap, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        f["ref_clues"] = [
            {
                "ref_id": r["ref_id"],
                "source": r["source"],
                "text": r["text"],
                "origin": r["origin"],
                "score": round(score, 3),
                "cosine": round(cos, 3),
                "signal_overlap": overlap,
            }
            for score, cos, overlap, r in scored[:top_k]
        ]
    return findings
