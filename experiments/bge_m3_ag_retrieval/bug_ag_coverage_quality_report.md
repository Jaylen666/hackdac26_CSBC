# Bug AG Coverage Quality Evaluation

This report compares legacy signal-based AG work items against the semantic BGE-M3 pairing experiment.
A hit means one LLM work item contains all keyword groups for a known-bug subcase.
Unobservable subcases are listed but excluded from recall denominators.

## Summary

| Module | Method | Units | Subcase recall | Bug any-hit recall | Bug full-hit recall | Not observable bugs |
|---|---:|---:|---:|---:|---:|---:|
| hmac | legacy_ag_only | 265 | 7/7 (100.0%) | 4/4 (100.0%) | 4/4 (100.0%) | 0 |
| hmac | legacy_signal_plus_uncertain | 428 | 7/7 (100.0%) | 4/4 (100.0%) | 4/4 (100.0%) | 0 |
| hmac | legacy_uncertain_only | 163 | 4/7 (57.1%) | 3/4 (75.0%) | 1/4 (25.0%) | 0 |
| hmac | optimized_paired_only | 135 | 7/7 (100.0%) | 4/4 (100.0%) | 4/4 (100.0%) | 0 |
| hmac | optimized_with_unmatched_uncertain | 232 | 7/7 (100.0%) | 4/4 (100.0%) | 4/4 (100.0%) | 0 |
| hmac | optimized_unmatched_uncertain_only | 97 | 2/7 (28.6%) | 2/4 (50.0%) | 1/4 (25.0%) | 0 |
| aes | legacy_ag_only | 25 | 0/7 (0.0%) | 0/4 (0.0%) | 0/4 (0.0%) | 1 |
| aes | legacy_signal_plus_uncertain | 128 | 5/7 (71.4%) | 4/4 (100.0%) | 2/4 (50.0%) | 1 |
| aes | legacy_uncertain_only | 103 | 5/7 (71.4%) | 4/4 (100.0%) | 2/4 (50.0%) | 1 |
| aes | optimized_paired_only | 37 | 1/7 (14.3%) | 1/4 (25.0%) | 0/4 (0.0%) | 1 |
| aes | optimized_with_unmatched_uncertain | 123 | 6/7 (85.7%) | 4/4 (100.0%) | 3/4 (75.0%) | 1 |
| aes | optimized_unmatched_uncertain_only | 86 | 5/7 (71.4%) | 4/4 (100.0%) | 2/4 (50.0%) | 1 |

## Per-Bug Details

### hmac

#### 009 hmac_bug_002_wipe_secret
Legal wipe_secret does not clear secret state for a later key=NULL HMAC run.

| Subcase | Observable | legacy_ag_only | legacy_signal_plus_uncertain | optimized_paired_only | optimized_with_unmatched_uncertain |
|---|---:|---:|---:|---:|---:|
| wipe_to_secret_key | atom | hit x7 (rank=59) | hit x7 (rank=59) | hit x10 (rank=7, score=0.798) | hit x10 (rank=7, score=0.798) |
| cfg_block_key_update_lifecycle | atom | hit x13 (rank=49) | hit x14 (rank=49) | hit x7 (rank=35, score=0.732) | hit x7 (rank=35, score=0.732) |

- `wipe_to_secret_key` evidence:
  `legacy_ag_only` `legacy_ag::hmac::59`: LEGACY_AG_EDGE 59 signal=secret_key_d consumer_spec=hmac__always_comb__update_secret_key__001 assumption=`cfg_block` 必须准确表示禁止密钥配置的状态；至少在引擎非 Idle 或其他不允许改钥的状态下应为 1。 bug_relevance=这是行为契约。若 `cfg_block` 在不应允许改钥时错误地为 0，则 `qe` 
  `legacy_signal_plus_uncertain` `legacy_ag::hmac::59`: LEGACY_AG_EDGE 59 signal=secret_key_d consumer_spec=hmac__always_comb__update_secret_key__001 assumption=`cfg_block` 必须准确表示禁止密钥配置的状态；至少在引擎非 Idle 或其他不允许改钥的状态下应为 1。 bug_relevance=这是行为契约。若 `cfg_block` 在不应允许改钥时错误地为 0，则 `qe` 
  `optimized_paired_only` `optimized_paired::hmac__always_comb__update_secret_key__001::assumption::1`: OPTIMIZED_QUERY hmac__always_comb__update_secret_key__001::assumption::1 kind=assumption query=软件/寄存器接口在合法写密钥时应避免同一周期既请求正常密钥写入又触发 `wipe_secret`；虽然硬件有安全回退行为，但该场景设计上不应作为常规使用。 bug_relevance: 若不满足则擦除会优先覆盖写入数据，结果是新密钥不会生效而是被整块
