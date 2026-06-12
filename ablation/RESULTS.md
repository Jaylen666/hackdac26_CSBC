# Ablation & Evaluation Results

## HMAC (4 known bugs + 2 Codex extra discoveries)

| Bug | Description | In Spec | In Ref | CSBC | Codex | Notes |
|---|---|---|---|---|---|---|
| 010 (SHA-512) | Outer-round length: SHA-512 falls to default, uses SHA-384 constant | ✅ | no | yes | exact | Single-file; spec gives formula |
| 009 (wipe/cfg) | cfg_block clears on stop before done, key rewritable mid-operation | ⚠️ | no | yes | exact | Cross-chunk protocol; CSBC anchor-pairing |
| 011 (stale done) | in_process 127-cycle gap before hash_done_event stable | ❌ | no | yes | miss | Cross-chunk temporal; Phase 2 found, Codex misdiagnosed as latch |
| 019 (alert ping) | prim_alert_sender drops ping handshake on skewed pair | ❌ | no | no | — | Shared prim internals; beyond chunk scope |
| 🆕 wipe_secret_we | wipe write-enable mistakenly gated by reg_error | ❌ | no | yes | miss | reggen bug; Phase 3 found, Codex raw-code missed |
| 🆕 err_code c/p | missing invalid_config_atstart in err_code priority case | ❌ | no | yes | exact | uncertain_point → Phase 3; both Codex and LLM found |
| 🆕🆕 extra_hash_stop_msg_freeze | hash_stop does not stop SW message injection or freeze saved message length | — | yes | no | extra | Codex extra discovery; intrinsic (also in refs); FIFO atomicity issue |
| 🆕🆕 extra_hash_stop_unlock_window | hash_stop prematurely unlocks key/config writes before engine is actually idle | — | yes | no | extra | Codex extra discovery; intrinsic; key/config rewrite window |

## AES (2 known CSBC bugs + 2 new discoveries + 1 Codex extra)

| Bug | Description | In Spec | In Ref | CSBC | Codex | Notes |
|---|---|---|---|---|---|---|
| 004 (state clear) | State-clear default branch key-length-dependent | ✅ | no | yes | miss | Cross-chunk coverage gap; Phase 2 found, Codex misdiagnosed as comb loop |
| 005 (key mux) | KEY_FULL_CLEAR/DEC_CLEAR route to key_expand_out | ✅ | no | yes | miss | Spec explicitly describes PRD clearing; Codex found different aspect |
| 🆕 N-001 | key_words_sel rail OR-merge fault fold | ❌ | yes | yes | miss | Fault-injection; intrinsic (also in refs); Codex partially identified |
| 🆕 N-002 | iv_sel rail OR-merge + iv_we un-gated during CTR | ❌ | yes | yes | miss | Needs threat-modeling perspective; outside LLM raw-code reach |
| 🆕🆕 extra_secallowforcingmasks | SecAllowForcingMasks=0 compile-time disable ignored at top level, FORCE_MASKS live | — | no | no | extra | Codex extra discovery; competition-inserted (only in buggy repo) |

## KMAC (3 known bugs)

| Bug | Description | In Official Spec | Full Pipeline | Bare LLM | Claude Agent | Notes |
|---|---|---|---|---|---|
| 017 (constant mask) | Constant all-ones mask instead of fresh PRNG input | ❌ | ⚠️ (U-UP) | — | — | 2 uncertain_points describe the issue precisely; static_mask has 0 driver → Phase 3 only (rank 234) |
| 021 (alert ping-skew) | Shared prim `prim_alert_sender` drops ping handshake | ❌ (prim) | ❌ | — | — | Same root cause as HMAC 019; beyond chunk scope |
| 036 (sparse_fsm suppress) | `sparse_fsm_error_o` suppressed 100 cycles after StTerminalError | ❌ | ❌ | — | — | Single always-block; counter+error in same chunk; no cross-chunk contradiction |
| 🆕 N-005 | `kmac_reduced` share1 unpacker not guarded by `NumShares>1` | ❌ | ✅ Codex Phase 3 | — | — | Latent config hazard (EnMasking=0, default=1); Phase 3 source comparison found |

> KMAC 3 known bugs: 0/3 CSBC-type (017=value error, 021=shared prim, 036=single-block timing). Phase 2 0/3 expected. Phase 3 found 1 latent config issue.

---

## Keymgr (2 original + 1 new + 1 Codex extra)

| Bug | Description | In Spec | In Ref | CSBC | Codex | Notes |
|---|---|---|---|---|---|---|
| 026 (invalid key leak) | StCtrlInvalid bypasses entropy mask, outputs raw key_state_q | ❌ | no | yes | miss | Phase 2 missed; Phase 3 ref comparison found; Codex raw-code missed (attracted by N-003) |
| 031 (data-enable FSM) | Illegal encoding silently redirected, alarm not raised | ❌ | no | no | miss | Single-file FSM; CSBC blind spot (Channel Z needed) |
| 🆕 N-003 | key_state ECC updated but data stale → false ecc_errs | ❌ | no | yes | miss | Phase 2 found signal (rank 2); Phase 3 corrected & confirmed; Codex raw-code found but in wrong context |
| 🆕🆕 extra_flash_seed_validity_shift | Flash-seed validity checks shifted off the stages that consume them | — | yes | no | extra | Codex extra discovery; intrinsic; stage-indexing mismatch in adv_dvalid |

---

## UART (1 original + 1 new + 1 Codex extra)

