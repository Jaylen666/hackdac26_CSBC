╔══════════════════════════════════════════════════════════════════╗
║  CSBC Demo: HMAC 010 — SHA-512 Outer-Length Bug Discovery       ║
║  Uncertain Point + Semantic AG Pipeline                         ║
╚══════════════════════════════════════════════════════════════════╝

════════════════════════════════════════════════════════════════════════
STEP 1: RTL Chunk Selection
════════════════════════════════════════════════════════════════════════

  This demo uses existing HMAC outputs and focuses on known bug 010:

    hmac_bug_003_sha512_outer_len
    HMAC-SHA512 outer-round message length falls back to SHA-384 length.

  The key signal chain is:

    CFG.digest_size
      → digest_size_supplied
      → digest_size / digest_size_i
      → assign_sha_message_length
      → sha_msg_len
      → sha_message_length_o
      → SHA padding / final HMAC digest

  Selected chunks:

  C1 Digest Size Cast                         hmac.sv:297-309
    always_comb begin : cast_digest_size
      digest_size = SHA2_None;

      unique case (digest_size_supplied)
        SHA2_256:  digest_size = SHA2_256;
        SHA2_384:  digest_size = SHA2_384;
        SHA2_512:  digest_size = SHA2_512;
        default:   digest_size = SHA2_None;
      endcase
    end

    Role:
      This chunk proves SHA2_512 is a legal, supported digest mode.

  C2 Message Length Computation ★ BUG HERE    hmac_core.sv:207-232
    always_comb begin : assign_sha_message_length
      sha_msg_len = 64'd0;

      if (!hmac_en_i) begin
        sha_msg_len = message_length_i;

      end else if (sel_msglen == SelIPadMsg) begin
        unique case (digest_size_i)
          SHA2_256: sha_msg_len = message_length_i + BlockSizeSHA256in64;
          SHA2_384,
          SHA2_512: sha_msg_len = message_length_i + BlockSizeSHA512in64;
          default:  sha_msg_len = 64'd0;
        endcase

      end else begin
        unique case (digest_size_i)
          SHA2_256: sha_msg_len = BlockSizeSHA256in64 + 64'd256;
          SHA2_384: sha_msg_len = BlockSizeSHA512in64 + 64'd384;
          default:  sha_msg_len = BlockSizeSHA512in64 + 64'd384;
        endcase
      end
    end

    Role:
      This chunk computes the length passed to SHA for the inner and outer HMAC rounds.
      The final outer path handles SHA2_256 and SHA2_384 explicitly, but not SHA2_512.

  C3 Block Boundary Check                      hmac_core.sv:236-244
    unique case (digest_size_i)
      SHA2_256: txcnt_eq_blksz = ... BlockSizeBitsSHA256 ...;
      SHA2_384: txcnt_eq_blksz = ... BlockSizeBitsSHA512 ...;
      SHA2_512: txcnt_eq_blksz = ... BlockSizeBitsSHA512 ...;
      default;
    endcase

    Role:
      This neighboring chunk confirms SHA2_512 is handled explicitly elsewhere.

  C4 Digest Push Completion                    hmac_core.sv:413-415
    if (fifo_wready_i && (((fifo_wdata_sel_o == 4'd7)  && (digest_size_i == SHA2_256)) ||
                          ((fifo_wdata_sel_o == 4'd15) && (digest_size_i == SHA2_512)) ||
                          ((fifo_wdata_sel_o == 4'd11) && (digest_size_i == SHA2_384)))) begin

    Role:
      This state-machine condition also treats SHA2_512 as a distinct 512-bit digest.

  C5 Register Spec                             hmac/doc/registers.md:211-219
    CFG.digest_size supports:
      SHA2_256 = 0x1
      SHA2_384 = 0x2
      SHA2_512 = 0x4
      SHA2_None = 0x8

    Role:
      The public register spec confirms SHA2_512 is not an illegal fallback mode.

  ┌─────────────────────────────────────────────────────────────┐
  │ ★ THE BUG (C2, final outer HMAC path):                      │
  │   SHA2_512 has no explicit branch.                          │
  │   It falls into default: BlockSizeSHA512in64 + 384.          │
  │                                                             │
  │ ★ THE EXPECTED SHA512 BEHAVIOR:                             │
  │   Outer HMAC input length should be block size + 512 bits.  │
  │   Clean refs use BlockSizeSHA512in64 + 512.                 │
  │                                                             │
  │ ★ IMPACT:                                                   │
  │   HMAC-SHA512 final outer hash uses a length 128 bits short.│
  └─────────────────────────────────────────────────────────────┘

════════════════════════════════════════════════════════════════════════
STEP 2: LLM-Generated Structured Specs
════════════════════════════════════════════════════════════════════════

  The relevant spec was already generated in:

    output/specs/hmac_core__always_comb__assign_sha_message_length__001.json

  Key spec outputs:

  ┌─ C2 assign_sha_message_length Spec ─────────────────────────┐
  │ Summary:
  │   根据 HMAC 使能状态、消息长度选择阶段和摘要算法类型，
  │   组合计算送往 SHA 的消息比特长度。
  │
  │ Guarantee 0:
  │   当 hmac_en_i 为 0 时，sha_msg_len 一定等于 message_length_i。
  │
  │ Guarantee 1:
  │   当 hmac_en_i 为 1 且 sel_msglen == SelIPadMsg 时，
  │   sha_msg_len = message_length_i + block size。
  │   SHA2_256 使用 BlockSizeSHA256in64；
  │   SHA2_384/SHA2_512 使用 BlockSizeSHA512in64。
  │
  │ Guarantee 2:
  │   当 hmac_en_i 为 1 且 sel_msglen != SelIPadMsg 时，
  │   sha_msg_len 为固定外层 HMAC 输入长度：
  │     SHA2_256 → BlockSizeSHA256in64 + 256
  │     SHA2_384 → BlockSizeSHA512in64 + 384
  │     other/default → BlockSizeSHA512in64 + 384
  │
  │ Assumption:
  │   在 HMAC 最终哈希路径中，digest_size_i 应只取该实现支持的合法编码，
  │   并且若期望 SHA2_512 语义，需要确认其不会依赖 default 分支的
  │   384 比特长度回退。
  │
  │ ⚠ Uncertain Point 0:
  │   最终哈希长度计算中，SHA2_512 没有显式分支；
  │   default 返回 BlockSizeSHA512in64 + 384 [hmac_core.sv:226-229]。
  │   这是否有意将 SHA2_512 与 SHA2_384 共享 384 比特中间摘要长度，
  │   还是遗漏了 512 比特分支，需要结合模块其余上下文确认。
  └──────────────────────────────────────────────────────────────┘

  ┌─ C1 cast_digest_size Spec ──────────────────────────────────┐
  │ Guarantee:
  │   当 digest_size_supplied 为 SHA2_256、SHA2_384 或 SHA2_512 时，
  │   digest_size 一定分别输出对应的合法枚举值。
  │
  │ Why it matters:
  │   This guarantee tells the downstream HMAC core that SHA2_512 is a
  │   legal, supported mode, not an illegal value that should fall into
  │   defensive default behavior.
  └──────────────────────────────────────────────────────────────┘

  Important point:
    This bug was first made visible by the uncertain point.
    The AG pair then provides context proving SHA2_512 is a legal mode.

════════════════════════════════════════════════════════════════════════
STEP 3: Atom Extraction
════════════════════════════════════════════════════════════════════════

  Existing HMAC atom inventory:

    68 spec files
    84 assumption atoms
    149 guarantee atoms
    163 uncertain atoms
    396 total atoms

  Key atoms for HMAC 010:

    [Uncertain]
      hmac_core__always_comb__assign_sha_message_length__001::uncertain::0

      最终哈希长度计算中，SHA2_512 没有显式分支；
      default 返回 BlockSizeSHA512in64 + 384。
      这是否有意将 SHA2_512 与 SHA2_384 共享 384 比特中间摘要长度，
      还是遗漏了 512 比特分支？

    [Assumption]
      hmac_core__always_comb__assign_sha_message_length__001::assumption::0

      在 HMAC 最终哈希路径中，digest_size_i 应只取该实现支持的合法编码，
      并且若期望 SHA2_512 语义，需要确认其不会依赖 default 分支的
      384 比特长度回退。

    [Guarantee]
      hmac__always_comb__cast_digest_size__001::guarantee::0

      当 digest_size_supplied 为 SHA2_256、SHA2_384 或 SHA2_512 时，
      digest_size 一定分别输出对应的合法枚举值。

    [Guarantee]
      hmac_core__continuous_region__batched_small_assigns__001::guarantee::0

      sha_message_length_o 始终与内部 sha_msg_len 完全一致。

════════════════════════════════════════════════════════════════════════
STEP 4: Semantic AG Pairing + Uncertain Routing
════════════════════════════════════════════════════════════════════════

  Existing HMAC semantic pairing results:

    legacy AG only:                  265 work items
    legacy AG + uncertain:           428 work items
    semantic paired only:            135 query units
    semantic paired + unmatched U:   232 work items

  For HMAC 010:

    legacy_ag_only hit:              yes, best rank 147
    legacy_uncertain_only hit:       yes, rank 93
    semantic_paired_only hit:        yes, best rank 5
    semantic_with_unmatched_U hit:   yes, best rank 5
    unmatched uncertain hit:         yes, rank 185

  The most useful semantic AG pair:

  ┌─ Pair A: Legal SHA2_512 mode context ───────────────────────┐
  │ Query:
  │   hmac_core__always_comb__assign_sha_message_length__001::assumption::0
  │
  │   In the final HMAC hash path, digest_size_i should be a supported
  │   legal mode, and if SHA2_512 is intended it must not rely on the
  │   default 384-bit fallback.
  │
  │ Candidate:
  │   hmac__always_comb__cast_digest_size__001::guarantee::0
  │
  │   digest_size_supplied = SHA2_512 produces legal digest_size = SHA2_512.
  │
  │ Score:
  │   dense = 0.856
  │   signal = 0.600
  │   total = 0.805
  │   shared = digest_size / digest_size_i
  │
  │ Interpretation:
  │   This pair proves that SHA2_512 is a legitimate input scenario.
  │   Therefore the default +384 behavior in C2 is not a safe illegal-input
  │   fallback; it affects a supported configuration.
  └──────────────────────────────────────────────────────────────┘

  The critical unmatched uncertain:

  ┌─ U-UP: Direct bug clue ─────────────────────────────────────┐
  │ Atom:
  │   hmac_core__always_comb__assign_sha_message_length__001::uncertain::0
  │
  │ Text:
  │   SHA2_512 has no explicit final-length branch.
  │   default returns BlockSizeSHA512in64 + 384.
  │
  │ Why it matters:
  │   This U point directly asks the right question:
  │   Is SHA2_512 intentionally using SHA2_384's digest length,
  │   or is the 512-bit branch missing?
  │
  │ Outcome:
  │   Phase3 confirms it is missing.
  └──────────────────────────────────────────────────────────────┘

  Demo message:
    This example is not a pure AG-pair discovery.
    The decisive clue is an uncertain point; semantic AG supplies the
    legality context and pushes the right evidence to Phase3.

════════════════════════════════════════════════════════════════════════
STEP 5: Phase3 Verification Result
════════════════════════════════════════════════════════════════════════

  Existing reviewed findings:

    F-0025  CONFIRMED  confidence=0.99
    F-0029  CONFIRMED  confidence=0.99
    F-0032  CONFIRMED  confidence=0.99

  Representative verdict: F-0025

  ┌─ LLM Verdict ────────────────────────────────────────────────┐
  │ Verdict:     CONFIRMED
  │ Severity:    HIGH
  │
  │ Reasoning:
  │   SHA2_512 is an explicitly supported digest mode, but the final
  │   outer HMAC length computation does not give it a separate branch.
  │   In the path hmac_en_i=1 and sel_msglen!=SelIPadMsg, the case only
  │   handles SHA2_256 and SHA2_384. SHA2_512 falls into default:
  │
  │     BlockSizeSHA512in64 + 64'd384
  │
  │   The same module handles SHA2_512 explicitly elsewhere, including
  │   intermediate length computation and digest push completion. Thus the
  │   default is not defensive behavior for an illegal mode; it is missing
  │   coverage for a legal mode.
  │
  │ Trigger:
  │   Enable HMAC mode, configure CFG.digest_size = SHA2_512, and execute
  │   the final outer HMAC hash path.
  │
  │ Impact:
  │   sha_message_length_o is 128 bits too short.
  │   HMAC-SHA512 digest becomes incorrect, breaking authentication /
  │   integrity semantics.
  └──────────────────────────────────────────────────────────────┘

════════════════════════════════════════════════════════════════════════
STEP 6: Buggy vs Clean Comparison
════════════════════════════════════════════════════════════════════════

  ┌─ Buggy (opentitan/hw/ip/hmac/rtl/hmac_core.sv:223-230) ─────┐
  │                                                              │
  │   // Handle final hash computation with padded outer key.    │
  │   unique case (digest_size_i)                                │
  │     SHA2_256: sha_msg_len = BlockSizeSHA256in64 + 64'd256;  │
  │     SHA2_384: sha_msg_len = BlockSizeSHA512in64 + 64'd384;  │
  │     default:  sha_msg_len = BlockSizeSHA512in64 + 64'd384;  │
  │   endcase                                                    │
  │                                                              │
  │   ⚠ SHA2_512 has no explicit branch.                         │
  │   ⚠ SHA2_512 falls into SHA2_384 length.                     │
  └──────────────────────────────────────────────────────────────┘

  ┌─ Clean (opentitan-refs/hw/ip/hmac/rtl/hmac_core.sv:225-233) ┐
  │                                                              │
  │   // message length for HASH = block size + inner digest     │
  │   if (digest_size_i == SHA2_256) begin                       │
  │     sha_msg_len = BlockSizeSHA256in64 + 64'd256;             │
  │   end else if (digest_size_i == SHA2_384) begin              │
  │     sha_msg_len = BlockSizeSHA512in64 + 64'd384;             │
  │   end else begin // SHA512                                   │
  │     sha_msg_len = BlockSizeSHA512in64 + 64'd512;             │
  │   end                                                        │
  │                                                              │
  │   ✅ SHA2_512 gets the correct 512-bit inner digest length.  │
  └──────────────────────────────────────────────────────────────┘

  Impact chain:

    CFG.digest_size = SHA2_512
      → digest_size_i = SHA2_512 is legal
      → final outer HMAC path selected
      → buggy RTL uses +384 instead of +512
      → sha_message_length_o is 128 bits short
      → SHA padding/final digest differs from standard HMAC-SHA512
      → software observes incorrect authentication tag

════════════════════════════════════════════════════════════════════════
Takeaway
════════════════════════════════════════════════════════════════════════

  This demo highlights why CSBC must treat uncertain_points as first-class
  analysis objects.

  In this case:

    1. The uncertain point directly identified the suspicious local behavior:
       SHA2_512 missing from final outer-length case.

    2. Semantic AG retrieved the surrounding legality context:
       SHA2_512 is a supported digest_size, not an illegal fallback.

    3. Phase3 verified the RTL against nearby code and refs:
       clean code has the missing +512 branch.

  Therefore HMAC 010 is a good demo for the improved CSBC story:

    "A/G pairing is useful, but U points capture design-intent gaps that
     would otherwise be missed or under-prioritized."

