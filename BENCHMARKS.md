# Known Bug Benchmarks

Standard benchmark for evaluating the RTL Bug Agent framework.
Each bug has a short description and "what to look for" in the findings.

---

## aes (3 original + 2 new discoveries)

| ID | Name | Description | Source |
|----|------|-------------|--------|
| 004 | aes_bug_002 | State-clear default branch is key-length-dependent: for AES-128/192, default path preserves residual state instead of wiping. | contest |
| 005 | aes_bug_001 | Key-clear muxes route KEY_FULL_CLEAR and KEY_DEC_CLEAR to key_expand_out instead of pseudo-random clearing source. | contest |
| 003 | aes_bug_003 | Software-visible witness for AES key clearing / state retention. | contest |
| 🆕 N-001 | aes_key_words_sel_fault_fold | Redundant FSM rails for `key_words_sel` are OR-merged; a single-rail fault in one rail can produce another legal (but wrong) encoding, bypassing error detection. `mr_err` fires too late to prevent the current cycle's wrong key-word routing. | Codex Phase 3 |
| 🆕 N-002 | aes_iv_sel_fault_fold | Same OR-merge rail-folding pattern in `iv_sel`; additionally, `iv_we` during CTR update is NOT gated by `mux_sel_err`, so a faulted IV selector writes the wrong source into `iv_reg` before the error is latched. | Codex Phase 3 |

## ascon (1 bug)

| ID | Name | Description |
|----|------|-------------|
| 006 | ascon_bug_001 | ascon_core reports NO_NONCE yet computes start_ok, launches the duplex core from an all-zero nonce state, and progresses into output-valid msg/tag paths. |

## csrng (1 bug)

| ID | Name | Description |
|----|------|-------------|
| 023 | csrng_bug_001 | CSRNG bug. |

## dma (2 bugs)

| ID | Name | Description |
|----|------|-------------|
| 007 | dma_bug_001 | Injected fault moves DMA FSM from DmaError to DmaIdle; block resumes post-error progress and issues new system read while error status remains asserted. |
| 032 | dma_bug_004 | Same-cycle interrupt-clear response leaves clear_index unadvanced, preventing the second configured interrupt-clear write from being issued. |

## entropy_src (1 bug)

| ID | Name | Description |
|----|------|-------------|
| 012 | entropy_src_bug_001 | Software-visible Markov fail-accounting witness. |

## hmac (4 bugs)

| ID | Name | Description | Detection Signal |
|----|------|-------------|------------------|
| 009 | hmac_bug_002 | After legal `dif_hmac_wipe_secret()`, a second HMAC run with key=NULL still reproduces the original keyed digest — wipe did not clear secret state. | `wipe_secret`, `secret_key_d`, `secret_key` |
| 010 | hmac_bug_003 | SHA-512 outer-round message length collapses to SHA-384 constant (1408 instead of 1536). Passes SHA-384 but produces full SHA-512 digest mismatch. | `sha_msg_len`, `digest_size_i`, `SHA2_512` |
| 011 | hmac_bug_004 | Stale completion: `hmac_idle` opens before stale completion signal; software restores next stream and reads stale digest values from previous stream. | `hash_done_event`, `in_process`, `done_state`, `cool_down_ct` |
| 019 | hmac_bug_005 | Alert ping-skew: HMAC alert sender responds to clean ping but produces no handshake when differential pair is skewed by two cycles. Root cause in shared `prim_alert_sender`. | `alerts`, `alert_tx_o`, `alert_req_i` |

## i2c (1 bug)

| ID | Name | Description |
|----|------|-------------|
| 020 | i2c_bug_001 | I2C bug. |

## keymgr (2 original + 2 new discoveries)

| ID | Name | Description | Source |
|----|------|-------------|--------|
| 026 | keymgr_bug_001 | Invalid-stage output exposes raw `key_state_q` instead of entropy-only masking value. Phase 2 missed; Phase 3 Codex independently discovered (F-EXTRA-0001). | contest |
| 031 | KEYMGR-TRIAGE-004 | Illegal encoding injected into keymgr data-enable FSM is silently redirected instead of raising the integrity alarm. | contest |
| 🆕 N-003 | keymgr_ecc_stale | `key_state_ecc_q` updates omit `key_state_q` data write-back; decoder sees new ECC + stale data → false `ecc_errs` → spurious fatal integrity handling. | Codex Phase 3 |

## kmac (3 original + 1 new)

| ID | Name | Description | Source |
|----|------|-------------|--------|
| 017 | kmac_bug_001 | Constant all-ones masking contribution instead of fresh mask input. 2 uncertain_points describe the issue; `static_mask` has 0 driver → Phase 3 only. | contest |
| 021 | kmac_bug_003 | Shared prim alert ping-skew (same root cause as HMAC 019). Beyond KMAC chunk scope. | contest |
| 036 | KMAC-BUG-002 | `kmac_core` suppresses `sparse_fsm_error_o` for 100 cycles after `StTerminalError`. Single always-block; no cross-chunk contradiction. | contest |
| 🆕 N-005 | kmac_reduced_share_unpacker | `kmac_reduced` share1 message unpacker unconditionally instantiated; accesses `msg_i[1]` out of bounds when `EnMasking=0`. Latent config hazard (default EnMasking=1). | Codex Phase 3 |

