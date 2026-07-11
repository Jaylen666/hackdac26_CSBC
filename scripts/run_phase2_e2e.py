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
from rtl_bug_agent.phase2.channel_f import run_channel_f
from rtl_bug_agent.phase2.formal_runner import run_formal_solver
from rtl_bug_agent.phase2.semantic_ag import (
    SemanticBatchConfig,
    SemanticAgConfig,
    build_pairing as build_semantic_pairing,
    summarise_pairing,
    unmatched_uncertain_candidates,
    unmatched_query_candidates,
)
from rtl_bug_agent.phase2.layer2 import extract_claims_for_ip, run_layer2
from rtl_bug_agent.phase2.fusion import fuse, print_summary
from rtl_bug_agent.phase2.formal_check import run_formal_checks
from rtl_bug_agent.phase2.phase3 import verify_top_findings
from rtl_bug_agent.phase2.trace import TraceSink
from rtl_bug_agent.env import load_dotenv, make_client


def _ckpt_path(out_root: Path, ip: str, channel: str) -> Path:
    return out_root / f".checkpoint_{ip}_{channel}.json"


def _load_ckpt(out_root: Path, ip: str, channel: str):
    p = _ckpt_path(out_root, ip, channel)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def _save_ckpt(out_root: Path, ip: str, channel: str, data):
    _ckpt_path(out_root, ip, channel).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def _as_dict(f):
    """Normalise a finding to a plain dict.

    After ref matching, `merged` holds dicts; before it, `Finding` objects.
    This lets downstream blocks (solver, phase3) accept either form.
    """
    return f.to_dict() if hasattr(f, "to_dict") else f


