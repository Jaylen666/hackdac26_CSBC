#!/usr/bin/env python3
"""Plot multi-module legacy vs optimized pairing comparison."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


HERE = Path(__file__).resolve().parent
INP = HERE / "out/multi_module_comparison.json"
OUTDIR = HERE / "out/plots"


def autolabel(ax, bars, fmt="{:.0f}") -> None:
    for bar in bars:
        h = bar.get_height()
        if h == 0:
            continue
        ax.annotate(
            fmt.format(h),
            xy=(bar.get_x() + bar.get_width() / 2, h),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
        )


def grouped_bar(rows, keys, labels, title, ylabel, filename) -> None:
    modules = [r["module"] for r in rows]
    x = np.arange(len(modules))
    width = 0.8 / len(keys)
    fig, ax = plt.subplots(figsize=(11, 5))
    for i, (key, label) in enumerate(zip(keys, labels)):
        vals = [r.get(key, 0) or 0 for r in rows]
        bars = ax.bar(x - 0.4 + width / 2 + i * width, vals, width, label=label)
        autolabel(ax, bars)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_xticks(x)
    ax.set_xticklabels(modules)
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUTDIR / filename, dpi=180)
    plt.close(fig)


def stacked_uncertain(rows) -> None:
    modules = [r["module"] for r in rows]
    normal = np.array([r["optimized_normal_pairs"] for r in rows])
    uncertain_signal = np.array([r["optimized_uncertain_with_signal_pairs"] for r in rows])
    uncertain_dense = np.array([r["optimized_uncertain_dense_fallback_pairs"] for r in rows])
    x = np.arange(len(modules))
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.bar(x, normal, label="assumption/normal")
    ax.bar(x, uncertain_signal, bottom=normal, label="uncertain with signal")
    ax.bar(x, uncertain_dense, bottom=normal + uncertain_signal, label="uncertain dense fallback")
    ax.set_title("Optimized Pair Composition")
    ax.set_ylabel("Selected AG candidates")
    ax.set_xticks(x)
    ax.set_xticklabels(modules)
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUTDIR / "optimized_pair_composition.png", dpi=180)
    plt.close(fig)


def ratios(rows) -> None:
    modules = [r["module"] for r in rows]
    vals = [
        r["optimized_vs_legacy_plus_uncertain_work_ratio"]
        if r["optimized_vs_legacy_plus_uncertain_work_ratio"] is not None else 0
        for r in rows
    ]
    fig, ax = plt.subplots(figsize=(10, 4.5))
    bars = ax.bar(modules, vals, color="#4C78A8")
    autolabel(ax, bars, fmt="{:.2f}")
    ax.axhline(1.0, color="black", linestyle="--", linewidth=1)
    ax.set_title("Optimized Pair Count / Legacy+Uncertain Work Items")
    ax.set_ylabel("ratio")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUTDIR / "optimized_vs_legacy_ratio.png", dpi=180)
    plt.close(fig)


def main() -> None:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    data = json.loads(INP.read_text(encoding="utf-8"))
    rows = data["rows"]

    grouped_bar(
        rows,
        ["legacy_behavioral_edges", "uncertain_atoms", "optimized_selected_pairs"],
        ["legacy AG edges", "uncertain points", "optimized selected pairs"],
        "AG Work Items by Module",
        "count",
        "ag_work_items.png",
    )
    grouped_bar(
        rows,
        ["legacy_plus_uncertain_calls_behavioral", "new_calls_one_query", "new_calls_topic_pack5", "new_calls_fixed_pack5"],
        ["legacy+unc calls", "optimized one-query", "topic-pack calls", "fixed-pack calls"],
        "Estimated LLM Calls by Module",
        "calls",
        "llm_calls.png",
    )
    grouped_bar(
        rows,
        ["new_est_tokens_one_query", "new_est_tokens_topic_pack5", "new_est_tokens_fixed_pack5"],
        ["optimized one-query", "topic-pack", "fixed-pack"],
        "Estimated Prompt Tokens for Optimized Candidates",
        "estimated prompt tokens",
        "prompt_tokens.png",
    )
    stacked_uncertain(rows)
    ratios(rows)
    print(f"Wrote plots to {OUTDIR}")


if __name__ == "__main__":
    main()