- `cfg_block_key_update_lifecycle` evidence:
  `legacy_ag_only` `legacy_ag::hmac::49`: y_inprocess consumer_spec=hmac__always_comb__line_825__001 assumption=reg2hw.key[0:31].qe 必须准确表示对应密钥字寄存器的合法写使能事件；若 qe 在无真实写入时错误拉高，或真实写入时未拉高，则该指示会误报或漏报。 bug_relevance=这是行为契约；若违反，其他依赖 update_seckey_inprocess 的逻辑可能错误地认为密钥更新
  `legacy_signal_plus_uncertain` `legacy_ag::hmac::49`: y_inprocess consumer_spec=hmac__always_comb__line_825__001 assumption=reg2hw.key[0:31].qe 必须准确表示对应密钥字寄存器的合法写使能事件；若 qe 在无真实写入时错误拉高，或真实写入时未拉高，则该指示会误报或漏报。 bug_relevance=这是行为契约；若违反，其他依赖 update_seckey_inprocess 的逻辑可能错误地认为密钥更新
  `optimized_paired_only` `optimized_paired::hmac__always_comb__update_secret_key__001::assumption::0`: OPTIMIZED_QUERY hmac__always_comb__update_secret_key__001::assumption::0 kind=assumption query=`cfg_block` 必须准确表示禁止密钥配置的状态；至少在引擎非 Idle 或其他不允许改钥的状态下应为 1。 bug_relevance: 这是行为契约。若 `cfg_block` 在不应允许改钥时错误地为 0，则 `qe` 写入会修改内部 s

#### 010 hmac_bug_003_sha512_outer_len
SHA-512 HMAC outer-round message length falls back to SHA-384 length.

| Subcase | Observable | legacy_ag_only | legacy_signal_plus_uncertain | optimized_paired_only | optimized_with_unmatched_uncertain |
|---|---:|---:|---:|---:|---:|
| sha512_outer_default_384 | atom | hit x3 (rank=147) | hit x4 (rank=147) | hit x3 (rank=5, score=0.805) | hit x4 (rank=5, score=0.805) |

- `sha512_outer_default_384` evidence:
  `legacy_ag_only` `legacy_ag::hmac::147`: LEGACY_AG_EDGE 147 signal=sha_msg_len consumer_spec=hmac_core__always_comb__assign_sha_message_length__001 assumption=在 HMAC 最终哈希路径中，digest_size_i 应只取该实现支持的合法编码，并且若期望 SHA2_512 语义，需要确认其不会依赖 default 分支的 384 比特长度回退。 bug_rel
  `legacy_signal_plus_uncertain` `legacy_ag::hmac::147`: LEGACY_AG_EDGE 147 signal=sha_msg_len consumer_spec=hmac_core__always_comb__assign_sha_message_length__001 assumption=在 HMAC 最终哈希路径中，digest_size_i 应只取该实现支持的合法编码，并且若期望 SHA2_512 语义，需要确认其不会依赖 default 分支的 384 比特长度回退。 bug_rel
  `optimized_paired_only` `optimized_paired::hmac_core__always_comb__assign_sha_message_length__001::assumption::0`: OPTIMIZED_QUERY hmac_core__always_comb__assign_sha_message_length__001::assumption::0 kind=assumption query=在 HMAC 最终哈希路径中，digest_size_i 应只取该实现支持的合法编码，并且若期望 SHA2_512 语义，需要确认其不会依赖 default 分支的 384 比特长度回退。 bug_relevance: 如果

#### 011 hmac_bug_004_stale_completion
hmac_idle opens before stale completion/digest signals are drained.
Note: This proxy checks whether completion-lifecycle ingredients are visible, not the full stale-digest temporal witness.

