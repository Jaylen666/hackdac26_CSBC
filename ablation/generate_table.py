#!/usr/bin/env python3
"""Generate a styled PNG comparison table using matplotlib."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# ── Data ──────────────────────────────────────────────────────────
# Data format: (IP, Bug, Spec, New, CSBC, FP, BL, CA, Why)
# FP/BL/CA: 1=found, 0=missed, -1=not tested
data = [
    # (IP, Bug, Spec, Ref, New, CSBC, FP, BL, CA, Why)
    # REF="NO"=competition-inserted (only in buggy repo), "YES"=intrinsic (also in clean refs)
    ("HMAC", "010 SHA-512 outer-length",               "YES", "NO",  "No",  "Yes", 1, 1, 1, "SHA-512 falls to default=SHA-384; code differs from refs"),
    ("HMAC", "009 wipe/cfg_block protocol",             "~",   "NO",  "No",  "Yes", 1, 0, 0, "wipe_secret gated by reg_error; refs uses !reg_error"),
    ("HMAC", "011 stale completion 127-cycle",          "NO",  "NO",  "No",  "Yes", 1, 0, 0, "cool_down_ct 127-cycle delay; refs sets hash_done immediately"),
    ("HMAC", "019 alert ping-skew",                     "NO",  "NO",  "No",  "No",  0, 0, 0, "Competition-inserted: prim_diff_decode lacks skew_cnt; refs has counter FSM"),
    ("HMAC", "NEW wipe_secret_we reg_error",            "NO",  "NO",  "YES", "No",  1, 0, 0, "Competition-inserted (reg_error gating); Phase 3 found it"),
    ("HMAC", "NEW err_code copy-paste",                 "NO",  "NO",  "YES", "No",  1, 1, 1, "Competition-inserted (missing invalid_config_atstart); Phase 3 + LLM found"),
    ("AES",  "004 state_mux key-length default",        "YES", "NO",  "No",  "Yes", 1, 0, 0, "default: state_d for AES-128/192; refs always clears"),
    ("AES",  "005 key_mux PRD clearing route",          "YES", "NO",  "No",  "Yes", 1, 1, 1, "KEY_CLEAR routed to key_expand_out; refs uses prd_clearing_key_i"),
    ("AES",  "NEW N-001 rail OR-merge fault fold",      "NO",  "YES", "YES", "No",  1, -1, -1, "Intrinsic: key_words_sel path identical in both repos"),
    ("AES",  "NEW N-002 iv_we un-gated during CTR",     "NO",  "YES", "YES", "No",  1, -1, -1, "Intrinsic: iv_sel/mux_sel_err path identical in both repos"),
    ("KMAC",  "017 constant all-ones mask",             "NO",  "NO",  "No",  "No",  0, -1, -1, "Competition-inserted: static_mask=all-ones replaces LFSR; refs uses msg_mask LFSR"),
    ("KMAC",  "021 alert ping-skew",                    "NO",  "NO",  "No",  "No",  0, -1, -1, "Competition-inserted: same prim_diff_decode issue as HMAC 019"),
    ("KMAC",  "036 sparse_fsm 100-cycle suppress",      "NO",  "NO",  "No",  "No",  0, -1, -1, "Competition-inserted: st_err_ct counter; refs has no suppression"),
    ("KMAC",  "NEW N-005 share unpacker unguarded",     "NO",  "YES", "YES", "No",  1, -1, -1, "Intrinsic: kmac_reduced.sv identical (only comment typo differs)"),
    ("Keymgr","026 invalid_stage key leak",             "NO",  "NO",  "No",  "Yes", 1, -1, -1, "Competition-inserted: key_output_ctrl rewrites refs assign logic"),
    ("Keymgr","031 data-enable FSM redirect",           "NO",  "NO",  "No",  "No",  0, -1, -1, "Competition-inserted: state_d redirect; refs raises fsm_err_o"),
    ("Keymgr","015 key_state ECC stale data",           "NO",  "NO",  "NO", "Yes", 1, -1, -1, "Competition-inserted: ECC update drops key_state_q; refs updates both"),
    ("UART",  "033 lsio_trigger_o unconditional",        "NO",  "NO",  "No",  "No",  1, -1, -1, "Competition-inserted: always 1'b1; refs uses watermark condition"),
    ("UART",  "NEW N-004 break interrupt re-arm",        "NO",  "YES", "YES", "No",  1, -1, -1, "Intrinsic: break FSM identical in both repos (no stability check)"),
    ("RV_DM", "034 DMI gate response dropped",           "NO",  "NO",  "No",  "No",  0, -1, -1, "Competition-inserted: missing resp_pending/lc_hw_debug_clr logic"),
    ("RV_DM", "047 ndmreset pending stuck",              "NO",  "NO",  "No",  "No",  0, -1, -1, "Competition-inserted: missing ndmreset latch/clear logic"),
]


cols = ["IP", "Bug", "In\nSpec", "In\nRef?", "New", "CSBC",
        "Full\nPipeline", "Bare\nLLM", "Claude\nAgent", "Why"]

# ── Build figure ─────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(22, 7.5))
ax.axis("off")

cell_text = []
cell_colors = []
for r in data:
    row = []
    colors = []
    ip, bug, spec, ref, new, csbc, fp, bl, ca, why = r
    row.extend([ip, bug, spec, ref, new, csbc])
    colors.extend(["white"] * 6)
    # Color REF column: YES=green (competition), NO=yellow (intrinsic)
    colors[3] = "#d4edda" if ref == "YES" else "#fff9c4"

    for v in (fp, bl, ca):
        if v == 1: row.append("✓"); colors.append("#d4edda")
        elif v == -1: row.append("—"); colors.append("#e8e8e8")
        else: row.append("✗"); colors.append("#f5f5f5")

    row.append(why)
    colors.append("white")
    cell_text.append(row)
    cell_colors.append(colors)

# Color the Spec column
for i, r in enumerate(data):
    s = r[2]
    if s == "YES": cell_colors[i][2] = "#d4edda"
    elif s == "NO": cell_colors[i][2] = "#f8d7da"
    else: cell_colors[i][2] = "#fff3cd"

# Color the New column
for i, r in enumerate(data):
    cell_colors[i][4] = "#d4edda" if r[4] == "YES" else "#f5f5f5"

# Color the CSBC column
for i, r in enumerate(data):
    cell_colors[i][5] = "#d4edda" if r[5] == "Yes" else "#f5f5f5"

# Header colors
header_colors = ["#2c3e50"] * len(cols)

table = ax.table(
    cellText=cell_text,
    colLabels=cols,
    cellColours=cell_colors,
    colColours=header_colors,
    cellLoc="center",
    loc="center",
)

table.auto_set_font_size(False)
table.set_fontsize(9.5)
for (row, col), cell in table.get_celld().items():
    cell.set_linewidth(0.3)
    cell.set_edgecolor("#cccccc")
    if row == 0:
        cell.set_text_props(color="white", fontweight="bold", fontsize=10)
    cell.set_height(cell.get_height() * 1.3)

# Column widths (approximate)
col_widths = [0.05, 0.14, 0.04, 0.04, 0.04, 0.04, 0.08, 0.06, 0.06, 0.41]
for (row, col), cell in table.get_celld().items():
    cell.set_width(col_widths[col] * 1.0)

# Summary text
summary = (
    "In Ref? NO = competition-inserted (only in buggy repo).  "
    "YES = intrinsic (also in clean refs).  "
    "17/21 bugs are competition-inserted; 4/21 are intrinsic upstream defects.  "
    "CSBC bugs = cross-chunk contradictions detectable by Phase 2.  "
    "5/5 CSBC bugs found by Full Pipeline.  "
    "Phase 3 (Codex) found 3 hidden competition insertions + 3 intrinsic bugs.  "
    "Uncertain-point pipeline recovers err_code (also a hidden insertion)."
)
fig.text(0.05, 0.04, summary, fontsize=9, color="#555555", style="italic")

plt.tight_layout(pad=0.8)
out = "/home/smy/rtl_bug_agent/ablation/outputs/comparison_table.png"
plt.savefig(out, dpi=180, bbox_inches="tight", facecolor="white")
print(f"Saved: {out}")
