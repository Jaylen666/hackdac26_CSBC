#!/usr/bin/env python3
"""
Full Phase 2 end-to-end pipeline with automatic checkpointing.
Usage:
    python3 scripts/run_phase2_e2e.py --ip hmac
    python3 scripts/run_phase2_e2e.py --ip kmac

Each channel's results are saved to disk immediately after completion.
On re-run, already-completed channels are skipped (resume behaviour).
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rtl_bug_agent.phase2.signal_graph import build_signal_graph
from rtl_bug_agent.phase2.channel_b import run_channel_b, run_channel_b_semantic
from rtl_bug_agent.phase2.channel_c import run_channel_c
from rtl_bug_agent.phase2.channel_d import run_channel_d
from rtl_bug_agent.phase2.semantic_ag import (
    SemanticBatchConfig,
    SemanticAgConfig,
    build_pairing as build_semantic_pairing,
    summarise_pairing,
    unmatched_uncertain_candidates,
)
from rtl_bug_agent.phase2.layer2 import extract_claims_for_ip, run_layer2
from rtl_bug_agent.phase2.fusion import fuse, print_summary
from rtl_bug_agent.phase2.phase3 import verify_top_findings
from rtl_bug_agent.env import load_dotenv, make_client


def _ckpt_path(ip: str, channel: str) -> Path:
    return Path(f"output/.checkpoint_{ip}_{channel}.json")


def _load_ckpt(ip: str, channel: str):
    p = _ckpt_path(ip, channel)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def _save_ckpt(ip: str, channel: str, data):
    _ckpt_path(ip, channel).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2 end-to-end")
    parser.add_argument("--ip", default="hmac", help="IP name")
    parser.add_argument("--specs-dir", default=None)
    parser.add_argument("--phase3-top-n", type=int, default=0)
    parser.add_argument("--workers", type=int, default=8,
                        help="Parallel LLM calls for Channel B/C (default: 8)")
    parser.add_argument("--ag-pairing-mode", choices=["legacy", "semantic", "shadow"],
                        default="legacy",
                        help="A-G pairing mode for Channel B (default: legacy)")
    parser.add_argument("--semantic-cache-dir", default="output/.semantic_ag_cache",
                        help="Cache directory for semantic AG embeddings")
    parser.add_argument("--semantic-model", default="BAAI/bge-m3",
                        help="Embedding model for semantic AG mode")
    parser.add_argument("--semantic-hf-home",
                        default="/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/out/hf_cache",
                        help="Local HuggingFace cache for semantic AG; used offline by default")
    parser.add_argument("--semantic-online-download-model", action="store_true",
                        help="Allow HuggingFace network access to download missing semantic model files")
    parser.add_argument("--semantic-batch-size", type=int, default=16)
    parser.add_argument("--semantic-embeddings", default=None,
                        help="Optional precomputed semantic AG embeddings .npz")
    parser.add_argument("--semantic-batch-mode", choices=["single", "guarded"],
                        default="single",
                        help="Semantic Channel B LLM batching mode (default: single)")
    parser.add_argument("--semantic-max-queries-per-batch", type=int, default=5)
    parser.add_argument("--semantic-max-prompt-tokens", type=int, default=5500)
    parser.add_argument("--semantic-max-dense-fallback-uncertain", type=int, default=1)
    parser.add_argument("--semantic-min-shared-roots", type=int, default=1)
    parser.add_argument("--semantic-max-signal-roots", type=int, default=4)
    parser.add_argument("--force", action="store_true",
                        help="Ignore checkpoints, re-run all channels")
    args = parser.parse_args()
    ip = args.ip
    specs_dir = args.specs_dir or f"output/specs_{ip}"
    out_path = Path(f"output/findings_{ip}.json")
    semantic_batch_cfg = SemanticBatchConfig(
        mode=args.semantic_batch_mode,
        max_queries=args.semantic_max_queries_per_batch,
        max_prompt_tokens=args.semantic_max_prompt_tokens,
        max_dense_fallback_uncertain=args.semantic_max_dense_fallback_uncertain,
        min_shared_roots=args.semantic_min_shared_roots,
        max_signal_roots=args.semantic_max_signal_roots,
    )

    t_start = time.monotonic()

    # ── Bootstrap ──────────────────────────────────────────────────
    load_dotenv("/home/smy/.env")
    client = make_client("GUOCHUANG_DEEPSEEK", thinking="high")
    client_gpt = make_client("OPENAI", thinking="high")

    # ── Pass 0 ─────────────────────────────────────────────────────
    print("=" * 60)
    print(f"Pass 0: Signal Dependency Graph ({ip})")
    print("=" * 60)
    graph = build_signal_graph(specs_dir)
    print(graph.summary())

    all_pairs = graph.find_ag_pairs(filter_mode="all")
    behav_pairs = graph.find_ag_pairs(filter_mode="behavioral")
    print(
        f"A-G pairs: {len(all_pairs)} total -> {len(behav_pairs)} "
        f"after filter "
        f"({100 * len(behav_pairs) // max(len(all_pairs), 1)}%)"
    )
    security_signals = set(graph.get_security_signals())

    # ── Uncertain Collector / Semantic Pairing Prep ────────────────
    semantic_pairing = None
    semantic_ph3_cands = []
    if args.ag_pairing_mode in ("semantic", "shadow"):
        sem_cfg = SemanticAgConfig(
            model_name=args.semantic_model,
            hf_home=args.semantic_hf_home,
            offline=not args.semantic_online_download_model,
            batch_size=args.semantic_batch_size,
        )
        print("  Semantic AG: building BGE-M3 pairing ...")
        semantic_pairing = build_semantic_pairing(
            graph,
            cache_dir=args.semantic_cache_dir,
            config=sem_cfg,
            embeddings_path=args.semantic_embeddings,
        )
        semantic_summary = summarise_pairing(semantic_pairing, semantic_batch_cfg)
        shadow_path = Path(f"output/semantic_ag_shadow_{ip}.json")
        shadow_path.write_text(
            json.dumps(semantic_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        semantic_ph3_cands = unmatched_uncertain_candidates(semantic_pairing)
        print(f"  Semantic AG summary written to {shadow_path}")
        print(
            "  Semantic AG: "
            f"{semantic_summary.get('num_selected_pairs', 0)} pairs, "
            f"{semantic_summary.get('num_query_units', 0)} query units, "
            f"{len(semantic_ph3_cands)} unmatched uncertain → Phase 3"
        )
        selected_batch = semantic_summary.get("selected_batch_summary", {})
        if selected_batch:
            print(
                "  Semantic AG batching: "
                f"mode={args.semantic_batch_mode}, "
                f"{selected_batch.get('calls', 0)} Channel B calls, "
                f"{selected_batch.get('avg_queries_per_call', 0)} avg queries/call"
            )

    if args.ag_pairing_mode in ("legacy", "shadow"):
        from rtl_bug_agent.phase2.uncertain_collector import collect_and_classify
        ch_b_cands, ph3_cands = collect_and_classify(graph)
        injected = 0
        for cand in ch_b_cands:
            sid = cand["chunk_id"]
            wa = cand.get("weak_assumption")
            if sid in graph.specs and wa:
                graph.specs[sid].setdefault("assumptions", []).append(wa)
                injected += 1
        print(f"  Uncertain collector: {injected} → Channel B, {len(ph3_cands)} → Phase 3")
    elif args.ag_pairing_mode == "semantic":
        ch_b_cands = []
        ph3_cands = semantic_ph3_cands
        print(
            "  Uncertain collector: skipped weak-assumption injection "
            "because semantic AG handles uncertain points directly"
        )
    else:
        print(
            "  Shadow mode: semantic pairing was summarized, while legacy "
            "uncertain injection and Channel B remain active."
        )

    # ── Channel B ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Layer 1 — Channel B: Assumption-Guarantee Pairing")
    print("=" * 60)
    _ckpt_path(ip, "B").parent.mkdir(parents=True, exist_ok=True)
    if args.ag_pairing_mode == "semantic":
        findings_b = run_channel_b_semantic(
            semantic_pairing or {"results": []},
            graph,
            client,
            workers=args.workers,
            checkpoint_path=str(_ckpt_path(ip, "B_semantic")),
            batch_config=semantic_batch_cfg,
        )
    else:
        findings_b = run_channel_b(
            graph, client, workers=args.workers,
            checkpoint_path=str(_ckpt_path(ip, "B"))
        )

    # ── Channel C ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Layer 1 — Channel C: Coverage Gap Detection")
    print("=" * 60)
    findings_c = run_channel_c(
        graph, client, workers=args.workers,
        checkpoint_path=str(_ckpt_path(ip, "C"))
    )

    # ── Channel D ──────────────────────────────────────────────────
    findings_d = None if args.force else _load_ckpt(ip, "D")
    if findings_d is not None:
        print(f"Channel D: loaded {len(findings_d)} findings from checkpoint")
    else:
        print("\n" + "=" * 60)
        print("Layer 1 — Channel D: Temporal Consistency")
        print("=" * 60)
        findings_d = run_channel_d(graph, client)
        _save_ckpt(ip, "D", findings_d)
        print(f"  Checkpoint saved ({len(findings_d)} findings)")

    # ── Layer 2 ────────────────────────────────────────────────────
    findings_g = None if args.force else _load_ckpt(ip, "G")
    claims = None
    if findings_g is not None:
        print(f"Layer 2: loaded {len(findings_g)} findings from checkpoint")
    else:
        print("\n" + "=" * 60)
        print("Layer 2 — Official Spec Alignment")
        print("=" * 60)
        claims = extract_claims_for_ip(ip, client)
        findings_g = run_layer2(claims, graph, client)
        _save_ckpt(ip, "G", findings_g)
        print(f"  Checkpoint saved ({len(findings_g)} findings)")

    # ── Pass 3: Fusion ─────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Pass 3: Fusion + Ranking")
    print("=" * 60)

    # Convert Phase 3 uncertain candidates to finding dicts
    up_findings = []
    for cand in ph3_cands:
        up_findings.append({
            "title": f"[U-UP] {cand['uncertain_text'][:150]}",
            "severity": "LOW",
            "verdict": "UNCERTAIN",
            "channels": ["U-UP"],
            "involved_signals": cand.get("signals", []),
            "involved_specs": [cand.get("chunk_id", "")],
            "contradiction": cand["uncertain_text"][:400],
            "score": 0.3,
            "evidence": [],
            "cross_channel_hits": 1,
            "is_self_ref": True,
        })

    all_findings = {
        "B-AG": findings_b,
        "C-COV": findings_c,
        "D-TMP": findings_d,
        "L2-G": findings_g,
        "U-UP": up_findings,
    }

    merged = fuse(all_findings, security_signals)
    print_summary(merged)

    # ── Per-channel stats ───────────────────────────────────────
    elapsed_total = time.monotonic() - t_start
    s = client.stats()
    print("\n" + "=" * 60)
    print("Per-Channel Statistics")
    print("=" * 60)
    for ch_name, findings in [
        ("Channel B", findings_b), ("Channel C", findings_c),
        ("Channel D", findings_d), ("Layer 2", findings_g),
        ("Uncertain (ChB)", ch_b_cands), ("Uncertain (Ph3)", ph3_cands),
    ]:
        n = len(findings)
        print(f"  {ch_name:20s}: {n:4d} items")
    print(f"  {'Total findings':20s}: {len(merged):4d} merged")
    print(f"  {'Total LLM calls':20s}: {s['call_count']}")
    print(f"  {'Total tokens':20s}: {s['total_tokens']:,}")
    print(f"  {'Wall time':20s}: {elapsed_total:.0f}s ({elapsed_total/60:.1f}min)")

    # ── Phase 3 ─────────────────────────────────────────────────────
    if args.phase3_top_n > 0:
        print("\n" + "=" * 60)
        print(f"Phase 3: Source-Level Verification (top {args.phase3_top_n})")
        print("=" * 60)
        merged_dicts = [f.to_dict() for f in merged]
        verified = verify_top_findings(
            merged_dicts, graph, client_gpt,
            top_n=args.phase3_top_n,
            official_claims=claims,
        )
        merged = verified

    # ── Write output + cleanup checkpoints ─────────────────────────
    elapsed = time.monotonic() - t_start
    stats = client.stats()
    stats["total_wall_seconds"] = round(elapsed, 1)
    stats["total_wall_minutes"] = round(elapsed / 60, 1)

    output = {
        "_stats": stats,
        "_ag_pairing_mode": args.ag_pairing_mode,
        "_semantic_batch_mode": args.semantic_batch_mode,
        "findings": [f.to_dict() if hasattr(f, "to_dict") else f for f in merged],
    }
    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote {out_path}")

    # Clean up checkpoints on success
    for ch in ["B", "B_semantic", "C", "D", "G"]:
        p = _ckpt_path(ip, ch)
        if p.exists():
            p.unlink()

    print("\n" + "=" * 60)
    print("Run Statistics")
    print("=" * 60)
    client.print_stats()
    print(f"  Total wall time: {elapsed:.0f}s ({elapsed / 60:.1f} min)")


if __name__ == "__main__":
    main()