| Subcase | Observable | legacy_ag_only | legacy_signal_plus_uncertain | optimized_paired_only | optimized_with_unmatched_uncertain |
|---|---:|---:|---:|---:|---:|
| done_state_hash_done_event | atom | hit x15 (rank=16) | hit x15 (rank=16) | hit x6 (rank=53, score=0.712) | hit x6 (rank=53, score=0.712) |
| in_process_completion_lifecycle | atom | hit x4 (rank=112) | hit x5 (rank=112) | hit x9 (rank=23, score=0.749) | hit x9 (rank=23, score=0.749) |
| cool_down_completion_window | atom | hit x8 (rank=17) | hit x10 (rank=17) | hit x1 (rank=32, score=0.737) | hit x2 (rank=32, score=0.737) |

- `done_state_hash_done_event` evidence:
  `legacy_ag_only` `legacy_ag::hmac::16`: LEGACY_AG_EDGE 16 signal=hash_done_event consumer_spec=hmac__always_comb__line_149__001 assumption=`done_state_q` 应只取该状态机定义的合法编码，尤其应落在 `DoneAwaitCmd`、`DoneAwaitHashDone`、`DoneAwaitMessageComplete`、`DoneAwaitHashComplete`
  `legacy_signal_plus_uncertain` `legacy_ag::hmac::16`: LEGACY_AG_EDGE 16 signal=hash_done_event consumer_spec=hmac__always_comb__line_149__001 assumption=`done_state_q` 应只取该状态机定义的合法编码，尤其应落在 `DoneAwaitCmd`、`DoneAwaitHashDone`、`DoneAwaitMessageComplete`、`DoneAwaitHashComplete`
  `optimized_paired_only` `optimized_paired::hmac__continuous_region__declarations_or_instances__006::uncertain::1`: ions_or_instances__006::uncertain::1 kind=uncertain query=无法从本片段单独确认 `hash_done_event` 是单周期脉冲、保持型信号，还是经过同步/去抖处理后的事件。 query_signals=hash_done_event query_refs=/home/smy/opentitan/hw/ip/hmac/rtl/hmac.sv:429-440 CANDIDATE_G
- `in_process_completion_lifecycle` evidence:
  `legacy_ag_only` `legacy_ag::hmac::112`: LEGACY_AG_EDGE 112 signal=in_process consumer_spec=hmac__always_ff__line_924__001 assumption=`reg_hash_done` 应只在一次合法处理流程结束时产生；若在未开始处理时错误拉高，`in_process` 会被清 0，但该块本身对此有安全回退行为（保持/清空为空闲），属于设计上不应发生的输入场景。 bug_relevance=若不满足则可能
  `legacy_signal_plus_uncertain` `legacy_ag::hmac::112`: LEGACY_AG_EDGE 112 signal=in_process consumer_spec=hmac__always_ff__line_924__001 assumption=`reg_hash_done` 应只在一次合法处理流程结束时产生；若在未开始处理时错误拉高，`in_process` 会被清 0，但该块本身对此有安全回退行为（保持/清空为空闲），属于设计上不应发生的输入场景。 bug_relevance=若不满足则可能
  `optimized_paired_only` `optimized_paired::hmac_core__continuous_region__hmac_core__001::assumption::0`: ac_en_i=1 的合法 HMAC 使用场景下，外部对 reg_hash_start_i/reg_hash_continue_i/reg_hash_process_i 的驱动需与内部 HMAC 状态机语义兼容，尤其 reg_hash_process_i 不应在与内部 hash_process 冲突的时刻被随意拉高。 bug_relevance: 因为 sha_hash_process_o 在 HMAC 模式下是外部 reg_hash_
