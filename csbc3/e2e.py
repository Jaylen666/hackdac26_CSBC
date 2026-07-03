"""
End-to-end CSBC v3 pipeline: chunk → extract → graph → anomaly → metrics.
Runs on one or more RTL files and produces a complete diagnostics report.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from csbc3.chunker import chunk_file, Chunk
from csbc3.parser import parse_all_assigns, find_gating_anomalies
from csbc3.always_extract import run_on_chunks
from csbc3.pipeline import build_signal_graph, match_pairs
from csbc3.metrics import compute_metrics, DesignMetrics
from csbc3.hierarchy import build_hierarchy, flatten_instances, print_hierarchy
from csbc3.unfold import unfold_file


def run_ip(
    ip: str,
    rtl_dir: str,
    top_file: str,
    top_module: str,
    search_dirs: list[str] | None = None,
    max_always_workers: int = 2,
    save_results: bool = True,
) -> dict[str, Any]:
    """Run the full CSBC v3 pipeline on one IP.

    Args:
        ip: IP name (e.g. "hmac", "aes")
        rtl_dir: path to RTL directory
        top_file: path to top-level .sv file
        top_module: name of the top module
        search_dirs: additional directories for submodule lookup
        max_always_workers: max parallel LLM calls for always blocks
        save_results: save report to output/csbc3_<ip>.json

    Returns: dict with all results
    """
    start = time.time()
    report: dict[str, Any] = {
        "ip": ip,
        "top_module": top_module,
        "top_file": top_file,
    }

    # ------- Step 1: Chunk -------
    print(f"\n{'='*60}")
    print(f"CSBC v3: {ip}")
    print(f"{'='*60}\n")

    print("Step 1: Chunking...")
    chunks = unfold_file(top_file, module_name=top_module)
    # Also chunk additional files not reachable from top
    if search_dirs:
        for d in search_dirs:
            for sv in sorted(Path(d).glob("*.sv")):
                extra = chunk_file(sv)
                existing_ids = {c.chunk_id for c in chunks}
                for c in extra:
                    if c.chunk_id not in existing_ids:
                        chunks.append(c)
                        existing_ids.add(c.chunk_id)

    by_type: dict[str, int] = {}
    for c in chunks:
        by_type[c.construct_type] = by_type.get(c.construct_type, 0) + 1

    print(f"  Total chunks: {len(chunks)}")
    for t, n in sorted(by_type.items()):
        print(f"    {t}: {n}")
    report["chunks"] = {"total": len(chunks), "by_type": by_type}

    # ------- Step 2: Assign parser -------
    print("\nStep 2: Parsing assign chunks...")
    assign_clauses = parse_all_assigns(chunks)
    print(f"  {len(assign_clauses)} assign clauses")
    report["assign_clauses"] = len(assign_clauses)

    # ------- Step 3: Always extraction -------
    print("\nStep 3: Extracting always blocks (parallel NL+Formal)...")
    always_results = run_on_chunks(chunks, max_workers=max_always_workers)
    always_formal = sum(1 for r in always_results if r.formalizable)
    always_high_u = sum(1 for r in always_results if r.nl_uncertainty == "high")
    always_mismatch = sum(1 for r in always_results if r.cross_check == "mismatch")
    print(f"  {len(always_results)} signal specs")
    print(f"    Formalizable: {always_formal}")
    print(f"    High uncertainty: {always_high_u}")
    print(f"    NL↔Formal mismatches: {always_mismatch}")
    report["always"] = {
        "signals": len(always_results),
        "formalizable": always_formal,
        "high_uncertainty": always_high_u,
        "nl_formal_mismatches": always_mismatch,
    }

    # ------- Step 4: Build hierarchy -------
    print("\nStep 4: Building design hierarchy...")
    search_paths = [Path(d) for d in (search_dirs or [])] + [Path(rtl_dir)]
    try:
        root = build_hierarchy(top_module, top_file, search_paths)
        instances = flatten_instances(root)
        print(f"  {len(instances)} instances in hierarchy")
        report["instances"] = len(instances)
    except Exception as e:
        print(f"  Hierarchy build failed: {e}")
        report["instances"] = 0

    # ------- Step 5: Signal graph -------
    print("\nStep 5: Building signal graph...")
    graph = build_signal_graph(assign_clauses, always_results)
    print(f"  {len(graph)} signals in graph")
    report["signal_graph_size"] = len(graph)

    # ------- Step 6: Anomaly detection -------
    print("\nStep 6: Structural anomaly detection (Z3-validated)...")
    t0 = time.time()
    anomalies = find_gating_anomalies(assign_clauses, validate=True)
    confirmed = [a for a in anomalies if a["verdict"] == "CONFIRMED_ANOMALY"]
    t1 = time.time()
    print(f"  {len(anomalies)} anomalies found ({len(confirmed)} confirmed) in {t1-t0:.1f}s")
    print(f"  Z3 validation: {t1-t0:.1f}s")
    for a in confirmed:
        sigs = a.get("involved_signals", [])
        print(f"    {a['finding_id']}: {', '.join(sigs)}")
    report["anomalies"] = {"total": len(anomalies), "confirmed": len(confirmed)}

    # ------- Step 7: Cross-chunk pairs -------
    print("\nStep 7: Cross-chunk contradiction check...")
    pairs = match_pairs(graph)
    print(f"  {len(pairs)} pair contradictions")
    report["pairs"] = len(pairs)

    # ------- Step 8: Metrics -------
    print("\nStep 8: Computing CSBC readiness metrics...")
    metrics = compute_metrics(chunks, assign_clauses, always_results, graph)
    print(f"\n{metrics.summary()}")
    report["metrics"] = {
        "readiness": round(metrics.readiness_score, 3),
        "signal_coverage": round(metrics.signal_coverage, 3),
        "assumption_coverage": round(metrics.assumption_coverage, 3),
        "csbc_reachability": round(metrics.csbc_reachability, 3),
        "uncertainty_rate": round(metrics.uncertainty_rate, 3),
        "formal_consistency": round(metrics.formal_consistency, 3),
        "dangling_drivers": metrics.signals_dangling_driver,
        "dangling_assumptions": metrics.signals_dangling_assumption,
    }

    elapsed = time.time() - start
    print(f"\nTotal time: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    report["elapsed_seconds"] = round(elapsed, 1)

    # Save
    if save_results:
        out_path = Path(f"output/csbc3_{ip}.json")
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"\nSaved to {out_path}")

    return report


if __name__ == "__main__":
    ip = sys.argv[1] if len(sys.argv) > 1 else "hmac"

    configs = {
        "hmac": {
            "rtl_dir": "/home/AM/hack2dac/opentitan/hw/ip/hmac/rtl",
            "top_file": "/home/AM/hack2dac/opentitan/hw/ip/hmac/rtl/hmac.sv",
            "top_module": "hmac",
            "search_dirs": [
                "/home/AM/hack2dac/opentitan/hw/ip/prim/rtl",
                "/home/AM/hack2dac/opentitan/hw/ip/tlul/rtl",
                "/home/AM/hack2dac/opentitan/hw/ip/prim_generic/rtl",
            ],
        },
        "aes": {
            "rtl_dir": "/home/AM/hack2dac/opentitan/hw/ip/aes/rtl",
            "top_file": "/home/AM/hack2dac/opentitan/hw/ip/aes/rtl/aes.sv",
            "top_module": "aes",
            "search_dirs": [
                "/home/AM/hack2dac/opentitan/hw/ip/prim/rtl",
                "/home/AM/hack2dac/opentitan/hw/ip/tlul/rtl",
            ],
        },
    }

    cfg = configs.get(ip)
    if not cfg:
        print(f"Unknown IP: {ip}. Known: {list(configs.keys())}")
        sys.exit(1)

    run_ip(ip, **cfg)
