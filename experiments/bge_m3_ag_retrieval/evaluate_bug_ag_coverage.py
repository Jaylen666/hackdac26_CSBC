#!/usr/bin/env python3
"""Evaluate known-bug AG/uncertain coverage for legacy vs semantic pairing.

This is an experiment-local quality proxy. It does not call an LLM and does
not modify the main framework.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPO = Path("/home/smy/rtl_bug_agent")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

import plan_llm_batches as batch_plan  # noqa: E402
from rtl_bug_agent.phase2.signal_graph import build_signal_graph  # noqa: E402


MODULE_SPECS = {
    "hmac": REPO / "output/specs",
    "aes": REPO / "output/specs_aes",
}


@dataclass(frozen=True)
class Subcase:
    id: str
    description: str
    groups: tuple[tuple[str, ...], ...]
    note: str = ""


@dataclass(frozen=True)
class BugProfile:
    id: str
    name: str
    module: str
    description: str
    subcases: tuple[Subcase, ...]
    note: str = ""


BUG_PROFILES: tuple[BugProfile, ...] = (
    BugProfile(
        id="009",
        name="hmac_bug_002_wipe_secret",
        module="hmac",
        description="Legal wipe_secret does not clear secret state for a later key=NULL HMAC run.",
        subcases=(
            Subcase(
                "wipe_to_secret_key",
                "wipe event reaches secret_key_d / secret_key datapath",
                (("wipe_secret",), ("secret_key_d", "secret_key")),
            ),
            Subcase(
                "cfg_block_key_update_lifecycle",
                "secret-key update depends on cfg_block lifecycle",
                (("cfg_block",), ("secret_key_d", "secret_key", "reg2hw.key")),
            ),
        ),
    ),
    BugProfile(
        id="010",
        name="hmac_bug_003_sha512_outer_len",
        module="hmac",
        description="SHA-512 HMAC outer-round message length falls back to SHA-384 length.",
        subcases=(
            Subcase(
                "sha512_outer_default_384",
                "final HMAC length has SHA2_512/default path returning 384-bit inner digest length",
                (("sha_msg_len", "sha_message_length"), ("SHA2_512",), ("384", "BlockSizeSHA512in64 + 384")),
            ),
        ),
    ),
    BugProfile(
        id="011",
        name="hmac_bug_004_stale_completion",
        module="hmac",
        description="hmac_idle opens before stale completion/digest signals are drained.",
        subcases=(
            Subcase(
                "done_state_hash_done_event",
                "done_state controls hash_done_event generation",
                (("done_state",), ("hash_done_event",)),
            ),
            Subcase(
                "in_process_completion_lifecycle",
                "in_process follows start/done/stop lifecycle",
                (("in_process",), ("reg_hash_done", "hash_done_event", "hash_process", "reg_hash_stop")),
            ),
            Subcase(
                "cool_down_completion_window",
                "cool_down counter participates in completion timing",
                (("cool_down", "cool_down_ct"),),
            ),
        ),
        note="This proxy checks whether completion-lifecycle ingredients are visible, not the full stale-digest temporal witness.",
    ),
    BugProfile(
        id="019",
        name="hmac_bug_005_alert_ping_skew",
        module="hmac",
        description="prim_alert_sender ping skew can suppress an expected alert handshake.",
        subcases=(
            Subcase(
                "hmac_alert_sender_interface",
                "HMAC alert sender instance exposes alert_tx_o and request input wiring",
                (("prim_alert_sender", "alert_tx_o"), ("alert_req_i", "alerts[0]", "alerts")),
            ),
            Subcase(
                "ping_skew_root_cause",
                "ping/skew handshake semantics are present in the module spec",
                (("ping",), ("skew", "differential", "diff")),
                note="Expected to be absent from hmac-only specs because the root cause is in prim_alert_sender.",
            ),
        ),
        note="The shared primitive root cause is usually not observable from hmac-only AG pairs.",
    ),
    BugProfile(
        id="004",
        name="aes_bug_002_state_clear_retention",
        module="aes",
        description="AES-128/192 state-clear default path preserves residual state instead of wiping.",
        subcases=(
            Subcase(
                "state_default_preserve",
                "state mux default path may preserve old state",
                (("state_d",), ("default", "未定义"), ("保持原值", "state_d = state_d", "保留")),
            ),
            Subcase(
                "key_length_dependent_clear",
                "state clear fallback depends on key_len_i / AES_256 rather than clearing all key lengths",
                (("key_len_i", "key_len"), ("AES_256", "AES-256"), ("prd_clearing_state_i", "clearing_state")),
            ),
        ),
    ),
    BugProfile(
        id="005",
        name="aes_bug_001_key_clear_mux",
        module="aes",
        description="KEY_FULL_CLEAR and KEY_DEC_CLEAR route key_expand_out instead of clearing source.",
        subcases=(
            Subcase(
                "key_full_clear_expand",
                "KEY_FULL_CLEAR selects key_expand_out",
                (("KEY_FULL_CLEAR",), ("key_expand_out",)),
            ),
            Subcase(
                "key_dec_clear_expand",
                "KEY_DEC_CLEAR selects key_expand_out",
                (("KEY_DEC_CLEAR",), ("key_expand_out",)),
            ),
        ),
    ),
    BugProfile(
        id="003",
        name="aes_bug_003_sw_key_clear_state_retention",
        module="aes",
        description="Software-visible witness for AES key clearing / state retention.",
        subcases=(
            Subcase(
                "key_clear_or_state_retention_witness",
                "at least one key-clear/state-retention witness ingredient is visible",
                (("KEY_FULL_CLEAR", "KEY_DEC_CLEAR", "state_d"), ("key_expand_out", "保持原值", "prd_clearing_state_i")),
            ),
        ),
        note="This benchmark entry overlaps the root causes of 004 and 005, so it is treated as a witness-level coverage check.",
    ),
    BugProfile(
        id="N-001",
        name="aes_key_words_sel_fault_fold",
        module="aes",
        description="OR-merged redundant rails can fold key_words_sel into a wrong legal selector.",
        subcases=(
            Subcase(
                "or_merge_key_words_selector",
                "mr_key_words_sel is OR-merged and later consumed as key_words_sel",
                (("mr_key_words_sel",), ("key_words_sel", "key_words_sel_o"), ("mr_err", "按位或", "OR")),
            ),
            Subcase(
                "key_words_zero_encoding",
                "KEY_WORDS_ZERO / selector encoding context is visible",
                (("KEY_WORDS_ZERO",), ("key_words_sel", "KEY_WORDS")),
            ),
        ),
    ),
    BugProfile(
        id="N-002",
        name="aes_iv_sel_fault_fold",
        module="aes",
        description="OR-merged iv_sel can write a wrong CTR IV source because iv_we is not gated by mux_sel_err.",
        subcases=(
            Subcase(
                "iv_ctr_write_not_gated",
                "iv_sel/IV_CTR/iv_we CTR update path is visible",
                (("iv_sel",), ("iv_we",), ("IV_CTR", "CTR"), ("mux_sel_err", "mr_err")),
            ),
        ),
        note="Expected to be absent from the current aes mini specs if iv_reg/iv_sel chunks were not generated.",
    ),
)


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def group_hit(text: str, group: tuple[str, ...]) -> bool:
    hay = norm(text)
    return any(term.lower() in hay for term in group)


def subcase_hit(text: str, subcase: Subcase) -> bool:
    return all(group_hit(text, group) for group in subcase.groups)


def load_atoms(module: str) -> list[dict[str, Any]]:
    atoms_path = HERE / f"out/atoms_{module}.jsonl"
    atoms = []
    with atoms_path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                atoms.append(json.loads(line))
    return atoms


def atom_full_text(atom: dict[str, Any]) -> str:
    parts = [
        atom.get("atom_id", ""),
        atom.get("kind", ""),
        atom.get("text", ""),
        atom.get("embedding_text", ""),
        json.dumps(atom.get("raw", ""), ensure_ascii=False),
        " ".join(atom.get("signals", []) or []),
        " ".join(atom.get("source_refs", []) or []),
    ]
    return "\n".join(str(p) for p in parts if p)


def concise(text: str, terms: list[str], width: int = 220) -> str:
    hay = text.replace("\n", " ")
    low = hay.lower()
    positions = [low.find(t.lower()) for t in terms if low.find(t.lower()) >= 0]
    if not positions:
        return hay[:width]
    pos = max(0, min(positions) - 70)
    return hay[pos : pos + width]


def build_legacy_units(module: str) -> dict[str, list[dict[str, Any]]]:
    specs_dir = MODULE_SPECS[module]
    graph = build_signal_graph(specs_dir)
    pairs = graph.find_ag_pairs(filter_mode="behavioral")
    ag_units = []
    edge_idx = 0
    for pair_idx, pair in enumerate(pairs):
        assumption = pair["assumption"]
        for guarantee_item in pair.get("driver_guarantees", []):
            edge_idx += 1
            guarantee = guarantee_item["guarantee"]
            text = "\n".join(
                [
                    f"LEGACY_AG_EDGE {edge_idx}",
                    f"signal={pair.get('signal')}",
                    f"consumer_spec={pair.get('consumer_spec')}",
                    "assumption=" + assumption.get("constraint", ""),
                    "bug_relevance=" + assumption.get("bug_relevance", ""),
                    f"driver_spec={guarantee_item.get('spec_id')}",
                    "guarantee=" + guarantee.get("property", ""),
                    "assumption_signals=" + " ".join(assumption.get("related_signals", []) or []),
                    "guarantee_signals=" + " ".join(guarantee.get("output_signals", []) or []),
                ]
            )
            ag_units.append(
                {
                    "unit_id": f"legacy_ag::{module}::{edge_idx}",
                    "kind": "legacy_ag",
                    "rank": edge_idx,
                    "pair_index": pair_idx,
                    "signal": pair.get("signal"),
                    "text": text,
                }
            )

    atoms = load_atoms(module)
    uncertain_units = []
    for idx, atom in enumerate([a for a in atoms if a["kind"] == "uncertain"], start=1):
        text = "\n".join(
            [
                f"LEGACY_UNCERTAIN {idx}",
                f"query={atom['atom_id']}",
                "uncertain=" + atom.get("text", ""),
                "signals=" + " ".join(atom.get("signals", []) or []),
                "source_refs=" + " ".join(atom.get("source_refs", []) or []),
            ]
        )
        uncertain_units.append(
            {
                "unit_id": f"legacy_uncertain::{module}::{idx}",
                "kind": "legacy_uncertain",
                "rank": idx,
                "query_atom_id": atom["atom_id"],
                "text": text,
            }
        )

    return {
        "legacy_ag_only": ag_units,
        "legacy_signal_plus_uncertain": ag_units + uncertain_units,
        "legacy_uncertain_only": uncertain_units,
    }


def render_optimized_unit(item: dict[str, Any]) -> str:
    query = item["query"]
    lines = [
        f"OPTIMIZED_QUERY {query['atom_id']} kind={query['kind']}",
        "query=" + query.get("text", ""),
        "query_signals=" + " ".join(query.get("signals", []) or []),
        "query_refs=" + " ".join(query.get("source_refs", []) or []),
        "CANDIDATE_GUARANTEES:",
    ]
    for match in item.get("matches", []):
        lines.extend(
            [
                (
                    f"- rank={match.get('rank')} score={match.get('score', 0):.4f} "
                    f"pair_type={match.get('pair_type')} shared={match.get('shared_signals', [])}"
                ),
                f"  guarantee={match.get('text', '')}",
                "  guarantee_signals=" + " ".join(match.get("signals", []) or []),
                "  guarantee_refs=" + " ".join(match.get("source_refs", []) or []),
            ]
        )
    return "\n".join(lines)


def build_optimized_units(module: str) -> dict[str, list[dict[str, Any]]]:
    data = json.loads((HERE / f"out/optimized_pairs_{module}.json").read_text(encoding="utf-8"))
    paired = []
    unmatched_uncertain = []
    for item in data["results"]:
        query = item["query"]
        matches = item.get("matches", [])
        if matches:
            best_score = max(m.get("score", 0.0) for m in matches)
            paired.append(
                {
                    "unit_id": f"optimized_paired::{query['atom_id']}",
                    "kind": "optimized_paired",
                    "query_atom_id": query["atom_id"],
                    "query_kind": query["kind"],
                    "best_score": best_score,
                    "pair_count": len(matches),
                    "text": render_optimized_unit(item),
                    "matches": matches,
                }
            )
        elif query["kind"] == "uncertain":
            text = "\n".join(
                [
                    f"OPTIMIZED_UNMATCHED_UNCERTAIN {query['atom_id']}",
                    "uncertain=" + query.get("text", ""),
                    "signals=" + " ".join(query.get("signals", []) or []),
                    "source_refs=" + " ".join(query.get("source_refs", []) or []),
                ]
            )
            unmatched_uncertain.append(
                {
                    "unit_id": f"optimized_unmatched_uncertain::{query['atom_id']}",
                    "kind": "optimized_unmatched_uncertain",
                    "query_atom_id": query["atom_id"],
                    "query_kind": "uncertain",
                    "best_score": None,
                    "pair_count": 0,
                    "text": text,
                    "matches": [],
                }
            )

    paired.sort(key=lambda u: (-(u["best_score"] or 0.0), u["unit_id"]))
    for rank, unit in enumerate(paired, start=1):
        unit["rank"] = rank
    start = len(paired) + 1
    unmatched_uncertain.sort(key=lambda u: u["unit_id"])
    for offset, unit in enumerate(unmatched_uncertain, start=0):
        unit["rank"] = start + offset

    return {
        "optimized_paired_only": paired,
        "optimized_with_unmatched_uncertain": paired + unmatched_uncertain,
        "optimized_unmatched_uncertain_only": unmatched_uncertain,
    }


def observability(atoms: list[dict[str, Any]], subcase: Subcase) -> dict[str, Any]:
    atom_hits = []
    for atom in atoms:
        text = atom_full_text(atom)
        if subcase_hit(text, subcase):
            atom_hits.append(
                {
                    "atom_id": atom["atom_id"],
                    "kind": atom["kind"],
                    "snippet": concise(text, [t for group in subcase.groups for t in group]),
                }
            )
    corpus_text = "\n".join(atom_full_text(a) for a in atoms)
    return {
        "atom_present": bool(atom_hits),
        "corpus_present": subcase_hit(corpus_text, subcase),
        "atom_hits": atom_hits[:5],
    }


def evaluate_method(units: list[dict[str, Any]], subcase: Subcase) -> dict[str, Any]:
    hits = []
    terms = [t for group in subcase.groups for t in group]
    for unit in units:
        if subcase_hit(unit["text"], subcase):
            hits.append(
                {
                    "unit_id": unit["unit_id"],
                    "kind": unit["kind"],
                    "rank": unit.get("rank"),
                    "best_score": unit.get("best_score"),
                    "pair_count": unit.get("pair_count"),
                    "snippet": concise(unit["text"], terms),
                }
            )
    best = hits[0] if hits else None
    return {
        "hit": bool(hits),
        "hit_count": len(hits),
        "best_rank": best.get("rank") if best else None,
        "best_score": best.get("best_score") if best else None,
        "examples": hits[:3],
    }


def evaluate_module(module: str) -> dict[str, Any]:
    atoms = load_atoms(module)
    method_units = {}
    method_units.update(build_legacy_units(module))
    method_units.update(build_optimized_units(module))

    bugs = [b for b in BUG_PROFILES if b.module == module]
    bug_rows = []
    for bug in bugs:
        sub_rows = []
        for subcase in bug.subcases:
            obs = observability(atoms, subcase)
            method_results = {
                method: evaluate_method(units, subcase)
                for method, units in method_units.items()
            }
            sub_rows.append(
                {
                    "subcase_id": subcase.id,
                    "description": subcase.description,
                    "note": subcase.note,
                    "observability": obs,
                    "methods": method_results,
                }
            )

        bug_rows.append(
            {
                "bug_id": bug.id,
                "name": bug.name,
                "description": bug.description,
                "note": bug.note,
                "subcases": sub_rows,
            }
        )

    metrics = summarize_metrics(bug_rows, method_units)
    return {
        "module": module,
        "method_unit_counts": {k: len(v) for k, v in method_units.items()},
        "metrics": metrics,
        "bugs": bug_rows,
    }


def summarize_metrics(bug_rows: list[dict[str, Any]], method_units: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    methods = list(method_units)
    out: dict[str, Any] = {}
    for method in methods:
        observable_subcases = 0
        subcase_hits = 0
        observable_bugs = 0
        bug_any_hits = 0
        bug_full_hits = 0
        not_observable_bugs = 0
        for bug in bug_rows:
            observable = [
                sub
                for sub in bug["subcases"]
                if sub["observability"]["atom_present"] or sub["observability"]["corpus_present"]
            ]
            if not observable:
                not_observable_bugs += 1
                continue
            observable_bugs += 1
            hits = [sub["methods"][method]["hit"] for sub in observable]
            observable_subcases += len(observable)
            subcase_hits += sum(1 for h in hits if h)
            if any(hits):
                bug_any_hits += 1
            if all(hits):
                bug_full_hits += 1
        out[method] = {
            "units": len(method_units[method]),
            "observable_subcases": observable_subcases,
            "subcase_hits": subcase_hits,
            "subcase_recall": subcase_hits / observable_subcases if observable_subcases else None,
            "observable_bugs": observable_bugs,
            "bug_any_hits": bug_any_hits,
            "bug_any_recall": bug_any_hits / observable_bugs if observable_bugs else None,
            "bug_full_hits": bug_full_hits,
            "bug_full_recall": bug_full_hits / observable_bugs if observable_bugs else None,
            "not_observable_bugs": not_observable_bugs,
        }
    return out


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{100 * value:.1f}%"


def write_report(result: dict[str, Any], out: Path) -> None:
    lines = [
        "# Bug AG Coverage Quality Evaluation",
        "",
        "This report compares legacy signal-based AG work items against the semantic BGE-M3 pairing experiment.",
        "A hit means one LLM work item contains all keyword groups for a known-bug subcase.",
        "Unobservable subcases are listed but excluded from recall denominators.",
        "",
        "## Summary",
        "",
        "| Module | Method | Units | Subcase recall | Bug any-hit recall | Bug full-hit recall | Not observable bugs |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for module_row in result["modules"]:
        for method, metric in module_row["metrics"].items():
            lines.append(
                "| "
                + " | ".join(
                    [
                        module_row["module"],
                        method,
                        str(metric["units"]),
                        f"{metric['subcase_hits']}/{metric['observable_subcases']} ({fmt_pct(metric['subcase_recall'])})",
                        f"{metric['bug_any_hits']}/{metric['observable_bugs']} ({fmt_pct(metric['bug_any_recall'])})",
                        f"{metric['bug_full_hits']}/{metric['observable_bugs']} ({fmt_pct(metric['bug_full_recall'])})",
                        str(metric["not_observable_bugs"]),
                    ]
                )
                + " |"
            )

    lines.extend(["", "## Per-Bug Details", ""])
    methods_to_show = [
        "legacy_ag_only",
        "legacy_signal_plus_uncertain",
        "optimized_paired_only",
        "optimized_with_unmatched_uncertain",
    ]
    for module_row in result["modules"]:
        lines.extend([f"### {module_row['module']}", ""])
        for bug in module_row["bugs"]:
            lines.append(f"#### {bug['bug_id']} {bug['name']}")
            lines.append(bug["description"])
            if bug.get("note"):
                lines.append(f"Note: {bug['note']}")
            lines.append("")
            lines.append("| Subcase | Observable | " + " | ".join(methods_to_show) + " |")
            lines.append("|---|---:|" + "|".join(["---:" for _ in methods_to_show]) + "|")
            for sub in bug["subcases"]:
                obs = sub["observability"]
                obs_label = "atom" if obs["atom_present"] else ("corpus" if obs["corpus_present"] else "no")
                cells = []
                for method in methods_to_show:
                    m = sub["methods"][method]
                    if not m["hit"]:
                        cells.append("miss")
                    else:
                        score = m["best_score"]
                        score_s = f", score={score:.3f}" if isinstance(score, (int, float)) else ""
                        cells.append(f"hit x{m['hit_count']} (rank={m['best_rank']}{score_s})")
                lines.append(f"| {sub['subcase_id']} | {obs_label} | " + " | ".join(cells) + " |")
            lines.append("")
            for sub in bug["subcases"]:
                interesting = []
                for method in methods_to_show:
                    examples = sub["methods"][method]["examples"]
                    if examples:
                        interesting.append((method, examples[0]))
                if not interesting and sub["observability"]["atom_hits"]:
                    interesting.append(("spec_atom", sub["observability"]["atom_hits"][0]))
                if interesting:
                    lines.append(f"- `{sub['subcase_id']}` evidence:")
                    for method, ex in interesting[:3]:
                        ident = ex.get("unit_id") or ex.get("atom_id")
                        snippet = ex.get("snippet", "").replace("|", "\\|")
                        lines.append(f"  `{method}` `{ident}`: {snippet}")
            lines.append("")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_plot(result: dict[str, Any], out_dir: Path) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover - optional dependency
        print(f"matplotlib unavailable, skipping plot: {exc}")
        return

    methods = [
        "legacy_ag_only",
        "legacy_signal_plus_uncertain",
        "optimized_paired_only",
        "optimized_with_unmatched_uncertain",
    ]
    labels = {
        "legacy_ag_only": "legacy AG",
        "legacy_signal_plus_uncertain": "legacy AG+U",
        "optimized_paired_only": "semantic paired",
        "optimized_with_unmatched_uncertain": "semantic paired+U",
    }
    modules = result["modules"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), constrained_layout=True)
    width = 0.18
    xs = list(range(len(modules)))
    for mi, method in enumerate(methods):
        vals = [m["metrics"][method]["subcase_recall"] or 0 for m in modules]
        axes[0].bar([x + (mi - 1.5) * width for x in xs], vals, width, label=labels[method])
        counts = [m["metrics"][method]["units"] for m in modules]
        axes[1].bar([x + (mi - 1.5) * width for x in xs], counts, width, label=labels[method])
    axes[0].set_title("Known-Bug Subcase Recall")
    axes[0].set_ylim(0, 1.05)
    axes[0].set_ylabel("recall")
    axes[0].set_xticks(xs, [m["module"] for m in modules])
    axes[1].set_title("LLM Work Items")
    axes[1].set_ylabel("items")
    axes[1].set_xticks(xs, [m["module"] for m in modules])
    axes[1].legend(loc="upper right", fontsize=8)
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / "bug_ag_quality_recall_vs_work.png", dpi=180)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modules", nargs="+", default=["hmac", "aes"], choices=sorted(MODULE_SPECS))
    parser.add_argument("--out", type=Path, default=HERE / "out/bug_ag_coverage_quality.json")
    parser.add_argument("--report", type=Path, default=HERE / "bug_ag_coverage_quality_report.md")
    parser.add_argument("--plot-dir", type=Path, default=HERE / "out/plots")
    args = parser.parse_args()

    modules = [evaluate_module(module) for module in args.modules]
    output = {
        "config": {
            "modules": args.modules,
            "benchmark_source": str(REPO / "BENCHMARKS.md"),
            "matching": "all keyword groups in a subcase must appear in one LLM work item",
            "methods": {
                "legacy_ag_only": "old signal-based behavioral AG edges only",
                "legacy_signal_plus_uncertain": "old signal-based AG edges plus each uncertain point as one standalone work item",
                "optimized_paired_only": "semantic BGE-M3 query units with at least one candidate guarantee",
                "optimized_with_unmatched_uncertain": "semantic paired units plus unmatched uncertain standalone work items",
            },
        },
        "modules": modules,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(output, args.report)
    write_plot(output, args.plot_dir)

    for module in modules:
        print(f"\n{module['module']}")
        for method, metric in module["metrics"].items():
            print(
                f"  {method}: units={metric['units']} "
                f"subcase={metric['subcase_hits']}/{metric['observable_subcases']} "
                f"bug_any={metric['bug_any_hits']}/{metric['observable_bugs']} "
                f"bug_full={metric['bug_full_hits']}/{metric['observable_bugs']}"
            )
    print(f"\nWrote {args.out}")
    print(f"Wrote {args.report}")


if __name__ == "__main__":
    main()
