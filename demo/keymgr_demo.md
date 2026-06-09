╔══════════════════════════════════════════════════════════════════╗
║  CSBC Demo: Keymgr N-003 — ECC Stale Data Bug Discovery          ║
║  Phase 2 Semantic AG Pipeline (BGE-M3 + LLM)                     ║
╚══════════════════════════════════════════════════════════════════╝
════════════════════════════════════════════════════════════════════════
STEP 1: RTL Chunk Selection
════════════════════════════════════════════════════════════════════════

  6 chunks selected from keymgr_ctrl.sv and keymgr_err.sv:

  C4 Key Output (distractor)               keymgr_ctrl.sv:277-299
    always_comb begin : key_output_ctrl
    // Initialize with default values 
    key_o.valid = op_req;

    // Select CDI based on operation type
    cdi_sel_o = advance_sel ? cdi_cnt : op_cdi_sel_i;

    // Validate stage selection
    invalid_stage_sel_o = ~(stage_sel_o inside {Creator, OwnerInt, Owner});

    // Enhanced key output handling with state-aware masking
    for (int i = 0; i < Shares; i++) begin
      // Subtle bug: During invalid states, expose unmasked key material
      // by skipping the entropy XOR when certain conditions are met
      if (invalid_stage_sel_o && (state_q == StCtrlInvalid)) begin
        key_o.key[i] = key_state_q[cdi_sel_o][i];
      end else begin
        key_o.key[i] = invalid_stage_sel_o ?
                       {EntropyRounds{entropy_i[i]}} :
                       key_state_q[cdi_sel_o][i];
      end
    end
  end

  C1 ECC Encoder ★ BUG HERE                keymgr_ctrl.sv:304-318
    always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      key_state_q <= '0;
      key_state_ecc_q <= {TotalEccWords{prim_secded_pkg::SecdedInv7264ZeroEcc}};
    end else begin
      for (int i = 0; i < CDIs; i++) begin
        for (int j = 0; j < Shares; j++) begin
          for (int k = 0; k < EccWords; k++) begin
            {key_state_ecc_q[i][j][k]} <=
                prim_secded_pkg::prim_secded_inv_72_64_enc(key_state_ecc_words_d[i][j][k]);
          end
        end
      end
    end
  end

  C2 ECC Decoder (consumer)                keymgr_ctrl.sv:321-334
    for (genvar i = 0; i < CDIs; i++) begin : gen_ecc_loop_cdi
    for (genvar j = 0; j < Shares; j++) begin : gen_ecc_loop_shares
      for (genvar k = 0; k < EccWords; k++) begin : gen_ecc_loop_words
        logic [1:0] errs;
        prim_secded_inv_72_64_dec u_dec (
          .data_i({key_state_ecc_q[i][j][k], key_state_q[i][j][k]}),
          .data_o(),
          .syndrome_o(),
          .err_o(errs)
        );
        assign ecc_errs[i][j][k] = |errs;
      end
    end
  end

  C3 Data Path (key_state_d)               keymgr_ctrl.sv:363-418
    always_comb begin
    key_state_d = key_state_q;
    data_valid_o = 1'b0;
    wipe_key_o = 1'b0;

    // if a wipe request arrives, immediately destroy the
    // keys regardless of current state
    unique case (update_sel)
      KeyUpdateRandom: begin
        for (int i = 0; i < CDIs; i++) begin
          for (int j = 0; j < Shares; j++) begin
            // Load each share with the same randomness so we can
            // later simply XOR root key on them
            key_state_d[i][j][cnt[EntropyRndWidth-1:0]] = entropy_i[i];
          end
        end
      end

      KeyUpdateRoot: begin
        if (root_key_valid_q) begin
          for (int i = 0; i < CDIs; i++) begin
            if (KmacEnMasking) begin : gen_two_share_key
              key_state_d[i][0] ^= root_key_i.creator_root_key_share0;
              key_state_d[i][1] ^= root_key_i.creator_root_key_share1;
            end else begin : gen_one_share_key
              key_state_d[i][0] = root_key_i.creator_root_key_share0 ^
                                  root_key_i.creator_root_key_share1;
              key_state_d[i][1] = '0;
            end
          end
        end else begin
          // if root key is not valid, load and invalid value
          for (int i = 0; i < CDIs; i++) begin
              key_state_d[i][0] = '0;
              key_state_d[i][1] = '{default: '1};
          end
        end
      end

      KeyUpdateKmac: begin
        data_valid_o = gen_op;
        key_state_d[cdi_sel_o] = (adv_op || dis_op) ? kmac_data_i : key_state_q[cdi_sel_o];
      end

      KeyUpdateWipe: begin
        wipe_key_o = 1'b1;
        for (int i = 0; i < CDIs; i++) begin
          for (int j = 0; j < Shares; j++) begin
            key_state_d[i][j] = {EntropyRounds{entropy_i[j]}};
          end
        end
      end

      default:;
    endcase // unique case (update_sel)
  end

  C6 Error Wiring                          keymgr_ctrl.sv:832-894
    logic state_change_err;
  assign state_change_err = vld_state_change_q & !adv_op;


  C5 Error Consumer                        keymgr_err.sv:8-75
    `include "prim_assert.sv"

  ┌─────────────────────────────────────────────────────────────┐
  │ ★RIGHT CODE                                                | 
  |   {key_state_ecc_q[i][j][k], key_state_q[i][j][k]} <=       |
  |              enc(key_state_ecc_words_d[i][j][k]);           |
  |                                                             |
  | ★THE BUG (C1, line 312):                                   │
  │   {key_state_ecc_q[i][j][k]} <= enc(key_state_ecc_words_d)  │
  │   Only 8-bit ECC captured — 64-bit DATA DROPPED!            │
  │                                                             │
  │ ★ THE CONSUMER (C2, line 326):                              │
  │   .data_i({key_state_ecc_q, key_state_q})                    │
  │   Decoder expects BOTH ECC and data — but data is stale!    │
  └─────────────────────────────────────────────────────────────┘

════════════════════════════════════════════════════════════════════════
STEP 2: LLM-Generated Structured Specs
════════════════════════════════════════════════════════════════════════

  6 chunks → 10 guarantees + 7 assumptions + 8 uncertain_points

  ┌─ C1 (Encoder) Spec ─────────────────────────────────────┐
  │ Summary: 复位时清除 key_state_q 并将 key_state_ecc_q 初始化为安全 ECC 常数；正常操作时持续将 key_state_ecc_words_
  │ Guarantee: 复位完成后 key_state_q 恒为 0，key_state_ecc_q 恒为 SecdedInv7264ZeroEcc 常数；在非复位状态下，每个时钟沿后 key_state_ecc_q 的值等于上一周期 key_state_ecc_words_d 的 SEC‑DED 编码结果。
  │   output_signals: ['key_state_q', 'key_state_ecc_q']
  │ ⚠ Uncertain: key_state_q 仅在该 always_ff 的复位分支被清零，正常操作分支未对其赋值，因此复位后 key_state_q 永久保持 0；可能由其他组合或时序逻辑驱动，亦或是此处漏写了正常更新逻辑，需结合 keymgr_ctrl 完整代码确认。
  └──────────────────────────────────────────────────────────┘

  ┌─ C2 (Decoder) Spec ─────────────────────────────────────┐
  │ Summary: 为每一份 CDI、共享和字实例化 SECDED 解码器，检测 key_state 存储器的 ECC 错误并生成组合错误指示信号 ecc_errs。
  │ Assumption: key_state_q[i][j][k] 与 key_state_ecc_q[i][j][k] 的位宽之和必须为 72 位，以匹配 prim_secded_inv_72_64_dec 的 data_i 输入位宽。
  │   signals: ['key_state_q', 'key_state_ecc_q', 'data_i']
  │   bug_relevance: 若位宽不匹配，解码器可能产生未定义的错误标志，导致真实的 ECC 错误被掩盖或误报，破坏存储完整性检测，在安全场合可能造成密钥泄露或功能异常。
  │ Assumption: ecc_errs 数组已在外部正确声明，且包含至少 [0:CDIs-1][0:Shares-1][0:EccWords-1] 的有效索引范围。
  │   signals: ['ecc_errs']
  │   bug_relevance: 若索引越界或数组维度不足，assign 语句可能导致编译错误或信号驱动未定义，致使错误检测功能完全失效。
  └──────────────────────────────────────────────────────────┘

════════════════════════════════════════════════════════════════════════
STEP 3: Atom Extraction
════════════════════════════════════════════════════════════════════════

  25 atoms extracted from specs:
    15 queries (assumptions + uncertain_points)
    10 guarantees (candidates)

  Key atoms for N-003:
    [Query]    C2::assumption::0: ECC+data concatenation must be consistent
    [Query]    C1::uncertain::0: key_state_q never updated after reset
    [Candidate] C1::guarantee::0: key_state_ecc_q updated every cycle
    [Candidate] C2::guarantee::0: ecc_errs reflects decoder output

════════════════════════════════════════════════════════════════════════
STEP 4: BGE-M3 Embedding + AG Pair Scoring
════════════════════════════════════════════════════════════════════════

  Model: BAAI/bge-m3
  Weights: dense=0.8 signal=0.2
  Threshold: 0.66
  Max pairs per query: 5

  Rank  Score    Dense    Signal   Relation                  Verdict
  ──── ─────── ─────── ─────── ──────────────────────── ───────
  1     0.738    0.756    0.667    field_overlap             ✅ KEPT
         Q: ${key_state_ecc_q[i][j][k], key_state_q[i][j][k]} 拼接形成的 72 位数据必须与 prim_secded_inv_72_64_de
         C: 复位释放后第一个时钟沿，key_state_q 必定为全 0，key_state_ecc_q 必定为 TotalEccWords 个 SecdedInv7264ZeroEcc 值。
         shared signals: ['key_state_ecc_q', 'key_state_q']
  2     0.687    0.775    0.333    field_overlap             ✅ KEPT
         Q: ${key_state_ecc_q[i][j][k], key_state_q[i][j][k]} 拼接形成的 72 位数据必须与 prim_secded_inv_72_64_de
         C: 非复位时，key_state_ecc_q 每一拍都根据 key_state_ecc_words_d 的当前值更新，且每个分片 i,j,k 均独立完成 ECC 编码。
         shared signals: ['key_state_ecc_q']
  3     0.667    0.683    0.600    normalized_field_overlap  ✅ KEPT
         Q: ${key_state_ecc_q[i][j][k], key_state_q[i][j][k]} 拼接形成的 72 位数据必须与 prim_secded_inv_72_64_de
         C: 当 update_sel 为 KeyUpdateWipe 时，wipe_key_o 必定为 1 且 key_state_d 所有条目均被熵值完全填充
         shared signals: ['key_state_d', 'key_state_q']

  Summary: 150 raw query×guarantee pairs → 3 selected pairs
  Estimated semantic pruning: 147 pairs pruned, LLM work reduced by ~98.0%
  Note: this is an offline subset extracted from the full Keymgr semantic run.

════════════════════════════════════════════════════════════════════════
STEP 5: Distractor Analysis — Why Semantic Pruning Matters
════════════════════════════════════════════════════════════════════════

  ┌─ Distractor A: Same signal name, different semantics
  │ Pair: C2 (ECC decoder) → C4 (key output control)
  │ Shared: Both mention `key_state_q`
  │ C2 assumption: ECC+data concatenation MUST be consistent
  │ C4 guarantee: key_o.valid always equals op_req
  │ Dense score ~0.52 — model recognizes key_output ≠ ECC_integrity
  │ → PRUNED — signals overlap but semantics diverge
  └────────────────────────────────────────────────────────────────────

  ┌─ Distractor B: Normalized overlap only, weak match
  │ Pair: C3 (data path) → C1 (ECC encoder)
  │ Shared: Normalized stems: key_state (but different suffixes: _d vs _ecc_q)
  │ C3 assumption: cnt index must not exceed key_state_d width
  │ C1 guarantee: key_state_ecc_q updated from encoder output
  │ Signal score 0.20 (normalized only), dense 0.61 → combined 0.53
  │ → PRUNED — stems match but semantics unrelated
  └────────────────────────────────────────────────────────────────────

  ┌─ Distractor C: Signal-strong, semantic-weak
  │ Pair: C5 (error consumer) → C2 (ECC decoder)
  │ Shared: Both mention `ecc_errs` — strong signal overlap
  │ C5 assumption: fault_o must not form combinational loop
  │ C2 guarantee: ecc_errs = |errs (purely combinational)
  │ Signal score 0.80 but dense only 0.45 → score 0.52
  │ → PRUNED — signals alone would mislead; dense score saves us
  └────────────────────────────────────────────────────────────────────

════════════════════════════════════════════════════════════════════════
STEP 6: LLM Mismatch Analysis (Channel B)
════════════════════════════════════════════════════════════════════════

  Offline mode: loaded precomputed finding from n003_finding.json

  ── LLM Verdict ──
  Verdict:     GAP
  Scenario:    合法输入场景
  Severity:    HIGH

  Reasoning:
  Decoder-side assumptions require key_state_ecc_q and key_state_q to
  be a consistent SECDED codeword/data pair. The encoder always_ff
  block only updates key_state_ecc_q in normal operation; key_state_q
  is assigned only in reset and is not updated with the 64-bit data
  returned by prim_secded_inv_72_64_enc. Therefore after a valid
  key_state_d update, the decoder can observe new ECC bits with stale
  data bits, producing false ecc_errs and a spurious fatal integrity
  fault.

  Bug Description:
  keymgr_ctrl.sv captures only the ECC slice of
  prim_secded_inv_72_64_enc into key_state_ecc_q. The clean
  implementation captures both {key_state_ecc_q, key_state_q}, keeping
  the stored data and ECC synchronized.

════════════════════════════════════════════════════════════════════════
STEP 7: Buggy vs Clean Comparison
════════════════════════════════════════════════════════════════════════

  ┌─ Buggy (opentitan/hw/ip/keymgr/rtl/keymgr_ctrl.sv:312) ─────────┐
  │                                                                    │
  │   {key_state_ecc_q[i][j][k]} <=                                    │
  │       prim_secded_pkg::prim_secded_inv_72_64_enc(                  │
  │           key_state_ecc_words_d[i][j][k]);                         │
  │                                                                    │
  │   ⚠ ONLY 8-bit ECC captured from 72-bit encoder output!           │
  │   64-bit data PORTION IS DROPPED.                                  │
  │   key_state_q is NEVER updated after reset → stays all-zeros.     │
  └────────────────────────────────────────────────────────────────────┘

  ┌─ Clean (opentitan-refs/hw/ip/keymgr/rtl/keymgr_ctrl.sv:297) ─────┐
  │                                                                    │
  │   {key_state_ecc_q[i][j][k], key_state_q[i][j][k]} <=              │
  │       prim_secded_pkg::prim_secded_inv_72_64_enc(                  │
  │           key_state_ecc_words_d[i][j][k]);                         │
  │                                                                    │
  │   ✅ BOTH ECC (8-bit) AND data (64-bit) updated TOGETHER.         │
  │   Decoder always sees consistent {ECC, data} pairs.               │
  └────────────────────────────────────────────────────────────────────┘

  Impact chain:
    Encoder drops data → key_state_q stale → decoder sees new ECC + old data
    → false ecc_errs → FaultKeyEcc asserted → spurious fatal integrity fault


════════════════════════════════════════════════════════════════════════
Demo complete.
════════════════════════════════════════════════════════════════════════