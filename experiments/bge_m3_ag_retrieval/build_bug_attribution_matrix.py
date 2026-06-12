#!/usr/bin/env python3
"""Build a bug attribution matrix from existing AG, uncertain, and review data.

This experiment-local script is intentionally separate from the main framework.
It uses keyword-group coverage as a proxy for whether a known bug's semantics
entered a legacy/semantic LLM work item, then joins that with curated Phase 3
review results.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = Path("/home/smy/rtl_bug_agent")
OUT = HERE / "out"
PLOTS = OUT / "plots"

sys.path.insert(0, str(HERE))

import evaluate_bug_ag_coverage as cov  # noqa: E402


MODULE_SPECS = {
    "hmac": REPO / "output/specs",
    "aes": REPO / "output/specs_aes",
    "keymgr": REPO / "output/specs_keymgr",
    "kmac": REPO / "output/specs_kmac",
    "rv_dm": REPO / "output/specs_rv_dm",
    "uart": REPO / "output/specs_uart",
}


EXTRA_PROFILES: tuple[cov.BugProfile, ...] = (
    cov.BugProfile(
        id="026",
        name="keymgr_bug_001_invalid_stage_raw_key",
        module="keymgr",
        description="Invalid-stage output exposes raw key_state_q instead of entropy-only masked value.",
        subcases=(
            cov.Subcase(
                "invalid_stage_raw_key_path",
                "invalid_stage_sel_o and StCtrlInvalid path references raw key_state_q",
                (("invalid_stage_sel_o", "invalid_stage_sel"), ("StCtrlInvalid",), ("key_state_q",)),
            ),
            cov.Subcase(
                "invalid_stage_entropy_mask_contract",
                "invalid stage output should use entropy/LFSR masking rather than key material",
                (("invalid_stage_sel",), ("entropy", "lfsr", "mask"), ("key_state", "key_o")),
            ),
        ),
        note="Known to be Phase 2 missed and Phase 3 independently discovered.",
    ),
    cov.BugProfile(
        id="031",
        name="KEYMGR-TRIAGE-004_data_en_illegal_redirect",
        module="keymgr",
        description="Illegal encoding injected into keymgr data-enable FSM is silently redirected instead of raising integrity alarm.",
        subcases=(
            cov.Subcase(
                "data_en_fsm_error_missing",
                "data enable FSM exposes fsm_err_o / illegal state handling",
                (("data_en", "data_hw_en_o", "data_sw_en_o"), ("fsm_err_o", "fsm_err"), ("illegal", "非法", "恒为 0")),
            ),
        ),
    ),
    cov.BugProfile(
        id="N-003",
        name="keymgr_ecc_stale",
        module="keymgr",
        description="key_state_ecc_q updates omit key_state_q data write-back, causing stale data/new ECC mismatch.",
        subcases=(
            cov.Subcase(
                "ecc_updates_without_data_writeback",
                "key_state_ecc_q updates from key_state_ecc_words_d while key_state_q data write-back can be stale",
                (("key_state_ecc_q",), ("key_state_ecc_words_d",), ("key_state_q", "key_state_d")),
            ),
            cov.Subcase(
                "ecc_errs_from_mismatch",
                "ecc_errs observes mismatch between key_state_ecc_q and key_state_q/key_state_d",
                (("ecc_errs", "ecc_err"), ("key_state_ecc_q",), ("key_state_q", "key_state_d")),
            ),
        ),
    ),
    cov.BugProfile(
        id="017",
        name="kmac_bug_001_static_mask",
        module="kmac",
        description="Constant/static all-ones masking contribution instead of fresh mask input.",
        subcases=(
            cov.Subcase(
                "static_mask_permutation",
                "msg_mask_permuted is derived from static_mask / RndCnstMsgPerm",
                (("static_mask",), ("msg_mask_permuted",), ("RndCnstMsgPerm",)),
            ),
            cov.Subcase(
                "fresh_mask_absent",
                "masking path lacks a fresh mask/random driver in the local spec",
                (("static_mask", "msg_mask_permuted"), ("fresh", "random", "entropy", "driver", "未驱动")),
            ),
        ),
        note="Known benchmark says two uncertain points describe it; root cause is mostly Phase 3-only.",
    ),
    cov.BugProfile(
        id="021",
        name="kmac_bug_003_alert_ping_skew",
        module="kmac",
        description="Shared prim_alert_sender ping-skew root cause affects KMAC alert sender instances.",
        subcases=(
            cov.Subcase(
                "kmac_alert_sender_interface",
                "KMAC instantiates prim_alert_sender and wires alerts to alert_tx_o",
                (("prim_alert_sender",), ("alert_tx_o",), ("alerts", "alert_req_i")),
            ),
            cov.Subcase(
                "ping_skew_root_cause",
                "ping/skew/differential behavior is visible in KMAC-local specs",
                (("ping",), ("skew", "differential", "diff")),
                note="Expected mostly absent because the root cause is in a shared primitive.",
            ),
        ),
    ),
    cov.BugProfile(
        id="036",
        name="KMAC-BUG-002_sparse_fsm_error_delay",
        module="kmac",
        description="kmac_core suppresses sparse_fsm_error_o for 100 cycles after StTerminalError.",
        subcases=(
            cov.Subcase(
                "terminal_error_delayed_sparse_error",
                "StTerminalError path delays sparse_fsm_error_o through st_err_ct threshold",
                (("StTerminalError",), ("sparse_fsm_error_o",), ("st_err_ct", "100")),
            ),
        ),
    ),
    cov.BugProfile(
        id="N-005",
        name="kmac_reduced_share_unpacker",
        module="kmac",
        description="kmac_reduced share1 unpacker is instantiated when EnMasking=0 / NumShares=1, accessing msg_i[1].",
        subcases=(
            cov.Subcase(
                "share1_unpacker_when_numshares_one",
                "NumShares can be 1 but share1 unpacker accesses msg_i[1]/msg[1]",
                (("NumShares",), ("EnMasking",), ("msg_i[1]", "msg[1]", "share1")),
            ),
        ),
    ),
    cov.BugProfile(
        id="022",
        name="rv_dm_bug_004",
        module="rv_dm",
        description="RV_DM benchmark bug; current benchmark lacks a precise local description.",
        subcases=(
            cov.Subcase(
                "rv_dm_generic_debug_control",
                "Generic debug control signals visible",
                (("debug",), ("rv_dm", "dm")),
                note="Low-specificity proxy because BENCHMARKS.md does not describe the root cause.",
            ),
        ),
    ),
    cov.BugProfile(
        id="034",
        name="RV_DM-TRIAGE-003_pending_dmi_response_drop",
        module="rv_dm",
        description="rv_dm_dmi_gate suppresses the last pending DMI response when dmi_en drops during completion.",
        subcases=(
            cov.Subcase(
                "dmi_gate_pending_response",
                "dmi_en drop intersects pending DMI request/response completion",
                (("dmi_en",), ("pending",), ("response", "rsp", "resp", "completion")),
            ),
        ),
    ),
    cov.BugProfile(
        id="046",
        name="rv_dm_bug_005_stale_debug_authorization",
        module="rv_dm",
        description="Debug strap sample leaves pinmux holding stale RV debug authorization after LC permission returns Off.",
        subcases=(
            cov.Subcase(
                "strap_pinmux_lc_debug",
                "strap/pinmux debug authorization depends on lifecycle debug enable",
                (("strap",), ("pinmux",), ("lc_hw_debug_en", "lifecycle", "lc_")),
            ),
        ),
    ),
    cov.BugProfile(
        id="047",
        name="rv_dm_bug_002_late_debug_ndmreset",
        module="rv_dm",
        description="Late debug enable via regs_tl path, DMI ndmreset, and pending-halt integration bug.",
        subcases=(
            cov.Subcase(
                "late_debug_ndmreset_pending_halt",
                "late debug enable combines with ndmreset and pending halt/debug state",
                (("ndmreset",), ("late", "lc_hw_debug_en", "debug"), ("pending", "halt")),
            ),
        ),
    ),
    cov.BugProfile(
        id="033",
        name="uart_bug_002_lsio_trigger_watermark",
        module="uart",
        description="lsio_trigger_o is unconditionally asserted after reset, ignoring watermark.",
        subcases=(
            cov.Subcase(
                "lsio_trigger_unconditional",
                "lsio_trigger_o is set after reset and stays high",
                (("lsio_trigger_o",), ("复位", "reset"), ("永久保持", "跳变为 1", "unconditional", "always")),
            ),
            cov.Subcase(
                "watermark_contract",
                "UART watermark event semantics are present",
                (("watermark",), ("event_tx_watermark", "rx_watermark", "watermark_thresh")),
            ),
        ),
        note="Known to be Phase 2 missed and Phase 3 independently discovered.",
    ),
    cov.BugProfile(
        id="N-004",
        name="uart_break_interrupt",
        module="uart",
        description="Break FSM re-arms on a single rx_in high sample without half-bit-time stability check.",
        subcases=(
            cov.Subcase(
                "break_rearm_on_rx_high",
                "BRK_WAIT returns/re-arms when rx_in becomes high",
                (("break", "BRK_WAIT"), ("rx_in",), ("高电平", "high", "空闲")),
            ),
            cov.Subcase(
                "half_bit_time_absent",
                "Half-bit-time stability requirement is visible in specs",
                (("half", "半个"), ("bit", "time", "baud")),
                note="Expected absent in current RTL-generated specs; absence is part of the finding.",
            ),
        ),
    ),
)


CONFIRMED_FINDING_MAP: dict[tuple[str, str], list[str]] = {
    ("aes", "004"): ["F-0007"],
    ("aes", "003"): ["F-0007"],
    ("aes", "N-001"): ["F-0009"],
    ("aes", "N-002"): ["F-0040"],
    ("hmac", "009"): ["F-0027", "F-0031"],
    ("hmac", "010"): ["F-0025", "F-0029", "F-0032"],
    ("keymgr", "026"): ["F-EXTRA-0001"],
    ("keymgr", "N-003"): ["F-0002", "F-0004"],
    ("kmac", "N-005"): ["F-0073"],
    ("uart", "033"): ["F-EXTRA-0001"],
    ("uart", "N-004"): ["F-EXTRA-0002"],
}

BENCHMARK_SOURCE: dict[tuple[str, str], str] = {
    ("aes", "004"): "contest",
    ("aes", "005"): "contest",
    ("aes", "003"): "contest",
    ("aes", "N-001"): "Codex Phase 3",
    ("aes", "N-002"): "Codex Phase 3",
    ("hmac", "009"): "contest",
    ("hmac", "010"): "contest",
    ("hmac", "011"): "contest",
    ("hmac", "019"): "contest",
    ("keymgr", "026"): "contest; Phase 2 missed; Phase 3 independently discovered",
    ("keymgr", "031"): "contest",
    ("keymgr", "N-003"): "Codex Phase 3",
    ("kmac", "017"): "contest; Phase 3 only",
    ("kmac", "021"): "contest; shared primitive",
    ("kmac", "036"): "contest",
    ("kmac", "N-005"): "Codex Phase 3",
    ("rv_dm", "022"): "contest; underspecified benchmark entry",
    ("rv_dm", "034"): "contest",
    ("rv_dm", "046"): "contest",
    ("rv_dm", "047"): "contest",
    ("uart", "033"): "contest; Phase 2 missed; Phase 3 independently discovered",
    ("uart", "N-004"): "Codex Phase 3",
}


def setup_profiles() -> None:
    cov.MODULE_SPECS = MODULE_SPECS
    existing = list(cov.BUG_PROFILES)
    seen = {(p.module, p.id) for p in existing}
    for profile in EXTRA_PROFILES:
        if (profile.module, profile.id) not in seen:
            existing.append(profile)
    cov.BUG_PROFILES = tuple(existing)


def method_rollup(subcases: list[dict[str, Any]], method: str) -> dict[str, Any]:
    observable = [
        s for s in subcases
        if s["observability"]["atom_present"] or s["observability"]["corpus_present"]
    ]
    hits = [s["methods"][method] for s in observable if s["methods"][method]["hit"]]
    best = None
    for h in hits:
        rank = h.get("best_rank")
        if rank is not None and (best is None or rank < best.get("best_rank", 10**9)):
            best = h
    return {
        "observable_subcases": len(observable),
        "total_subcases": len(subcases),
        "hit_subcases": len(hits),
        "any_hit": bool(hits),
        "full_hit": bool(observable) and len(hits) == len(observable),
        "best_rank": best.get("best_rank") if best else None,
        "best_score": best.get("best_score") if best else None,
    }


def phase3_role(module: str, bug_id: str, confirmed: list[str], legacy_hit: bool, semantic_hit: bool, uncertain_hit: bool) -> str:
    source = BENCHMARK_SOURCE.get((module, bug_id), "")
    source_l = source.lower()
    if "phase 2 missed" in source_l and confirmed:
        return "independent_phase3_discovery"
    if "codex phase 3" in source_l and confirmed:
        return "phase3_discovery"
    if "phase 3 only" in source_l and confirmed:
        return "phase3_discovery"
    if confirmed and (legacy_hit or semantic_hit or uncertain_hit):
        return "phase3_verified_framework_candidate"
    if confirmed:
        return "phase3_expanded_discovery"
    if legacy_hit or semantic_hit or uncertain_hit:
        return "candidate_covered_not_confirmed"
    return "missed_or_unobservable"


def confidence_for(row: dict[str, Any]) -> str:
    source_l = row.get("benchmark_source", "").lower()
    name_l = row.get("bug_name", "").lower()
    notes_l = row.get("notes", "").lower()
    if "underspecified" in source_l or "shared primitive" in source_l:
        return "low"
    if "alert_ping_skew" in name_l or "shared primitive" in notes_l:
        return "low"
    if row["confirmed_finding_ids"] and row["observable_subcases"] > 0:
        return "high"
    if row["confirmed_finding_ids"]:
        return "medium"
    if row["observable_subcases"] > 0 and (row["semantic_any_hit"] or row["legacy_plus_uncertain_any_hit"]):
        return "medium"
    return "low"


def build_matrix(coverage: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for module_row in coverage["modules"]:
        module = module_row["module"]
        for bug in module_row["bugs"]:
            legacy = method_rollup(bug["subcases"], "legacy_ag_only")
            legacy_plus_unc = method_rollup(bug["subcases"], "legacy_signal_plus_uncertain")
            uncertain = method_rollup(bug["subcases"], "legacy_uncertain_only")
            semantic = method_rollup(bug["subcases"], "optimized_paired_only")
            semantic_plus_unc = method_rollup(bug["subcases"], "optimized_with_unmatched_uncertain")
            semantic_unmatched = method_rollup(bug["subcases"], "optimized_unmatched_uncertain_only")
            confirmed = CONFIRMED_FINDING_MAP.get((module, bug["bug_id"]), [])
            observable = max(
                legacy["observable_subcases"],
                legacy_plus_unc["observable_subcases"],
                semantic_plus_unc["observable_subcases"],
            )
            row = {
                "module": module,
                "bug_id": bug["bug_id"],
                "bug_name": bug["name"],
                "benchmark_source": BENCHMARK_SOURCE.get((module, bug["bug_id"]), "unknown"),
                "description": bug["description"],
                "observable_subcases": observable,
                "total_subcases": len(bug["subcases"]),
                "legacy_ag_any_hit": legacy["any_hit"],
                "legacy_ag_full_hit": legacy["full_hit"],
                "legacy_ag_best_rank": legacy["best_rank"],
                "legacy_plus_uncertain_any_hit": legacy_plus_unc["any_hit"],
                "legacy_plus_uncertain_full_hit": legacy_plus_unc["full_hit"],
                "legacy_plus_uncertain_best_rank": legacy_plus_unc["best_rank"],
                "uncertain_only_any_hit": uncertain["any_hit"],
                "uncertain_only_best_rank": uncertain["best_rank"],
                "semantic_any_hit": semantic["any_hit"],
                "semantic_full_hit": semantic["full_hit"],
                "semantic_best_rank": semantic["best_rank"],
                "semantic_best_score": semantic["best_score"],
                "semantic_plus_uncertain_any_hit": semantic_plus_unc["any_hit"],
                "semantic_plus_uncertain_full_hit": semantic_plus_unc["full_hit"],
                "semantic_plus_uncertain_best_rank": semantic_plus_unc["best_rank"],
                "semantic_unmatched_uncertain_any_hit": semantic_unmatched["any_hit"],
                "confirmed_finding_ids": confirmed,
                "phase3_role": phase3_role(
                    module,
                    bug["bug_id"],
                    confirmed,
                    legacy["any_hit"],
                    semantic["any_hit"],
                    uncertain["any_hit"] or semantic_unmatched["any_hit"],
                ),
                "notes": bug.get("note", ""),
            }
            row["confidence"] = confidence_for(row)
            rows.append(row)
    return rows


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fields = [
        "module", "bug_id", "bug_name", "benchmark_source", "observable_subcases", "total_subcases",
        "legacy_ag_any_hit", "legacy_ag_best_rank",
        "legacy_plus_uncertain_any_hit", "legacy_plus_uncertain_best_rank",
        "uncertain_only_any_hit", "uncertain_only_best_rank",
        "semantic_any_hit", "semantic_best_rank", "semantic_best_score",
        "semantic_plus_uncertain_any_hit", "semantic_plus_uncertain_best_rank",
        "semantic_unmatched_uncertain_any_hit",
        "confirmed_finding_ids", "phase3_role", "confidence", "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            out = dict(row)
            out["confirmed_finding_ids"] = ",".join(row["confirmed_finding_ids"])
            writer.writerow({k: out.get(k) for k in fields})


def write_markdown(rows: list[dict[str, Any]], coverage: dict[str, Any], path: Path) -> None:
    lines = [
        "# Bug Attribution Matrix",
        "",
        "This matrix joins known benchmark bugs with AG/uncertain coverage proxy results and curated Phase 3 review outcomes.",
        "A coverage hit means one work item contains all keyword groups for at least one known-bug subcase; it is not a substitute for LLM verification.",
        "",
        "## Matrix",
        "",
        "| Module | Bug | Observable | Legacy AG | Legacy+U | U only | Semantic AG | Semantic+U | Confirmed | Attribution | Confidence |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for r in rows:
        def hit(v: bool, rank: Any = None) -> str:
            if not v:
                return "miss"
            return f"hit@{rank}" if rank is not None else "hit"

        lines.append(
            "| "
            + " | ".join(
                [
                    r["module"],
                    f"{r['bug_id']} {r['bug_name']}",
                    f"{r['observable_subcases']}/{r['total_subcases']}",
                    hit(r["legacy_ag_any_hit"], r["legacy_ag_best_rank"]),
                    hit(r["legacy_plus_uncertain_any_hit"], r["legacy_plus_uncertain_best_rank"]),
                    hit(r["uncertain_only_any_hit"], r["uncertain_only_best_rank"]),
                    hit(r["semantic_any_hit"], r["semantic_best_rank"]),
                    hit(r["semantic_plus_uncertain_any_hit"], r["semantic_plus_uncertain_best_rank"]),
                    ",".join(r["confirmed_finding_ids"]) or "-",
                    r["phase3_role"],
                    r["confidence"],
                ]
            )
            + " |"
        )

    lines.extend(["", "## Coverage Summary", ""])
    by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_module[row["module"]].append(row)
    lines.append("| Module | Bugs | Confirmed | Legacy AG any | Legacy+U any | Semantic AG any | Semantic+U any | Unobservable |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for module, mrows in sorted(by_module.items()):
        lines.append(
            f"| {module} | {len(mrows)} | "
            f"{sum(bool(r['confirmed_finding_ids']) for r in mrows)} | "
            f"{sum(r['legacy_ag_any_hit'] for r in mrows)} | "
            f"{sum(r['legacy_plus_uncertain_any_hit'] for r in mrows)} | "
            f"{sum(r['semantic_any_hit'] for r in mrows)} | "
            f"{sum(r['semantic_plus_uncertain_any_hit'] for r in mrows)} | "
            f"{sum(r['observable_subcases'] == 0 for r in mrows)} |"
        )

    lines.extend(["", "## Work-Item Counts", ""])
    lines.append("| Module | legacy_ag_only | legacy+uncertain | semantic_paired | semantic+unmatched_uncertain |")
    lines.append("|---|---:|---:|---:|---:|")
    for module_row in coverage["modules"]:
        c = module_row["method_unit_counts"]
        lines.append(
            f"| {module_row['module']} | {c['legacy_ag_only']} | {c['legacy_signal_plus_uncertain']} | "
            f"{c['optimized_paired_only']} | {c['optimized_with_unmatched_uncertain']} |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot(rows: list[dict[str, Any]], coverage: dict[str, Any]) -> None:
    import matplotlib.pyplot as plt

    PLOTS.mkdir(parents=True, exist_ok=True)
    modules = sorted({r["module"] for r in rows})

    def counts_for(field: str) -> list[int]:
        return [sum(1 for r in rows if r["module"] == m and r[field]) for m in modules]

    totals = [sum(1 for r in rows if r["module"] == m) for m in modules]
    x = range(len(modules))

    plt.figure(figsize=(11, 5))
    width = 0.18
    for offset, field, label in [
        (-1.5 * width, "legacy_ag_any_hit", "legacy AG"),
        (-0.5 * width, "legacy_plus_uncertain_any_hit", "legacy+U"),
        (0.5 * width, "semantic_any_hit", "semantic AG"),
        (1.5 * width, "semantic_plus_uncertain_any_hit", "semantic+U"),
    ]:
        plt.bar([i + offset for i in x], counts_for(field), width=width, label=label)
    plt.plot(list(x), totals, color="black", marker="o", linewidth=1, label="known bugs")
    plt.xticks(list(x), modules)
    plt.ylabel("Bug count with any coverage hit")
    plt.title("Known-bug coverage by pairing mode")
    plt.legend()
    plt.tight_layout()
    plt.savefig(PLOTS / "bug_attribution_coverage_by_module.png", dpi=180)
    plt.close()

    role_counts = Counter(r["phase3_role"] for r in rows)
    plt.figure(figsize=(10, 4.8))
    labels = list(role_counts)
    plt.bar(labels, [role_counts[k] for k in labels], color="#4f6f52")
    plt.xticks(rotation=25, ha="right")
    plt.ylabel("Bug count")
    plt.title("Attribution role distribution")
    plt.tight_layout()
    plt.savefig(PLOTS / "bug_attribution_phase3_roles.png", dpi=180)
    plt.close()

    methods = [
        ("legacy_ag_only", "legacy AG"),
        ("legacy_signal_plus_uncertain", "legacy+U"),
        ("optimized_paired_only", "semantic AG"),
        ("optimized_with_unmatched_uncertain", "semantic+U"),
    ]
    method_units = {m["module"]: m["method_unit_counts"] for m in coverage["modules"]}
    plt.figure(figsize=(10, 5))
    for method, label in methods:
        xs = []
        ys = []
        for module in modules:
            mrows = [r for r in rows if r["module"] == module]
            units = method_units[module][method]
            if method == "legacy_ag_only":
                hits = sum(r["legacy_ag_any_hit"] for r in mrows)
            elif method == "legacy_signal_plus_uncertain":
                hits = sum(r["legacy_plus_uncertain_any_hit"] for r in mrows)
            elif method == "optimized_paired_only":
                hits = sum(r["semantic_any_hit"] for r in mrows)
            else:
                hits = sum(r["semantic_plus_uncertain_any_hit"] for r in mrows)
            recall = hits / len(mrows) if mrows else 0
            xs.append(units)
            ys.append(recall)
        plt.scatter(xs, ys, label=label, s=60)
        for module, xval, yval in zip(modules, xs, ys):
            plt.annotate(module, (xval, yval), fontsize=8, xytext=(3, 3), textcoords="offset points")
    plt.xlabel("Work items")
    plt.ylabel("Any-hit bug coverage")
    plt.title("Coverage proxy vs LLM work-item count")
    plt.legend()
    plt.tight_layout()
    plt.savefig(PLOTS / "bug_attribution_recall_vs_work_items.png", dpi=180)
    plt.close()


def main() -> None:
    setup_profiles()
    modules = ["hmac", "aes", "keymgr", "kmac", "rv_dm", "uart"]
    coverage = {
        "config": {
            "modules": modules,
            "method": "keyword-group proxy joined with curated Phase 3 review mapping",
            "warning": "coverage hit is not equivalent to a confirmed bug; shared-component and underspecified benchmark bugs may be unobservable",
        },
        "modules": [cov.evaluate_module(module) for module in modules],
    }
    rows = build_matrix(coverage)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "bug_attribution_coverage_full.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUT / "bug_attribution_matrix.json").write_text(
        json.dumps({"rows": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(rows, OUT / "bug_attribution_matrix.csv")
    write_markdown(rows, coverage, OUT / "bug_attribution_matrix.md")
    plot(rows, coverage)

    print(f"wrote {OUT / 'bug_attribution_matrix.json'}")
    print(f"wrote {OUT / 'bug_attribution_matrix.md'}")
    print(f"wrote plots under {PLOTS}")


if __name__ == "__main__":
    main()
