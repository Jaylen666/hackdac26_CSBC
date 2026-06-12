#!/usr/bin/env python3
"""Generate latest ablation overview chart from bug_comparison_table.csv."""

import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path("/home/smy/rtl_bug_agent/experiments/signal_only_ablation")
CSV_PATH = ROOT / "bug_comparison_table.csv"
OUT_PATH = ROOT / "out" / "llm_raw_code_vs_pipeline.png"


def norm_grade(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("strong"):
        return "strong"
    if value.startswith("weak"):
        return "weak"
    if value.startswith("no spec"):
        return "no spec"
    if value.startswith("no"):
        return "no"
    return value


def color_for(col: str, value: str) -> str:
    v = value.strip().lower()
    if col in {"In_AGU", "In_Spec"}:
        if v == "strong":
            return "#d4edda"
        if v == "weak":
            return "#fff3cd"
        if v in {"no", "no spec"}:
            return "#e9ecef"
        return "white"
    if col == "CSBC":
        if v == "yes":
            return "#d4edda"
        if v in {"no", "miss"}:
            return "#f8d7da"
        return "#e9ecef"
    if col == "Codex_agent":
        if v == "exact":
            return "#d4edda"
        if v == "extra":
            return "#d1ecf1"
        if v == "miss":
            return "#f8d7da"
        return "#e9ecef"
    if col == "codex with chunk":
        if v == "yes":
            return "#d4edda"
        if v == "miss":
            return "#f8d7da"
        return "#e9ecef"
    return "white"


with CSV_PATH.open(encoding="utf-8", newline="") as f:
    rows = list(csv.DictReader(f))

display_cols = [
    ("IP", "IP"),
    ("ID", "Bug\nID"),
    ("Description", "Actual Bug Description"),
    ("In_AGU", "In\nAGU"),
    ("In_Spec", "In\nSpec"),
    ("CSBC", "CSBC"),
    ("Codex_agent", "Codex\nwhole-RTL"),
    ("codex with chunk", "Codex\nwith chunk"),
]

cell_text = []
cell_colors = []
for row in rows:
    display_row = []
    display_row_colors = []
    for key, _label in display_cols:
        value = row[key]
        if key in {"In_AGU", "In_Spec"}:
            value = norm_grade(value)
        display_row.append(value)
        display_row_colors.append(color_for(key, value))
    cell_text.append(display_row)
    cell_colors.append(display_row_colors)

fig, ax = plt.subplots(figsize=(28, 28))
ax.axis("off")

header_colors = ["#2c3e50"] * len(display_cols)
table = ax.table(
    cellText=cell_text,
    colLabels=[label for _key, label in display_cols],
    cellColours=cell_colors,
    colColours=header_colors,
    cellLoc="center",
    loc="center",
)

table.auto_set_font_size(False)
table.set_fontsize(9)

col_widths = [0.05, 0.05, 0.45, 0.07, 0.07, 0.07, 0.10, 0.10]
for (r, c), cell in table.get_celld().items():
    cell.set_linewidth(0.4)
    cell.set_edgecolor("#cccccc")
    if r == 0:
        cell.set_text_props(color="white", fontweight="bold", fontsize=10)
    if c == 2:
        cell.set_text_props(ha="left", va="top", fontsize=8.8)
    cell.set_height(cell.get_height() * 2.0)
    cell.set_width(col_widths[c])

fig.suptitle(
    "Ablation Overview: Official Spec / AGU Coverage vs Codex Baselines vs CSBC",
    fontsize=15,
    fontweight="bold",
    y=0.995,
)

desc = (
    "This chart is generated from bug_comparison_table.csv. "
    "In_AGU and In_Spec are simplified to grade-only labels. "
    "Codex whole-RTL = earlier direct Codex audit over full module RTL + official spec/docs. "
    "Codex with chunk = latest blind chunk-package ablation. "
    "Only exact hits are counted as yes in the chunk baseline."
)
fig.text(0.03, 0.04, desc, fontsize=10, color="#444444")

def count_eq(key: str, target: str) -> int:
    return sum(1 for r in rows if r[key].strip().lower() == target)

stats = (
    f"In_AGU: {count_eq('In_AGU', 'strong')} strong | {count_eq('In_AGU', 'weak')} weak | "
    f"{count_eq('In_AGU', 'no spec')} no spec    "
    f"In_Spec: {count_eq('In_Spec', 'strong')} strong | {count_eq('In_Spec', 'weak')} weak | {count_eq('In_Spec', 'no')} no    "
    f"CSBC: {count_eq('CSBC', 'yes')} yes | {count_eq('CSBC', 'no')} no    "
    f"Codex whole-RTL: {count_eq('Codex_agent', 'exact')} exact | {count_eq('Codex_agent', 'extra')} extra | {count_eq('Codex_agent', 'miss')} miss    "
    f"Codex with chunk: {count_eq('codex with chunk', 'yes')} yes | {count_eq('codex with chunk', 'miss')} miss"
)
fig.text(0.03, 0.015, stats, fontsize=9.5, color="#333333", fontweight="bold")

plt.subplots_adjust(top=0.92, bottom=0.08, left=0.02, right=0.98)
plt.savefig(OUT_PATH, dpi=180, bbox_inches="tight", facecolor="white")
print(f"Saved: {OUT_PATH}")