## otbn (1 bug)

| ID | Name | Description |
|----|------|-------------|
| 018 | otbn_bug_001 | BusyExecute illegal OTBN DMEM read returns valid leaked word before fault is observed; leaked word can be captured and forwarded by DMA. |

## otp_macro (1 bug)

| ID | Name | Description |
|----|------|-------------|
| 027 | otp_macro_bug_002 | OTP macro bug (Zeroize command does not actually wipe — original word still readable). |

## prim (1 bug)

| ID | Name | Description |
|----|------|-------------|
| 008 | prim_bug_002 | Recovered differential transition dropped by `prim_diff_decode` after SigInt; `prim_alert_sender` misses pending ping event and never starts expected handshake. |

## rom_ctrl (1 bug)

| ID | Name | Description |
|----|------|-------------|
| 002 | rom_ctrl_bug_001 | Competition bus `rvalid` contract bug. |

## rv_dm (4 bugs)

| ID | Name | Description |
|----|------|-------------|
| 022 | rv_dm_bug_004 | RV_DM bug. |
| 034 | RV_DM-TRIAGE-003 | `rv_dm_dmi_gate` suppresses last pending DMI response when `dmi_en` drops during the only completion pulse, leaving host-side transaction outstanding. |
| 046 | rv_dm_bug_005 | RV debug strap sample leaves always-on pinmux holding stale RV debug authorization after live lifecycle debug permission returns to Off. |
| 047 | rv_dm_bug_002 | Late debug enable via `regs_tl` path, DMI `ndmreset`, and `rv_dm` pending-halt integration logic bug. |

## soc_dbg_ctrl (2 bugs)

| ID | Name | Description |
|----|------|-------------|
| 024 | soc_dbg_ctrl_alert_skew | Alert skew candidate in shared debug control. |
| 045 | soc_dbg_ctrl_bug_001 | Legal shadowed CSR write publishes unlocked production debug policy while lifecycle qualifiers remain Off. |

## sram_ctrl (1 bug)

| ID | Name | Description |
|----|------|-------------|
| 037 | SRAM_CTRL-BUG-001 | Module misroutes correctable ECC classification bit; module boundary error-record contract for `sram_rerror_o.correctable` is violated. |

## tlul (2 bugs)

| ID | Name | Description |
|----|------|-------------|
| 028 | tlul_bug_001 | Merged TL-UL request-word fault corrupts live UART CTRL access while keeping request valid enough to be consumed by downstream CSR path. |
| 029 | tlul_bug_002 | AccessLatency=1 adapter mutates buffered TL-UL read payload to all-ones after response phase while keeping `d_error` clear; downstream consumer accepts corrupted payload. |

## uart (1 original + 1 new)

| ID | Name | Description | Source |
|----|------|-------------|--------|
| 033 | uart_bug_002 | `lsio_trigger_o` unconditionally asserted after reset, ignoring watermark. Phase 2 missed; Phase 3 Codex independently discovered (F-EXTRA-0001). | contest |
| 🆕 N-004 | uart_break_interrupt | Break FSM re-arms on single `rx_in` high without half-bit-time stability check; violates documented rate/arming contract. | Codex Phase 3 |

## usbdev (1 bug)

| ID | Name | Description |
|----|------|-------------|
| 001 | usbdev_bug_001 | USB device software-visible witness. |

---

## Shared Component Bugs (affect multiple IPs)

These bugs originate in shared primitives and impact any IP that instantiates them.

| ID | Name | Description | Affected IPs |
|----|------|-------------|--------------|
| 008 | prim_bug_002 | `prim_diff_decode` drops recovered differential transition after SigInt; alert sender misses ping. | hmac, soc_dbg_ctrl, ... |
| 038 | prim_alert_bug_001 | Clean ping leaves alert path quiescent; skewed ping latches local alert cause and leaves non-zero downstream class-A interrupt. | any IP using `prim_alert_sender` |

---

## Usage

After running the framework on a module, count how many of its known bugs
appear in the top-N findings (by score or by matching description keywords):

```bash
# Example: check HMAC bug coverage
python3 -c "
import json
findings = json.load(open('output/findings_hmac.json'))['findings']
bugs = {
    '009': ['wipe', 'secret_key_d', 'secret_key'],
    '010': ['sha_msg_len', 'SHA2_512', 'BlockSizeSHA512'],
    '011': ['hash_done_event', 'in_process', 'done_state', 'cool_down'],
    '019': ['alerts[0]', 'alert_tx', 'alert_req'],
}
for bid, keywords in bugs.items():
    hits = [f for f in findings if any(k.lower() in json.dumps(f).lower() for k in keywords)]
    print(f'Bug {bid}: {len(hits)} hits')
"
```