def _select_channel_f_candidates(
    *,
    ag_pairing_mode: str,
    semantic_pairing: dict | None,
    ph3_cands: list[dict],
) -> list[dict]:
    """Select Channel F candidates without leaking semantic data in shadow mode."""
    if ag_pairing_mode == "semantic" and semantic_pairing is not None:
        return unmatched_query_candidates(
            semantic_pairing, kinds=("uncertain", "assumption")
        )
    return ph3_cands


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2 end-to-end")
    parser.add_argument("--ip", default="hmac", help="IP name")
    parser.add_argument(
        "--out-root",
        default="output",
        help="Root directory for outputs and checkpoints (default: output)",
    )
    parser.add_argument(
        "--specs-dir",
        default=None,
        help="Spec directory; defaults to <out-root>/specs_<ip>",
    )
    parser.add_argument(
        "--structural-facts",
        default=None,
        help="Optional JSONL/JSON structural facts file to attach",
    )
    parser.add_argument(
        "--rtl-root",
        default=".",
        help="Root directory for RTL files (default: current directory)",
    )
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
    parser.add_argument("--channel-b-max-tokens", type=int, default=10000,
                        help="Max output tokens for Channel B LLM calls (default: 10000)")
    parser.add_argument("--formal-check-top-n", type=int, default=0,
                        help="Run bounded formal checks on top-N findings after fusion")
    parser.add_argument("--formal-check-depth", type=int, default=20,
                        help="Bounded model-check depth for formal checks")
    parser.add_argument("--no-ref-augment", action="store_true",
                        help="Disable ref retrieval augmentation (LLM ref extraction + "
                             "bidirectional ref_match) after fusion")
    parser.add_argument("--ref-out-dir", default=None,
                        help="Dir for ref atom extraction outputs "
                             "(default: <out-root>/ref_out)")
    parser.add_argument("--ref-workers", type=int, default=4,
                        help="Parallel workers for LLM ref extraction (default: 4)")
    parser.add_argument("--ref-fresh", action="store_true",
                        help="Ignore ref extraction checkpoints and re-extract")
    parser.add_argument("--trace", action="store_true",
                        help="Write per-finding traceability sidecar trace_<ip>.jsonl (no LLM cost)")
    parser.add_argument("--channel-f", action="store_true",
                        help="Run Channel F: synthesise SVAs for unpaired uncertain points "
                             "(and unpaired assumptions in semantic mode)")
    parser.add_argument("--channel-f-max-tokens", type=int, default=10000,
                        help="Max output tokens for Channel F LLM calls (default: 10000)")
    parser.add_argument("--run-solver", action="store_true",
                        help="Run formal solver (sby+z3) on PENDING SVAs before Phase 3")
    parser.add_argument("--solver-depth", type=int, default=20,
                        help="BMC depth for formal solver (default: 20)")
    parser.add_argument("--solver-timeout", type=int, default=300,
                        help="Timeout per SVA in seconds (default: 300)")
    parser.add_argument("--force", action="store_true",
                        help="Ignore checkpoints, re-run all channels")
    parser.add_argument("--channels", default="B",
                        help="Comma-separated channels to run; current pipeline uses B (default: B)")
    args = parser.parse_args()
    channels_to_run = set(c.strip() for c in args.channels.split(",") if c.strip())
    ip = args.ip
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    specs_dir = args.specs_dir or str(out_root / f"specs_{ip}")
    out_path = out_root / f"findings_{ip}.json"
    trace_sink = TraceSink(out_root / f"trace_{ip}.jsonl") if args.trace else None
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
    client_gpt = make_client("GUOCHUANG_DEEPSEEK", thinking="high")

    # ── Pass 0 ─────────────────────────────────────────────────────
    print("=" * 60)
    print(f"Pass 0: Signal Dependency Graph ({ip})")
    print("=" * 60)
    graph = build_signal_graph(specs_dir, structural_facts_path=args.structural_facts)
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
        shadow_path = out_root / f"semantic_ag_shadow_{ip}.json"
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
    _ckpt_path(out_root, ip, "B").parent.mkdir(parents=True, exist_ok=True)
    if args.ag_pairing_mode == "semantic":
        findings_b = run_channel_b_semantic(
            semantic_pairing or {"results": []},
            graph,
            client,
            workers=args.workers,
            max_tokens=args.channel_b_max_tokens,
            checkpoint_path=str(_ckpt_path(out_root, ip, "B_semantic")),
            batch_config=semantic_batch_cfg,
            trace_sink=trace_sink,
        )
    else:
        findings_b = run_channel_b(
            graph, client, workers=args.workers,
            checkpoint_path=str(_ckpt_path(out_root, ip, "B")),
            trace_sink=trace_sink,
        )

    # ── Channel C ──
    findings_c = []
    if "C" in channels_to_run:
        print("\n" + "=" * 60)
        print("Layer 1 — Channel C: Coverage Gap Detection")
        print("=" * 60)
        findings_c = run_channel_c(
            graph, client, workers=args.workers,
            checkpoint_path=str(_ckpt_path(out_root, ip, "C"))
        )
    else:
        print("\nChannel C: skipped (--channels)")

    # ── Channel D ──
    findings_d = []
    if "D" in channels_to_run:
        findings_d = None if args.force else _load_ckpt(out_root, ip, "D")
        if findings_d is not None:
            print(f"Channel D: loaded {len(findings_d)} findings from checkpoint")
        else:
            print("\n" + "=" * 60)
            print("Layer 1 — Channel D: Temporal Consistency")
            print("=" * 60)
            findings_d = run_channel_d(graph, client)
            _save_ckpt(out_root, ip, "D", findings_d)
            print(f"  Checkpoint saved ({len(findings_d)} findings)")
    else:
        print("\nChannel D: skipped (--channels)")

    # ── Layer 2 ──
    findings_g = []
    claims = None
    if "L2" in channels_to_run:
        findings_g = None if args.force else _load_ckpt(out_root, ip, "G")
        if findings_g is not None:
            print(f"Layer 2: loaded {len(findings_g)} findings from checkpoint")
        else:
            print("\n" + "=" * 60)
            print("Layer 2 — Official Spec Alignment")
            print("=" * 60)
            claims = extract_claims_for_ip(ip, client)
            findings_g = run_layer2(claims, graph, client)
            _save_ckpt(out_root, ip, "G", findings_g)
            print(f"  Checkpoint saved ({len(findings_g)} findings)")
    else:
        print("\nLayer 2: skipped (--channels)")

    # ── Channel F: SVA synthesis for unpaired items ───────────────
    findings_f = []
    if args.channel_f:
        print("\n" + "=" * 60)
        print("Channel F: Property Synthesis for Unpaired Items")
        print("=" * 60)
        # Legacy/shadow: only uncertain points. Live semantic: also unmatched
        # assumptions, which exist only on the semantic query side.
        channel_f_cands = _select_channel_f_candidates(
            ag_pairing_mode=args.ag_pairing_mode,
            semantic_pairing=semantic_pairing,
            ph3_cands=ph3_cands,
        )
        findings_f = run_channel_f(
            channel_f_cands,
            graph,
            client,
            security_signals,
            workers=args.workers,
            max_tokens=args.channel_f_max_tokens,
            checkpoint_path=str(_ckpt_path(out_root, ip, "F")),
            trace_sink=trace_sink,
        )

    # ── Pass 3: Fusion ─────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Pass 3: Fusion + Ranking")
    print("=" * 60)

    # Convert Phase 3 uncertain candidates to finding dicts.
    # When Channel F is on, these same candidates become F-SVA findings (with
    # synthesised SVAs), so skip the U-UP duplication to avoid double-counting.
    up_findings = []
    if not args.channel_f:
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
        "F-SVA": findings_f,
    }

    # cluster=False: keep every channel finding as its own ranked entry.
    # We prefer duplicate bug descriptions (cheap to skim in manual review)
    # over the risk of fuzzy clustering swallowing a real bug behind an
    # unrelated cluster representative (see keymgr N-003 audit).
    merged = fuse(
        all_findings, security_signals, trace_sink=trace_sink, cluster=False
    )
    print_summary(merged)

    # ── Ref matching: LLM ref extraction + bidirectional match ─────
    # Replaces the old rule-based ref_library + single-direction attach_refs.
    # Extraction is LLM-based, one-shot & checkpointed (auto-run if the raw
    # atom file is missing). Matching binds specific/general ref_clues onto
    # EVERY finding (specific layer first, general layer after).
    #
    # Unknown gate: if the ref atoms still carry `unknown` (kind neither
    # specific nor general), extraction has not been human-adjudicated yet.
    # We STOP here and print what needs manual review — no downstream match.
    if not args.no_ref_augment:
        from rtl_bug_agent.phase2 import ref_extract, ref_match

        ref_out_dir = Path(args.ref_out_dir) if args.ref_out_dir else out_root / "ref_out"
        raw_path = ref_out_dir / f"{ip}_ref_raw.json"

        if ip in ref_extract.SKIP_MODULES:
            print(f"\nRef matching: {ip} in SKIP_MODULES (no official spec), skipped")
        else:
            # 1) auto-extract if raw atoms missing (or forced fresh)
            if args.ref_fresh or not raw_path.exists():
                print("\n" + "=" * 60)
                print(f"Ref extraction (LLM, one-shot + checkpointed): {ip}")
                print("=" * 60)
                raw_path = ref_extract.extract_refs(
                    ip, ref_out_dir,
                    workers=args.ref_workers, fresh=args.ref_fresh,
                    client=client,
                )
            else:
                print(f"\nRef extraction: reuse existing {raw_path}")

            if raw_path is None or not Path(raw_path).exists():
                print("  [warn] no ref atoms produced; skipping ref matching")
            else:
                atoms = ref_match.load_atoms(str(raw_path))
                unknowns = ref_match.unknown_atoms(atoms)
                if unknowns:
                    # HARD STOP — human must adjudicate unknown atoms first.
                    print("\n" + "!" * 60)
                    print(f"STOP: {len(unknowns)} ref atom(s) are still `unknown` "
                          f"and need manual specific/general adjudication before matching.")
                    print(f"  Edit {raw_path} — set each atom's ref_kind to "
                          f"'specific' or 'general', then re-run.")
                    print("!" * 60)
                    for a in unknowns:
                        print(f"  - {a.get('ref_id')}  kw={a.get('keywords')}")
                        print(f"      {str(a.get('ref_content', ''))[:160]}")
                    # Persist findings so far (no ref_clues), then exit non-zero.
                    out_path.write_text(
                        json.dumps(
                            {"_stats": client.stats(),
                             "_ref_matching": "halted_unknown_atoms",
                             "findings": [f.to_dict() for f in merged]},
                            ensure_ascii=False, indent=2,
                        ),
                        encoding="utf-8",
                    )
                    print(f"\nWrote findings (without ref_clues) → {out_path}")
                    sys.exit(2)

                # 2) bidirectional match — bind ref_clues onto ALL findings.
                #    Work on dicts so ref_clues survives into the final output.
                print("\n" + "=" * 60)
                print(f"Ref matching (bidirectional): {ip}")
                print("=" * 60)
                ref_cfg = SemanticAgConfig(
                    model_name=args.semantic_model,
                    hf_home=args.semantic_hf_home,
                    offline=not args.semantic_online_download_model,
                    batch_size=args.semantic_batch_size,
                )
                merged_dicts = [f.to_dict() for f in merged]
                ref_match.match(merged_dicts, atoms, sem_config=ref_cfg, verbose=True)
                n_with = sum(1 for f in merged_dicts if f.get("ref_clues"))
                print(f"  ref_clues attached: {n_with}/{len(merged_dicts)} findings")
                merged = merged_dicts   # downstream now consumes dicts

    # ── Formal Solver ──────────────────────────────────────────────
    if args.run_solver:
        print("\n" + "=" * 60)
        print("Formal Solver: Executing PENDING SVAs")
        print("=" * 60)
        # Collect RTL files from the IP directory.
        rtl_files = list(Path(args.rtl_root).rglob("*.sv")) + list(Path(args.rtl_root).rglob("*.v"))
        solver_work_dir = out_root / "formal_runner"
        # Convert to dicts for solver API (merged may already be dicts after
        # ref matching; _as_dict handles both Finding objects and dicts).
        merged_dicts = [_as_dict(f) for f in merged]
        updated_dicts = run_formal_solver(
            merged_dicts,
            rtl_files=rtl_files,
            work_dir=solver_work_dir,
            timeout_per_sva=args.solver_timeout,
            depth=args.solver_depth,
        )
        # Backfill formal_result into merged (works for both dicts and objects).
        for finding, updated in zip(merged, updated_dicts):
            if "formal_result" in updated:
                if hasattr(finding, "formal_result"):
                    finding.formal_result = updated["formal_result"]
                else:
                    finding["formal_result"] = updated["formal_result"]

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

    # ── Phase 3 ──
    if args.phase3_top_n > 0:
        print("\n" + "=" * 60)
        print(f"Phase 3: Source-Level Verification (top {args.phase3_top_n})")
        print("=" * 60)
        # ref_clues were already attached (bidirectional ref_match) right after
        # fusion, so merged findings carry them into Phase 3 unchanged.
        merged_dicts = [_as_dict(f) for f in merged]

        verified = verify_top_findings(
            merged_dicts, graph, client_gpt,
            top_n=args.phase3_top_n,
            official_claims=claims,
            trace_sink=trace_sink,
        )
        merged = verified

    # ── Optional formal checks ───────────────────────────────────
    formal_checks = []
    if args.formal_check_top_n > 0:
        print("\n" + "=" * 60)
        print(f"Formal Check: top {args.formal_check_top_n}")
        print("=" * 60)
        merged_dicts = [f.to_dict() if hasattr(f, "to_dict") else f for f in merged]
        formal_checks = run_formal_checks(
            merged_dicts,
            graph,
            out_root=out_root,
            top_n=args.formal_check_top_n,
            depth=args.formal_check_depth,
        )
        print(f"  Formal checks written to {out_root / 'formal_checks'}")

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
    if formal_checks:
        output["_formal_checks"] = formal_checks
    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote {out_path}")

    # Clean up transient checkpoints on success.
    # Keep B, B_semantic, and F so expensive LLM-heavy channels can be resumed.
    for ch in ["C", "D", "G"]:
        p = _ckpt_path(out_root, ip, ch)
        if p.exists():
            p.unlink()

    print("\n" + "=" * 60)
    print("Run Statistics")
    print("=" * 60)
    client.print_stats()
    print(f"  Total wall time: {elapsed:.0f}s ({elapsed / 60:.1f} min)")


if __name__ == "__main__":
    main()
