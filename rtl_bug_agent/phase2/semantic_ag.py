"""Semantic A-G retrieval for Phase 2.

This module is intentionally self-contained and optional.  The legacy
signal-graph pairing path remains the default; callers opt into this module
when they want BGE-M3 based candidate recall.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from rtl_bug_agent.phase2.signal_graph import SignalGraph
from rtl_bug_agent.phase2.formal_sketch import build_formal_sketch, merge_formal_sketch


@dataclass(frozen=True)
class SemanticAgConfig:
    model_name: str = "BAAI/bge-m3"
    hf_home: str | None = "/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/out/hf_cache"
    offline: bool = True
    batch_size: int = 16
    use_fp16: bool = True
    assumption_top_k: int = 5
    uncertain_top_k: int = 3
    assumption_min_score: float = 0.66
    uncertain_min_score_with_signal: float = 0.66
    uncertain_dense_fallback: float = 0.82
    dense_weight: float = 0.8
    signal_weight: float = 0.2
    # v2.0 §3.2: pairing is purely semantic (dense + signal). Formal similarity
    # no longer drives ranking; default 0.0. Set >0 only to experiment with
    # formal-aware ranking. Conflict is reported via match.diagnostics regardless.
    formal_weight: float = 0.0
    exclude_same_spec: bool = True


@dataclass(frozen=True)
class SemanticBatchConfig:
    mode: str = "single"
    max_queries: int = 5
    max_prompt_tokens: int = 5500
    max_dense_fallback_uncertain: int = 1
    min_shared_roots: int = 1
    max_signal_roots: int = 4


def _get_text(item: dict[str, Any]) -> str:
    """Get text field from new format (claim) or old format (property/constraint)."""
    return str(item.get("claim") or item.get("property") or item.get("constraint") or "")


def _get_signals(item: dict[str, Any]) -> list[str]:
    """Get signals from new format (signals) or old format (output_signals/related_signals)."""
    sigs = item.get("signals") or item.get("output_signals") or item.get("related_signals") or []
    return list(sigs) if isinstance(sigs, list) else []


def _get_risk(item: dict[str, Any]) -> str:
    """Get risk field from new format (risk) or old format (bug_relevance)."""
    return str(item.get("risk") or item.get("bug_relevance") or "")


def _get_source_refs(item: dict[str, Any]) -> list[str]:
    """Get source refs from new format (refs) or old format (source_refs)."""
    refs = item.get("refs") or item.get("source_refs") or []
    return list(refs) if isinstance(refs, list) else []


def build_atoms(graph: SignalGraph) -> list[dict[str, Any]]:
    """Build assumption / guarantee / uncertain atoms from Phase-1 specs.

    Handles both old format (property/constraint/output_signals/related_signals)
    and new structured format (claim/signals/risk/refs).
    """
    atoms: list[dict[str, Any]] = []
    for chunk_id in sorted(graph.specs):
        spec = graph.specs[chunk_id]

        for idx, assumption in enumerate(spec.get("assumptions", []) or []):
            if not isinstance(assumption, dict):
                continue
            text = _get_text(assumption)
            if not text:
                continue
            risk = _get_risk(assumption)
            full_text = "\n".join(
                part for part in [text, f"risk: {risk}"] if part
            )
            formal_sketch = merge_formal_sketch(
                assumption.get("formal_sketch"),
                build_formal_sketch(assumption, spec=spec, role="assumption"),
            )
            atoms.append(
                _make_atom(
                    spec,
                    "assumption",
                    idx,
                    full_text,
                    _get_signals(assumption),
                    _get_source_refs(assumption),
                    {"raw": assumption, "formal_sketch": formal_sketch},
                )
            )

        for idx, guarantee in enumerate(spec.get("guarantees", []) or []):
            if not isinstance(guarantee, dict):
                continue
            text = _get_text(guarantee)
            if not text:
                continue
            formal_sketch = merge_formal_sketch(
                guarantee.get("formal_sketch"),
                build_formal_sketch(guarantee, spec=spec, role="guarantee"),
            )
            atoms.append(
                _make_atom(
                    spec,
                    "guarantee",
                    idx,
                    text,
                    _get_signals(guarantee),
                    _get_source_refs(guarantee),
                    {"raw": guarantee, "formal_sketch": formal_sketch},
                )
            )

        for idx, point in enumerate(spec.get("uncertain_points", []) or []):
            if isinstance(point, dict):
                # New structured format
                text = str(point.get("claim", "")).strip()
                if not text:
                    continue
                signals = _get_signals(point)
                formal_sketch = merge_formal_sketch(
                    point.get("formal_sketch"),
                    build_formal_sketch(point, spec=spec, role="uncertain"),
                )
            else:
                # Old string format
                text = str(point).strip()
                if not text:
                    continue
                signals = sorted(set(re.findall(r"`([^`]+)`", text)))
                formal_sketch = build_formal_sketch(
                    {"claim": text, "signals": signals, "cond": ""},
                    spec=spec,
                    role="uncertain",
                )
            atoms.append(
                _make_atom(
                    spec,
                    "uncertain",
                    idx,
                    text,
                    signals,
                    _get_source_refs(point) if isinstance(point, dict) else spec.get("evidence_refs", []),
                    {"raw": point, "formal_sketch": formal_sketch},
                )
            )

    return atoms


def pair_atoms(
    atoms: list[dict[str, Any]],
    embeddings: np.ndarray,
    config: SemanticAgConfig,
    graph: SignalGraph | None = None,
) -> dict[str, Any]:
    """Select semantic A-G candidates using dense score + signal relation."""
    id_to_index = {atom["atom_id"]: i for i, atom in enumerate(atoms)}
    queries = [atom for atom in atoms if atom["kind"] in ("assumption", "uncertain")]
    guarantees = [atom for atom in atoms if atom["kind"] == "guarantee"]

    dense_weight, signal_weight, formal_weight = _normalised_weights(config)
    results: list[dict[str, Any]] = []
    selected_pairs: list[dict[str, Any]] = []

    for query in queries:
        qi = id_to_index[query["atom_id"]]
        rows = []
        for cand in guarantees:
            if config.exclude_same_spec and cand["spec_id"] == query["spec_id"]:
                continue
            dense = float(embeddings[id_to_index[cand["atom_id"]]] @ embeddings[qi])
            sig_rel, sig_kind, shared = _signal_relation(query, cand)
            formal_rel, formal_kind, formal_shared, formal_diag = _formal_relation(query, cand)
            pair_type = _pair_type(query, sig_rel, dense, config)
            if pair_type is None:
                continue

            score = (
                dense_weight * dense
                + signal_weight * sig_rel
                + formal_weight * formal_rel
            )
            if pair_type == "normal" and score < config.assumption_min_score:
                continue
            if (
                pair_type == "uncertain_with_signal"
                and score < config.uncertain_min_score_with_signal
            ):
                continue
            if (
                pair_type == "uncertain_dense_fallback"
                and dense < config.uncertain_dense_fallback
            ):
                continue

            rows.append(
                {
                    "score": float(score),
                    "dense_score": dense,
                    "signal_relation_score": float(sig_rel),
                    "signal_relation_kind": sig_kind,
                    "shared_signals": shared,
                    "formal_relation_score": float(formal_rel),
                    "formal_relation_kind": formal_kind,
                    "formal_shared": formal_shared,
                    "diagnostics": formal_diag,
                    "pair_type": pair_type,
                    "atom_id": cand["atom_id"],
                    "spec_id": cand["spec_id"],
                    "kind": cand["kind"],
                    "text": cand["text"],
                    "signals": cand.get("signals", []),
                    "source_refs": cand.get("source_refs", []),
                }
            )

        rows.sort(key=lambda item: item["score"], reverse=True)
        limit = config.assumption_top_k if query["kind"] == "assumption" else config.uncertain_top_k
        kept = sorted(
            rows,
            key=lambda item: (
                item["score"],
                item.get("formal_relation_score", 0.0),
                item["dense_score"],
                item["signal_relation_score"],
            ),
            reverse=True,
        )[:limit]
        structural_facts = []
        if graph is not None:
            structural_facts = graph.get_structural_facts(
                query.get("signals", []),
                limit=8,
            )
        for rank, row in enumerate(kept, start=1):
            row["rank"] = rank
            selected_pairs.append({"query_atom_id": query["atom_id"], **row})

        results.append(
            {
                "query": _public_atom(query),
                "matches": kept,
                "num_candidates_after_filter": len(rows),
                "structural_facts": structural_facts,
                "num_structural_facts": len(structural_facts),
            }
        )

    by_query_kind: dict[str, int] = {}
    by_pair_type: dict[str, int] = {}
    num_query_with_structure = 0
    num_structure_facts = 0
    for item in results:
        by_query_kind[item["query"]["kind"]] = (
            by_query_kind.get(item["query"]["kind"], 0) + len(item["matches"])
        )
        for match in item["matches"]:
            by_pair_type[match["pair_type"]] = by_pair_type.get(match["pair_type"], 0) + 1
        if item.get("structural_facts"):
            num_query_with_structure += 1
            num_structure_facts += len(item["structural_facts"])

    return {
        "metadata": {
            "method": "optimized_dense_signal_relation",
            "model_name": config.model_name,
            "assumption_top_k": config.assumption_top_k,
            "uncertain_top_k": config.uncertain_top_k,
            "assumption_min_score": config.assumption_min_score,
            "uncertain_min_score_with_signal": config.uncertain_min_score_with_signal,
            "uncertain_dense_fallback": config.uncertain_dense_fallback,
            "dense_weight": dense_weight,
            "signal_weight": signal_weight,
            "formal_weight": formal_weight,
            "exclude_same_spec": config.exclude_same_spec,
            "num_atoms": len(atoms),
            "num_queries": len(queries),
            "num_guarantees": len(guarantees),
            "num_selected_pairs": len(selected_pairs),
            "selected_pairs_by_query_kind": by_query_kind,
            "selected_pairs_by_pair_type": by_pair_type,
            "num_nonempty_queries": sum(1 for r in results if r["matches"]),
            "num_unmatched_uncertain": sum(
                1 for r in results if r["query"]["kind"] == "uncertain" and not r["matches"]
            ),
            "num_queries_with_structural_facts": num_query_with_structure,
            "num_structural_facts_attached": num_structure_facts,
        },
        "results": results,
    }


def embed_atoms_cached(
    atoms: list[dict[str, Any]],
    cache_dir: str | Path,
    config: SemanticAgConfig,
) -> np.ndarray:
    """Embed atoms with BGE-M3, reusing a cache keyed by atom text hash."""
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    digest = _atoms_digest(atoms, config.model_name)
    emb_path = cache / f"embeddings_{digest}.npz"
    meta_path = cache / f"atoms_{digest}.jsonl"
    if emb_path.exists():
        return np.load(emb_path, allow_pickle=False)["embeddings"]

    texts = [atom["embedding_text"] for atom in atoms]
    embeddings = _l2_normalize(
        _encode(
            config.model_name,
            texts,
            config.batch_size,
            config.use_fp16,
            config.hf_home,
            config.offline,
        )
    )
    np.savez_compressed(
        emb_path,
        embeddings=embeddings,
        atom_ids=np.array([atom["atom_id"] for atom in atoms]),
        model=np.array([config.model_name]),
    )
    with meta_path.open("w", encoding="utf-8") as f:
        for atom in atoms:
            f.write(json.dumps(atom, ensure_ascii=False, sort_keys=True) + "\n")
    return embeddings


def build_pairing(
    graph: SignalGraph,
    cache_dir: str | Path,
    config: SemanticAgConfig | None = None,
    embeddings_path: str | Path | None = None,
) -> dict[str, Any]:
    cfg = config or SemanticAgConfig()
    atoms = build_atoms(graph)
    if not atoms:
        return {"metadata": {"num_atoms": 0}, "results": []}
    if embeddings_path:
        with np.load(embeddings_path, allow_pickle=False) as data:
            embeddings = data["embeddings"]
            if "atom_ids" in data:
                expected = [atom["atom_id"] for atom in atoms]
                actual = [str(x) for x in data["atom_ids"].tolist()]
                if actual != expected:
                    raise ValueError("Embeddings atom_ids do not match current specs")
    else:
        embeddings = embed_atoms_cached(atoms, cache_dir, cfg)
    return pair_atoms(atoms, embeddings, cfg, graph=graph)


def query_units(pairing: dict[str, Any]) -> list[dict[str, Any]]:
    """Return semantic query units with at least one candidate guarantee."""
    units = []
    for item in pairing.get("results", []):
        if not item.get("matches"):
            continue
        units.append(
            {
                "unit_id": item["query"]["atom_id"],
                "query": item["query"],
                "matches": item["matches"],
                "est_tokens": estimate_tokens(render_query_unit(item["query"], item["matches"])),
            }
        )
    return units


def make_batches(
    units: list[dict[str, Any]],
    config: SemanticBatchConfig | None = None,
) -> list[dict[str, Any]]:
    """Pack query units into guarded LLM batches.

    ``single`` preserves the historical one-query-per-call behavior.
    ``guarded`` greedily packs units that share normalized signal roots while
    respecting quality guardrails.
    """
    cfg = config or SemanticBatchConfig()
    if cfg.mode == "single":
        return [_batch_dict(i, [unit]) for i, unit in enumerate(units)]
    if cfg.mode != "guarded":
        raise ValueError(f"unsupported semantic batch mode: {cfg.mode}")

    batches: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    ordered = sorted(
        units,
        key=lambda u: (
            _primary_root(u),
            u["query"].get("kind", ""),
            _is_dense_fallback(u),
            u["query"].get("atom_id", ""),
        ),
    )
    for unit in ordered:
        candidate = cur + [unit]
        if cur and not _batch_allowed(candidate, cfg):
            batches.append(cur)
            cur = [unit]
        else:
            cur = candidate
    if cur:
        batches.append(cur)
    return [_batch_dict(i, batch) for i, batch in enumerate(batches)]


def summarise_batches(batches: list[dict[str, Any]]) -> dict[str, Any]:
    if not batches:
        return {
            "calls": 0,
            "query_count": 0,
            "pair_count": 0,
            "total_est_prompt_tokens": 0,
            "avg_queries_per_call": 0,
        }
    query_count = sum(len(b["units"]) for b in batches)
    pair_count = sum(sum(len(u["matches"]) for u in b["units"]) for b in batches)
    total_tokens = sum(b["est_prompt_tokens"] for b in batches)
    return {
        "calls": len(batches),
        "query_count": query_count,
        "pair_count": pair_count,
        "total_est_prompt_tokens": total_tokens,
        "max_est_prompt_tokens": max(b["est_prompt_tokens"] for b in batches),
        "avg_queries_per_call": round(query_count / len(batches), 2),
        "avg_pairs_per_call": round(pair_count / len(batches), 2),
        "dense_fallback_batches": sum(
            1 for b in batches if b["dense_fallback_uncertain_count"] > 0
        ),
    }


def unmatched_query_candidates(
    pairing: dict[str, Any],
    kinds: tuple[str, ...] = ("uncertain",),
) -> list[dict[str, Any]]:
    """Return unmatched queries of the given *kinds* as Channel F candidates.

    In the A-G model, assumptions and uncertain points are *queries* while
    guarantees are only *candidates*; so an "unpaired assumption" is a query
    with zero matches, but "unpaired guarantee" is not expressible here. Pass
    ``kinds=("uncertain", "assumption")`` to also surface high-value unpaired
    assumptions for SVA synthesis.

    Each candidate carries ``formal_sketch`` so Channel F's gate can read
    ``formalizability`` directly.
    """
    out = []
    for item in pairing.get("results", []):
        query = item.get("query", {})
        kind = query.get("kind", "")
        if kind not in kinds or item.get("matches"):
            continue
        out.append(
            {
                "chunk_id": query.get("spec_id", ""),
                "source_file": query.get("source_file", ""),
                "line_start": query.get("line_start", 0),
                "line_end": query.get("line_end", 0),
                "uncertain_text": query.get("text", "")[:400],
                "text": query.get("text", "")[:400],
                "kind": kind,
                "signals": query.get("signals", []),
                "atom_id": query.get("atom_id", ""),
                "formal_sketch": query.get("formal_sketch", {}),
                "summary": "",
                "source": f"semantic_unmatched_{kind}",
            }
        )
    return out


def unmatched_uncertain_candidates(pairing: dict[str, Any]) -> list[dict[str, Any]]:
    """Return unmatched uncertain points as Phase-3/fusion candidates.

    Thin back-compat wrapper over :func:`unmatched_query_candidates`.
    """
    return unmatched_query_candidates(pairing, kinds=("uncertain",))


def render_query_unit(query: dict[str, Any], matches: list[dict[str, Any]]) -> str:
    lines = [
        f"QUERY {query['atom_id']} kind={query['kind']}",
        f"query_signals={query.get('signals', [])}",
        f"query_text={query.get('text', '')}",
        "CANDIDATE_GUARANTEES:",
    ]
    for match in matches:
        lines.extend(
            [
                (
                    f"- rank={match['rank']} score={match['score']:.4f} "
                    f"dense={match['dense_score']:.4f} "
                    f"signal_relation={match['signal_relation_score']:.4f} "
                    f"relation_kind={match['signal_relation_kind']} "
                    f"shared={match.get('shared_signals', [])}"
                ),
                f"  guarantee_id={match['atom_id']}",
                f"  guarantee_signals={match.get('signals', [])}",
                f"  guarantee_text={match.get('text', '')}",
            ]
        )
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    cjk = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text or ""))
    non_cjk = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", " ", text or "")
    ascii_tokens = 0
    for item in re.findall(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]", non_cjk):
        if re.match(r"^[A-Za-z0-9_]+$", item):
            ascii_tokens += max(1, math.ceil(len(item) / 4))
        else:
            ascii_tokens += 1
    return cjk + ascii_tokens


def summarise_pairing(
    pairing: dict[str, Any],
    batch_config: SemanticBatchConfig | None = None,
) -> dict[str, Any]:
    meta = dict(pairing.get("metadata", {}))
    units = query_units(pairing)
    meta["num_query_units"] = len(units)
    meta["est_query_unit_tokens"] = sum(u["est_tokens"] for u in units)
    meta["single_batch_calls"] = len(make_batches(units, SemanticBatchConfig(mode="single")))
    guarded = make_batches(units, SemanticBatchConfig(mode="guarded"))
    guarded_summary = summarise_batches(guarded)
    meta["guarded_batch_calls"] = guarded_summary["calls"]
    meta["guarded_batch_avg_queries"] = guarded_summary["avg_queries_per_call"]
    meta["guarded_batch_est_prompt_tokens"] = guarded_summary["total_est_prompt_tokens"]
    if batch_config is not None:
        selected = make_batches(units, batch_config)
        meta["selected_batch_mode"] = batch_config.mode
        meta["selected_batch_summary"] = summarise_batches(selected)
    return meta


def _make_atom(
    spec: dict[str, Any],
    kind: str,
    index: int,
    text: str,
    signals: list[str],
    source_refs: Any,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    chunk_id = spec.get("chunk_id", "")
    atom = {
        "atom_id": f"{chunk_id}::{kind}::{index}",
        "kind": kind,
        "spec_id": chunk_id,
        "source_file": spec.get("source_file", ""),
        "line_start": spec.get("line_start"),
        "line_end": spec.get("line_end"),
        "text": text,
        "signals": signals,
        "source_refs": source_refs if source_refs is not None else [],
        "formal_sketch": (extra or {}).get("formal_sketch", {}),
    }
    atom["embedding_text"] = "\n".join(
        part
        for part in [
            f"kind: {kind}",
            f"text: {text}",
            f"signals: {_signal_tokens(signals)}",
            f"source_refs: {_refs_text(source_refs)}",
            f"chunk_id: {chunk_id}",
            f"summary: {spec.get('summary', '')}",
            f"security_implications: {spec.get('security_implications', '')}",
            f"source_file: {spec.get('source_file', '')}",
            f"line_range: {spec.get('line_start', '')}-{spec.get('line_end', '')}",
        ]
        if part.strip()
    )
    if extra:
        atom.update(extra)
    return atom


def _public_atom(atom: dict[str, Any]) -> dict[str, Any]:
    return {
        "atom_id": atom["atom_id"],
        "spec_id": atom["spec_id"],
        "kind": atom["kind"],
        "text": atom["text"],
        "signals": atom.get("signals", []),
        "source_refs": atom.get("source_refs", []),
        "source_file": atom.get("source_file", ""),
        "line_start": atom.get("line_start"),
        "line_end": atom.get("line_end"),
        "formal_sketch": atom.get("formal_sketch", {}),
    }


def _signal_tokens(signals: list[str]) -> str:
    toks: list[str] = []
    for sig in signals:
        toks.append(sig)
        toks.extend(part for part in re.split(r"[_\W]+", sig) if part)
    return " ".join(dict.fromkeys(toks))


def _refs_text(refs: Any) -> str:
    if isinstance(refs, list):
        return " ".join(str(r) for r in refs)
    if refs:
        return str(refs)
    return ""


def _atoms_digest(atoms: list[dict[str, Any]], model_name: str) -> str:
    h = hashlib.sha256()
    h.update(model_name.encode())
    for atom in atoms:
        h.update(atom["atom_id"].encode())
        h.update(atom["embedding_text"].encode())
    return h.hexdigest()[:16]


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    denom = np.linalg.norm(mat, axis=1, keepdims=True)
    denom[denom == 0.0] = 1.0
    return mat / denom


def _encode(
    model_name: str,
    texts: list[str],
    batch_size: int,
    fp16: bool,
    hf_home: str | None,
    offline: bool,
) -> np.ndarray:
    try:
        from FlagEmbedding import BGEM3FlagModel
    except ImportError as exc:
        raise RuntimeError(
            "FlagEmbedding is not installed; install semantic AG dependencies first."
        ) from exc
    if hf_home:
        os.environ.setdefault("HF_HOME", hf_home)
    if offline:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    model = BGEM3FlagModel(model_name, use_fp16=fp16)
    result = model.encode(
        texts,
        batch_size=batch_size,
        max_length=8192,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    return np.asarray(result["dense_vecs"], dtype=np.float32)


def _text_signals(atom: dict[str, Any]) -> set[str]:
    text = f"{atom.get('text', '')}\n{atom.get('embedding_text', '')}"
    return {x.strip() for x in re.findall(r"`([^`]+)`", text) if x.strip()}


def _expanded_signals(atom: dict[str, Any]) -> set[str]:
    return set(atom.get("signals", []) or []) | _text_signals(atom)


def _normalize_signal(signal: str) -> str:
    sig = signal.strip()
    sig = re.sub(r"\[[^\]]+\]", "", sig)
    sig = re.sub(r"\.[A-Za-z0-9_]+$", "", sig)
    changed = True
    while changed:
        changed = False
        for prefix in ("mr_",):
            if sig.startswith(prefix):
                sig = sig[len(prefix):]
                changed = True
        for suffix in ("_ctrl", "_raw", "_sel", "_o", "_i", "_q", "_d"):
            if sig.endswith(suffix) and len(sig) > len(suffix):
                sig = sig[: -len(suffix)]
                changed = True
    return sig


def _normalized_map(signals: set[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for sig in signals:
        norm = _normalize_signal(sig)
        if norm:
            out.setdefault(norm, []).append(sig)
    return out


def _field_signal_overlap(query: dict[str, Any], cand: dict[str, Any]) -> float:
    q = set(query.get("signals", []) or [])
    c = set(cand.get("signals", []) or [])
    if not q or not c:
        return 0.0
    return len(q & c) / len(q | c)


def _signal_relation(query: dict[str, Any], cand: dict[str, Any]) -> tuple[float, str, list[str]]:
    q_field = set(query.get("signals", []) or [])
    c_field = set(cand.get("signals", []) or [])
    shared_field = sorted(q_field & c_field)
    if shared_field:
        return _field_signal_overlap(query, cand), "field_overlap", shared_field

    q_norm = _normalized_map(q_field)
    c_norm = _normalized_map(c_field)
    shared_norm_keys = sorted(set(q_norm) & set(c_norm))
    if shared_norm_keys:
        shared_norm = sorted(
            {
                sig
                for key in shared_norm_keys
                for sig in q_norm.get(key, []) + c_norm.get(key, [])
            }
        )
        return 0.6, "normalized_field_overlap", shared_norm

    shared_text = sorted(_expanded_signals(query) & _expanded_signals(cand))
    if shared_text:
        return 0.2, "text_signal_overlap", shared_text

    q_text_norm = _normalized_map(_expanded_signals(query))
    c_text_norm = _normalized_map(_expanded_signals(cand))
    shared_text_norm_keys = sorted(set(q_text_norm) & set(c_text_norm))
    if shared_text_norm_keys:
        shared_text_norm = sorted(
            {
                sig
                for key in shared_text_norm_keys
                for sig in q_text_norm.get(key, []) + c_text_norm.get(key, [])
            }
        )
        return 0.2, "normalized_text_signal_overlap", shared_text_norm

    return 0.0, "none", []


def _formal_relation(
    query: dict[str, Any],
    cand: dict[str, Any],
) -> tuple[float, str, list[str], dict[str, Any]]:
    """Compute a formal-similarity score between two sketches.

    Returns ``(score, kind, shared, diagnostics)``. The score reflects
    *similarity* only (scope/clock/shape/signal overlap) and feeds ranking.
    Consequent **conflict** is NOT scored here — it is a contradiction signal,
    not a similarity signal — and is instead reported in ``diagnostics`` so it
    can be surfaced to humans / trace without distorting semantic ranking
    (Formal CSBC v2.0 §3.2: pairing stays purely semantic).
    """
    q = query.get("formal_sketch", {}) or {}
    c = cand.get("formal_sketch", {}) or {}
    if not q and not c:
        return 0.0, "none", [], {}

    score = 0.0
    shared: list[str] = []

    q_scope = str(q.get("scope", "") or "").strip()
    c_scope = str(c.get("scope", "") or "").strip()
    if q_scope and c_scope and q_scope == c_scope:
        score += 0.18
        shared.append(f"scope:{q_scope}")

    q_clock = str(q.get("clock", "") or "").strip()
    c_clock = str(c.get("clock", "") or "").strip()
    if q_clock and c_clock and q_clock == c_clock:
        score += 0.16
        shared.append(f"clock:{q_clock}")

    q_reset = str(q.get("reset", "") or "").strip()
    c_reset = str(c.get("reset", "") or "").strip()
    if q_reset and c_reset and q_reset == c_reset:
        score += 0.08
        shared.append(f"reset:{q_reset}")

    q_shape = str(q.get("temporal_shape", "") or "").strip()
    c_shape = str(c.get("temporal_shape", "") or "").strip()
    if q_shape and c_shape and q_shape == c_shape:
        score += 0.14
        shared.append(f"shape:{q_shape}")

    q_form = str(q.get("formalizability", "") or "").strip()
    c_form = str(c.get("formalizability", "") or "").strip()
    if q_form and c_form and q_form == c_form:
        score += 0.10
        shared.append(f"formal:{q_form}")

    q_sig = set(str(s) for s in q.get("signals", []) or [])
    c_sig = set(str(s) for s in c.get("signals", []) or [])
    if q_sig and c_sig:
        inter = sorted(q_sig & c_sig)
        if inter:
            score += min(0.20, 0.05 * len(inter))
            shared.extend(inter[:6])

    q_ant = str(q.get("antecedent", "") or "")
    c_cons = str(c.get("consequent", "") or "")
    if q_ant and c_cons:
        overlap = _text_overlap_score(q_ant, c_cons)
        if overlap:
            score += min(0.10, overlap * 0.10)
            shared.append("antecedent/consequent")

    # Consequent conflict detection — the core CSBC signal. Detected here but
    # NOT added to the score (v2.0 §3.2). Reported as diagnostics so Phase 3 /
    # trace / humans can see it, while pairing order stays purely semantic.
    diagnostics: dict[str, Any] = {}
    conflict_sigs = _consequent_conflict(q, c)
    if conflict_sigs:
        diagnostics["conflict_signals"] = conflict_sigs

    kind = "aligned" if score >= 0.5 else "weak"
    return min(score, 1.0), kind, shared, diagnostics


# Match "signal == value", "signal != value", "!signal", "signal" patterns
_EQ_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_.$\[\]]*)\s*(==|!=)\s*([A-Za-z0-9_'\.]+)")
_NEG_RE = re.compile(r"(^|[^A-Za-z0-9_])!\s*([A-Za-z_][A-Za-z0-9_.$\[\]]*)")


def _parse_signal_constraints(expr: str) -> dict[str, set[str]]:
    """Parse an expression into {signal: {asserted value strings}}.

    Captures ``sig == V`` (value V), ``sig != V`` (not-V marker), ``!sig``
    (value '0), and bare ``sig`` (value '1) so two consequents can be checked
    for direct value conflicts on the same signal.
    """
    out: dict[str, set[str]] = {}
    if not expr:
        return out
    for sig, op, val in _EQ_RE.findall(expr):
        key = sig.strip().lower()
        tag = f"={val.strip().lower()}" if op == "==" else f"!={val.strip().lower()}"
        out.setdefault(key, set()).add(tag)
    for _, sig in _NEG_RE.findall(expr):
        out.setdefault(sig.strip().lower(), set()).add("=1'b0")
    return out


def _consequent_conflict(q: dict[str, Any], c: dict[str, Any]) -> list[str]:
    """Return signals whose asserted values conflict between two consequents.

    Only flags a conflict when the two items share antecedent context (same
    temporal shape and overlapping antecedent tokens), so unrelated guarantees
    on the same signal are not falsely marked.
    """
    q_cons = _parse_signal_constraints(str(q.get("consequent", "") or ""))
    c_cons = _parse_signal_constraints(str(c.get("consequent", "") or ""))
    if not q_cons or not c_cons:
        return []

    # Require some shared antecedent context to avoid spurious conflicts.
    q_ant = str(q.get("antecedent", "") or "")
    c_ant = str(c.get("antecedent", "") or "")
    if q_ant and c_ant and _text_overlap_score(q_ant, c_ant) < 0.1:
        return []

    conflicts: list[str] = []
    for sig in set(q_cons) & set(c_cons):
        qv, cv = q_cons[sig], c_cons[sig]
        # Direct value conflict: one asserts ==X, other asserts ==Y (X!=Y),
        # or one asserts ==X while the other asserts !=X.
        q_eq = {v[1:] for v in qv if v.startswith("=")}
        c_eq = {v[1:] for v in cv if v.startswith("=")}
        q_ne = {v[2:] for v in qv if v.startswith("!=")}
        c_ne = {v[2:] for v in cv if v.startswith("!=")}
        if q_eq and c_eq and not (q_eq & c_eq):
            conflicts.append(sig)
        elif (q_eq & c_ne) or (c_eq & q_ne):
            conflicts.append(sig)
    return sorted(conflicts)


def _text_overlap_score(a: str, b: str) -> float:
    ta = set(re.findall(r"[A-Za-z_][A-Za-z0-9_.$\[\]:]*", a.lower()))
    tb = set(re.findall(r"[A-Za-z_][A-Za-z0-9_.$\[\]:]*", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _pair_type(
    query: dict[str, Any],
    signal_relation: float,
    dense: float,
    config: SemanticAgConfig,
) -> str | None:
    if query["kind"] == "assumption":
        if signal_relation <= 0.0:
            return None
        return "normal"
    if query["kind"] == "uncertain":
        if signal_relation > 0.0:
            return "uncertain_with_signal"
        if dense >= config.uncertain_dense_fallback:
            return "uncertain_dense_fallback"
    return None


def _is_dense_fallback(unit: dict[str, Any]) -> bool:
    return any(
        match.get("pair_type") == "uncertain_dense_fallback"
        for match in unit.get("matches", [])
    )


def _unit_signal_roots(unit: dict[str, Any]) -> set[str]:
    roots = set()
    for sig in unit["query"].get("signals", []) or []:
        roots.add(_normalize_signal(sig).lstrip("!~"))
    for match in unit.get("matches", []):
        for sig in match.get("shared_signals", []) or []:
            roots.add(_normalize_signal(sig).lstrip("!~"))
    return {root for root in roots if root}


def _primary_root(unit: dict[str, Any]) -> str:
    roots = sorted(_unit_signal_roots(unit))
    if roots:
        return roots[0]
    return unit["query"].get("spec_id", "")


def _batch_allowed(units: list[dict[str, Any]], config: SemanticBatchConfig) -> bool:
    if len(units) > config.max_queries:
        return False
    if _batch_est_prompt_tokens(units) > config.max_prompt_tokens:
        return False
    if sum(1 for unit in units if _is_dense_fallback(unit)) > config.max_dense_fallback_uncertain:
        return False
    all_roots = set().union(*(_unit_signal_roots(unit) for unit in units))
    if len(all_roots) > config.max_signal_roots:
        return False
    if config.min_shared_roots <= 0 or len(units) <= 1:
        return True
    newest_roots = _unit_signal_roots(units[-1])
    previous_roots = set().union(*(_unit_signal_roots(unit) for unit in units[:-1]))
    if not newest_roots:
        return not _is_dense_fallback(units[-1])
    return len(newest_roots & previous_roots) >= config.min_shared_roots


def _batch_est_prompt_tokens(units: list[dict[str, Any]]) -> int:
    overhead = 700 + 80 * len(units)
    return overhead + sum(unit.get("est_tokens", 0) for unit in units)


def _batch_dict(batch_id: int, units: list[dict[str, Any]]) -> dict[str, Any]:
    roots = sorted(set().union(*(_unit_signal_roots(unit) for unit in units))) if units else []
    return {
        "batch_id": batch_id,
        "units": units,
        "query_atom_ids": [unit["query"]["atom_id"] for unit in units],
        "query_count": len(units),
        "pair_count": sum(len(unit.get("matches", [])) for unit in units),
        "signal_roots": roots,
        "dense_fallback_uncertain_count": sum(1 for unit in units if _is_dense_fallback(unit)),
        "est_prompt_tokens": _batch_est_prompt_tokens(units),
    }


def _normalised_weights(config: SemanticAgConfig) -> tuple[float, float, float]:
    total = config.dense_weight + config.signal_weight + config.formal_weight
    if total <= 0:
        raise ValueError("dense_weight + signal_weight + formal_weight must be positive")
    return (
        config.dense_weight / total,
        config.signal_weight / total,
        config.formal_weight / total,
    )
