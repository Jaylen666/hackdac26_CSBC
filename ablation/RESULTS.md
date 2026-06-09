# Ablation & Evaluation Results

## HMAC (4 known bugs)

| Bug | Description | In Official Spec | Full Pipeline | Bare LLM | Claude Agent | Notes |
|---|---|---|---|---|---|---|
| 010 (SHA-512) | Outer-round length: SHA-512 falls to default, uses SHA-384 constant | ✅ | ✅ C-COV + Layer2 | ✅ | ✅ | 单文件遗漏 |
| 009 (wipe/cfg) | cfg_block clears on stop before done, key rewritable mid-operation | ⚠️ | ✅ B-AG + D-TMP | ❌ | ❌ | 跨 chunk 协议 |
| 011 (stale done) | in_process 127-cycle gap before hash_done_event stable | ❌ | ✅ D-TMP v3 | ❌ | ❌ | 跨 3 个 always 块 |
| 019 (alert ping) | prim_alert_sender drops ping handshake on skewed pair | ❌ | ❌ | ❌ | ❌ | 超出范围 |
| 🆕 wipe_secret_we | wipe write-enable mistakenly gated by reg_error | ❌ | ✅ Codex Phase 3 | ❌ | ❌ | reggen bug |
| 🆕 err_code c/p | missing invalid_config_atstart in err_code priority case | ❌ | ✅ (U-UP) | ✅ | ✅ | uncertain_point → Phase 3 |

## AES (2 known CSBC bugs + 2 new discoveries)

| Bug | Description | In Official Spec | Full Pipeline | Bare LLM | Claude Agent | Notes |
|---|---|---|---|---|---|---|
| 004 (state clear) | State-clear default branch key-length-dependent | ✅ | ✅ Phase 2 + Codex | ✅ C-COV | ❌ | ❌ | cross-chunk coverage gap |
| 005 (key mux) | KEY_FULL_CLEAR/DEC_CLEAR route to key_expand_out | ✅ | ⚠️ Layer 2 VIOLATION | ⚠️ signal (rank 3) | ✅ (1/3) | ✅ | Found by all 3 methods; spec explicitly describes PRD clearing |
| 🆕 N-001 | key_words_sel rail OR-merge fault fold | ❌ | ✅ Codex Phase 3 | ❌ | ❌ | — | |
| 🆕 N-002 | iv_sel rail OR-merge + iv_we un-gated | ❌ | ✅ Codex Phase 3 | ❌ | ❌ | — | |

## KMAC (3 known bugs)

| Bug | Description | In Official Spec | Full Pipeline | Bare LLM | Claude Agent | Notes |
|---|---|---|---|---|---|
| 017 (constant mask) | Constant all-ones mask instead of fresh PRNG input | ❌ | ⚠️ (U-UP) | — | — | 2 uncertain_points describe the issue precisely; static_mask has 0 driver → Phase 3 only (rank 234) |
| 021 (alert ping-skew) | Shared prim `prim_alert_sender` drops ping handshake | ❌ (prim) | ❌ | — | — | Same root cause as HMAC 019; beyond chunk scope |
| 036 (sparse_fsm suppress) | `sparse_fsm_error_o` suppressed 100 cycles after StTerminalError | ❌ | ❌ | — | — | Single always-block; counter+error in same chunk; no cross-chunk contradiction |
| 🆕 N-005 | `kmac_reduced` share1 unpacker not guarded by `NumShares>1` | ❌ | ✅ Codex Phase 3 | — | — | Latent config hazard (EnMasking=0, default=1); Phase 3 source comparison found |

> KMAC 3 known bugs: 0/3 CSBC-type (017=value error, 021=shared prim, 036=single-block timing). Phase 2 0/3 expected. Phase 3 found 1 latent config issue.

---

## Keymgr (2 original + 2 new discoveries)

| Bug | Description | In Official Spec | Full Pipeline | Bare LLM | Claude Agent | Notes |
|---|---|---|---|---|---|---|
| 026 (invalid key leak) | StCtrlInvalid bypasses entropy mask, outputs raw key_state_q | ❌ | ✅ (Phase 3) | — | — | Phase 2 missed; Phase 3 Codex independently discovered via ref comparison |
| 031 (data-enable FSM) | Illegal encoding silently redirected, alarm not raised | ❌ | ❌ | — | — | Single-file FSM behavior; CSBC blind spot (Channel Z needed) |
| 🆕 N-003 | key_state ECC updated but data stale → false ecc_errs | ❌ | ✅ Codex Phase 3 | — | — | F-0002+F-0004; Phase 2 found signal (rank 2), Phase 3 corrected & confirmed |

> Keymgr 消融实验（Bare LLM / Claude Agent）尚未运行。"—" 表示未测试。

### Keymgr 漏检分析

**Bug 031 (data-enable FSM)**: 单模块 FSM 状态编码行为——非法编码被静默重定向而非触发报警。所有相关逻辑在同一个 chunk (`keymgr_data_en_state.sv`, 136 行) 内，无跨 chunk 矛盾可捕获。属于 CSBC 框架边界外（单块内部状态机行为），需要 Channel Z (状态可达性)。

**Bug 026 (最初漏检, Phase 3 补救)**: `StCtrlInvalid` 状态下密钥输出应走熵掩码路径，实际走了 `key_state_q`。Phase 2 的 finding 中提到过 `invalid_stage_sel` 但措辞不精确，判为多驱动冲突。Phase 3 Codex 在源码比对时独立发现了真正的路由错误——**证明 Phase 3 能补救 Phase 2 未精确描述的 CSBC bug**。

**N-003 (新)**: Phase 2 找到信号 (`key_state_ecc_q` rank 2), Phase 3 Codex 发现的非预期行为——ECC 更新时数据未同步回写。

---

## UART (1 original + 1 new discovery)

| Bug | Description | In Official Spec | Full Pipeline | Bare LLM | Claude Agent | Notes |
|---|---|---|---|---|---|
| 033 (lsio_trigger) | `lsio_trigger_o` unconditionally asserted after reset, ignoring watermark | ❌ | ✅ (Phase 3 extra) | — | — | Phase 2 completely invisible (1 driver, 0 consumer); Phase 3 Codex F-EXTRA-0001 independently discovered |
| 🆕 N-004 | Break FSM re-arms without half-bit-time stability check | ❌ | ✅ (Phase 3 extra) | — | — | Codex F-EXTRA-0002; break detection timing contract |

> UART Phase 2 原始产出 0 个 Channel 相关 finding。两个 bug 均由 Phase 3 Codex 在源码审查中独立发现。N-004 为完全新 bug。

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
- **Phase 3 rescues CSBC blind spots.** UART 033 (1 driver, 0 consumer), Keymgr 026 (Phase 2 found but mischaracterized), N-003 (Phase 2 found signal, Phase 3 corrected) — all were Phase 2 blind spots that Phase 3 source-level review correctly identified.
- **Bare LLM / Claude Agent only catch single-file, spec-described bugs.** Neither can track multi-chunk signal relationships. Clear CSBC boundary confirmed across HMAC and AES ablation.