- `cool_down_completion_window` evidence:
  `legacy_ag_only` `legacy_ag::hmac::17`: 149__001 guarantee=当状态为 `DoneAwaitHashComplete`、`hash_running` 为 0 且 `cool_down_ct_q` 已达到 127 时，`hash_done_event` 必为 1，且下一状态返回 `DoneAwaitCmd`；在计数未满 127 前不会在该路径上宣告完成。 assumption_signals=done_state_q done_state_d hash_done
  `legacy_signal_plus_uncertain` `legacy_ag::hmac::17`: 149__001 guarantee=当状态为 `DoneAwaitHashComplete`、`hash_running` 为 0 且 `cool_down_ct_q` 已达到 127 时，`hash_done_event` 必为 1，且下一状态返回 `DoneAwaitCmd`；在计数未满 127 前不会在该路径上宣告完成。 assumption_signals=done_state_q done_state_d hash_done
  `optimized_paired_only` `optimized_paired::hmac__always_comb__line_149__001::uncertain::0`: 149__001::uncertain::0 kind=uncertain query=代码只在 `DoneAwaitCmd` 中显式将 `cool_down_ct_d` 置 0，在其他状态未给出默认赋值；冷却计数器的完整保持/默认行为可能依赖该 always_comb 块外的赋值，需结合上下文确认 [hmac.sv:155,180-183]。 query_signals=DoneAwaitCmd cool_down_ct_d quer

#### 019 hmac_bug_005_alert_ping_skew
prim_alert_sender ping skew can suppress an expected alert handshake.
Note: The shared primitive root cause is usually not observable from hmac-only AG pairs.

| Subcase | Observable | legacy_ag_only | legacy_signal_plus_uncertain | optimized_paired_only | optimized_with_unmatched_uncertain |
|---|---:|---:|---:|---:|---:|
| hmac_alert_sender_interface | atom | hit x3 (rank=140) | hit x3 (rank=140) | hit x1 (rank=89, score=0.686) | hit x1 (rank=89, score=0.686) |
| ping_skew_root_cause | no | miss | miss | miss | miss |

- `hmac_alert_sender_interface` evidence:
  `legacy_ag_only` `legacy_ag::hmac::140`: LEGACY_AG_EDGE 140 signal=alert_tx_o consumer_spec=hmac__generate_for__gen_alert_tx__001 assumption=设计意图应当允许所有告警发送实例共享同一个内部请求信号 alerts[0]；若系统规范期望每个通道使用独立的 alerts[i]，则这里的连接将导致告警映射错误。 bug_relevance=如果该约束不成立，合法的多通道告警架构会被错误折
  `legacy_signal_plus_uncertain` `legacy_ag::hmac::140`: LEGACY_AG_EDGE 140 signal=alert_tx_o consumer_spec=hmac__generate_for__gen_alert_tx__001 assumption=设计意图应当允许所有告警发送实例共享同一个内部请求信号 alerts[0]；若系统规范期望每个通道使用独立的 alerts[i]，则这里的连接将导致告警映射错误。 bug_relevance=如果该约束不成立，合法的多通道告警架构会被错误折
  `optimized_paired_only` `optimized_paired::hmac__generate_for__gen_alert_tx__001::assumption::0`: _001::assumption::0 kind=assumption query=设计意图应当允许所有告警发送实例共享同一个内部请求信号 alerts[0]；若系统规范期望每个通道使用独立的 alerts[i]，则这里的连接将导致告警映射错误。 bug_relevance: 如果该约束不成立，合法的多通道告警架构会被错误折叠为单一请求源，造成部分告警无法上报、错误通道同时触发，甚至影响 fatal/非fatal 告警分离。 query

### aes

#### 004 aes_bug_002_state_clear_retention
AES-128/192 state-clear default path preserves residual state instead of wiping.

| Subcase | Observable | legacy_ag_only | legacy_signal_plus_uncertain | optimized_paired_only | optimized_with_unmatched_uncertain |
|---|---:|---:|---:|---:|---:|
| state_default_preserve | atom | miss | hit x1 (rank=32) | miss | hit x1 (rank=61) |
| key_length_dependent_clear | atom | miss | miss | miss | miss |

- `state_default_preserve` evidence:
  `legacy_signal_plus_uncertain` `legacy_uncertain::aes::32`: =aes_cipher_core__always_comb__state_mux__001::uncertain::0 uncertain=default 分支中 state_d 保持原值（state_d = state_d）可能推断出组合锁存器，与纯 always_comb 设计意图有潜在冲突，需确认该反馈是否可取。 signals= source_refs=output/aes_mini_rtl/aes_cipher_core.sv
  `optimized_with_unmatched_uncertain` `optimized_unmatched_uncertain::aes_cipher_core__always_comb__state_mux__001::uncertain::0`:  aes_cipher_core__always_comb__state_mux__001::uncertain::0 uncertain=default 分支中 state_d 保持原值（state_d = state_d）可能推断出组合锁存器，与纯 always_comb 设计意图有潜在冲突，需确认该反馈是否可取。 signals= source_refs=output/aes_mini_rtl/aes_cipher_core.sv