| Bug | Description | In Official Spec | Full Pipeline | Bare LLM | Claude Agent | Notes |
|---|---|---|---|---|---|
| 033 (lsio_trigger) | `lsio_trigger_o` unconditionally asserted after reset, ignoring watermark | ❌ | ✅ (Phase 3 extra) | — | — | Phase 2 completely invisible (1 driver, 0 consumer); Phase 3 Codex F-EXTRA-0001 independently discovered |
| 🆕 N-004 | Break FSM re-arms without half-bit-time stability check | ❌ | yes | yes | exact | Break detection timing contract; Codex raw-code misidentified as loopback |
| 🆕🆕 extra_timeout_val0_sticky | rx_timeout permanently asserted when TIMEOUT_CTRL.EN=1 and TIMEOUT_CTRL.VAL=0 | — | yes | no | extra | Codex extra discovery; intrinsic; VAL=0 legal config with fatal consequence |

> UART Phase 2 原始产出 0 个 Channel 相关 finding。两个 bug 均由 Phase 3 Codex 在源码审查中独立发现。N-004 为完全新 bug。extra_timeout_val0_sticky 为 Codex 额外发现（真实 bug，CONFIRMED）。

---

## RV_DM (2 known bugs)

| Bug | Description | In Official Spec | Full Pipeline | Cross-Chunk Only | Bare LLM | Claude Agent | Notes |
|---|---|---|---|---|---|---|
| 034 (DMI gate) | Last DMI response dropped when dmi_en falls during completion pulse | ❌ 未规定响应原子性 | ❌ | ❌ | — | 单模块 race |
| 047 (ndmreset) | ndmreset pending stuck after debug revoke mid-sequence | ❌ 未规定授权撤销时的 pending 行为 | ❌ | ❌ | — | FSM deadlock |

## Experiment Config

| | CSBC Pipeline | Bare LLM |
|---|---|---|
| Model | DeepSeek v4-pro | DeepSeek v4-pro |
| Input | 4,630 lines → 68 specs | 62K chars (spec + RTL) |
| LLM calls | 242 | 1 |
| Total tokens | ~751K | ~21K in / 13K out |
| Wall time | ~30 min | ~5 min |
| Output | 33 structured findings | 4 free-text bugs |

## Ablation 2: Cross-Chunk Only (no official spec / Layer 2)

Isolate the value of CSBC's internal cross-chunk comparison by disabling
Layer 2 (official spec alignment).  Only Channels B/C/D run — all
findings come purely from cross-spec consistency, with no reference to
any external design document.

| Bug | In Official Spec | Full Pipeline (B/C/D + L2) | Cross-Chunk Only (B/C/D) | Bare LLM |
|---|---|---|---|---|
| 010 (SHA-512) | ✅ | ✅ | ✅ (C-COV finds coverage gap) | ✅ |
| 009 (wipe/cfg) | ⚠️ 部分 | ✅ (B-AG + D-TMP) | ✅ (B-AG pairing) | ❌ |
| 011 (stale done) | ❌ | ✅ (D-TMP v3) | ✅ (D-TMP anchor-supervised) | ❌ |
| 019 (alert ping) | ❌ | ❌ | ❌ | ❌ |
| 🆕 wipe_secret_we | ❌ | ✅ (Codex) | ❌ | ❌ |

**Finding**: Cross-chunk only (B/C/D) matches the full pipeline on 3/4
known CSBC bugs.  The internal cross-spec consistency check is the
primary detection mechanism.  Layer 2 adds precision (Bug 005 VERDICT
confirmed via spec claim), and Codex Phase 3 catches reggen template
errors (wipe_secret_we) that require source-level inspection.

---

### In Official Spec 说明

- ✅ 明确: 官方 spec 中对该行为有精确描述，可作为 Layer 2 参考。裸 LLM 有机会从 spec 中推断正确行为。
- ⚠️ 部分: spec 提及了相关约束但未给出精确边界。CSBC 管道的跨 chunk 比对可以发现 spec 未覆盖的矛盾。
- ❌: spec 未提及该行为。**CSBC 在此类 bug 上不依赖 spec 参考——通过 Phase 1 的 chunk spec 交叉比对即可检出。**

---

## Key Takeaways

- **CSBC Phase 2 catches cross-chunk bugs; Phase 3 catches the rest.** 5/7 tested CSBC bugs found by Phase 2. Of the 5 completely-invisible-to-Phase-2 bugs (UART 033, Keymgr 026, wipe_secret_we, err_code, N-003), Phase 3 Codex recovered 4 of them. The only truly missed bugs are single-module FSM/race issues (Keymgr 031, RV_DM 034/047, HMAC 019).
- **Codex Phase 3 independently discovered 5 extra bugs beyond the contest set:** HMAC extra_hash_stop_msg_freeze, HMAC extra_hash_stop_unlock_window, AES extra_secallowforcingmasks, Keymgr extra_flash_seed_validity_shift, UART extra_timeout_val0_sticky. 3/5 are intrinsic (also in clean refs), 1/5 confirmed real via manual RTL+spec verification.
- **Bare LLM raw-code test (DeepSeek/GPT-5.4): 4-5/14 exact hits, ~50% ceiling.** Signal-only chunk selection narrows code focus effectively, but LLM without spec guidance misdiagnoses (latch vs delay, comb loop vs stale value) and is attracted to the most prominent code pattern rather than the target bug.
- **Phase 3 rescues CSBC blind spots.** UART 033 (1 driver, 0 consumer), Keymgr 026 (Phase 2 found but mischaracterized), N-003 (Phase 2 found signal, Phase 3 corrected) — all were Phase 2 blind spots that Phase 3 source-level review correctly identified.
- **Bare LLM / Claude Agent only catch single-file, spec-described bugs.** Neither can track multi-chunk signal relationships. Clear CSBC boundary confirmed across HMAC and AES ablation.