- `key_length_dependent_clear` evidence:
  `spec_atom` `aes_cipher_core__always_comb__state_mux__001::guarantee::0`:  state_d，并在未定义选择值时根据密钥长度决定是清除还是保持。 security_implications: default 分支在 key_len_i 为 AES_256 时强制输出 prd_clearing_state_i，可能是为了防止非法状态选择值下敏感状态数据泄露；但若 key_len_i 不为 AES_256，状态将保持原值，有可能保留敏感数据。 source_file: output/aes_mini_rtl/aes

#### 005 aes_bug_001_key_clear_mux
KEY_FULL_CLEAR and KEY_DEC_CLEAR route key_expand_out instead of clearing source.

| Subcase | Observable | legacy_ag_only | legacy_signal_plus_uncertain | optimized_paired_only | optimized_with_unmatched_uncertain |
|---|---:|---:|---:|---:|---:|
| key_full_clear_expand | atom | miss | hit x1 (rank=26) | miss | hit x1 (rank=56) |
| key_dec_clear_expand | atom | miss | hit x1 (rank=25) | miss | hit x1 (rank=55) |

- `key_full_clear_expand` evidence:
  `legacy_signal_plus_uncertain` `legacy_uncertain::aes::26`: ways_comb__key_full_mux__001::uncertain::0 uncertain=KEY_FULL_ROUND 和 KEY_FULL_CLEAR 分支均输出 key_expand_out，两者行为完全相同，未体现'清除'与'正常轮'之间的区别，可能实际区别由后续逻辑控制或该清除状态仅用于标记而不改变数据通路。 signals= source_refs=output/aes_mini_rtl/aes_cipher_
  `optimized_with_unmatched_uncertain` `optimized_unmatched_uncertain::aes_cipher_core__always_comb__key_full_mux__001::uncertain::0`: ways_comb__key_full_mux__001::uncertain::0 uncertain=KEY_FULL_ROUND 和 KEY_FULL_CLEAR 分支均输出 key_expand_out，两者行为完全相同，未体现'清除'与'正常轮'之间的区别，可能实际区别由后续逻辑控制或该清除状态仅用于标记而不改变数据通路。 signals= source_refs=output/aes_mini_rtl/aes_cipher_
- `key_dec_clear_expand` evidence:
  `legacy_signal_plus_uncertain` `legacy_uncertain::aes::25`: es_cipher_core__always_comb__key_dec_mux__001::uncertain::0 uncertain=KEY_DEC_CLEAR 与 KEY_DEC_EXPAND 均选择 key_expand_out，而 KEY_DEC_CLEAR 从命名看可能意为“清除”，通常应使用随机数据 prd_clearing_key_i。当前行为是有意设计（清除状态仍需扩展输出）还是遗漏，无法从片段本身确认。 signa
  `optimized_with_unmatched_uncertain` `optimized_unmatched_uncertain::aes_cipher_core__always_comb__key_dec_mux__001::uncertain::0`: es_cipher_core__always_comb__key_dec_mux__001::uncertain::0 uncertain=KEY_DEC_CLEAR 与 KEY_DEC_EXPAND 均选择 key_expand_out，而 KEY_DEC_CLEAR 从命名看可能意为“清除”，通常应使用随机数据 prd_clearing_key_i。当前行为是有意设计（清除状态仍需扩展输出）还是遗漏，无法从片段本身确认。 signa

#### 003 aes_bug_003_sw_key_clear_state_retention
Software-visible witness for AES key clearing / state retention.
Note: This benchmark entry overlaps the root causes of 004 and 005, so it is treated as a witness-level coverage check.

| Subcase | Observable | legacy_ag_only | legacy_signal_plus_uncertain | optimized_paired_only | optimized_with_unmatched_uncertain |
|---|---:|---:|---:|---:|---:|
| key_clear_or_state_retention_witness | atom | miss | hit x3 (rank=25) | miss | hit x3 (rank=55) |

- `key_clear_or_state_retention_witness` evidence:
  `legacy_signal_plus_uncertain` `legacy_uncertain::aes::25`: es_cipher_core__always_comb__key_dec_mux__001::uncertain::0 uncertain=KEY_DEC_CLEAR 与 KEY_DEC_EXPAND 均选择 key_expand_out，而 KEY_DEC_CLEAR 从命名看可能意为“清除”，通常应使用随机数据 prd_clearing_key_i。当前行为是有意设计（清除状态仍需扩展输出）还是遗漏，无法从片段本身确认。 signa
  `optimized_with_unmatched_uncertain` `optimized_unmatched_uncertain::aes_cipher_core__always_comb__key_dec_mux__001::uncertain::0`: es_cipher_core__always_comb__key_dec_mux__001::uncertain::0 uncertain=KEY_DEC_CLEAR 与 KEY_DEC_EXPAND 均选择 key_expand_out，而 KEY_DEC_CLEAR 从命名看可能意为“清除”，通常应使用随机数据 prd_clearing_key_i。当前行为是有意设计（清除状态仍需扩展输出）还是遗漏，无法从片段本身确认。 signa

#### N-001 aes_key_words_sel_fault_fold
OR-merged redundant rails can fold key_words_sel into a wrong legal selector.

| Subcase | Observable | legacy_ag_only | legacy_signal_plus_uncertain | optimized_paired_only | optimized_with_unmatched_uncertain |
|---|---:|---:|---:|---:|---:|
| or_merge_key_words_selector | atom | miss | miss | hit x1 (rank=12, score=0.727) | hit x1 (rank=12, score=0.727) |
| key_words_zero_encoding | atom | miss | hit x1 (rank=79) | miss | hit x1 (rank=102) |

- `or_merge_key_words_selector` evidence:
  `optimized_paired_only` `optimized_paired::aes_cipher_control__always_comb__combine_sparse_signals__001::assumption::0`: el[i]等）在同一周期内应产生完全相同的值，以保证控制一致。 bug_relevance: 若违反（例如由于设计错误或攻击导致源值不同），mr_err将被置位，组合输出可能变为多个值的按位或，导致下游使用未定义的选择状态，可能引发功能错误或安全违规。 query_signals=mr_state_sel mr_add_rk_sel mr_key_full_sel mr_key_dec_sel mr_key_words_sel mr_r
  `optimized_with_unmatched_uncertain` `optimized_paired::aes_cipher_control__always_comb__combine_sparse_signals__001::assumption::0`: el[i]等）在同一周期内应产生完全相同的值，以保证控制一致。 bug_relevance: 若违反（例如由于设计错误或攻击导致源值不同），mr_err将被置位，组合输出可能变为多个值的按位或，导致下游使用未定义的选择状态，可能引发功能错误或安全违规。 query_signals=mr_state_sel mr_add_rk_sel mr_key_full_sel mr_key_dec_sel mr_key_words_sel mr_r
- `key_words_zero_encoding` evidence:
  `legacy_signal_plus_uncertain` `legacy_uncertain::aes::79`: core__generate_for__gen_shares_round_key__001::uncertain::2 uncertain=KEY_WORDS_ZERO 是否仅是安全回退还是有实际功能路径不明确。 signals= source_refs=output/aes_mini_rtl/aes_cipher_core.sv:516-535
  `optimized_with_unmatched_uncertain` `optimized_unmatched_uncertain::aes_cipher_core__generate_for__gen_shares_round_key__001::uncertain::2`: core__generate_for__gen_shares_round_key__001::uncertain::2 uncertain=KEY_WORDS_ZERO 是否仅是安全回退还是有实际功能路径不明确。 signals= source_refs=output/aes_mini_rtl/aes_cipher_core.sv:516-535

#### N-002 aes_iv_sel_fault_fold
OR-merged iv_sel can write a wrong CTR IV source because iv_we is not gated by mux_sel_err.
Note: Expected to be absent from the current aes mini specs if iv_reg/iv_sel chunks were not generated.

| Subcase | Observable | legacy_ag_only | legacy_signal_plus_uncertain | optimized_paired_only | optimized_with_unmatched_uncertain |
|---|---:|---:|---:|---:|---:|
| iv_ctr_write_not_gated | no | miss | miss | miss | miss |


