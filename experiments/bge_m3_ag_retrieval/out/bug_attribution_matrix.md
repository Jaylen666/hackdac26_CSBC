# Bug Attribution Matrix

This matrix joins known benchmark bugs with AG/uncertain coverage proxy results and curated Phase 3 review outcomes.
A coverage hit means one work item contains all keyword groups for at least one known-bug subcase; it is not a substitute for LLM verification.

## Matrix

| Module | Bug | Observable | Legacy AG | Legacy+U | U only | Semantic AG | Semantic+U | Confirmed | Attribution | Confidence |
|---|---|---:|---:|---:|---:|---:|---:|---|---|---|
| hmac | 009 hmac_bug_002_wipe_secret | 2/2 | hit@49 | hit@49 | hit@22 | hit@7 | hit@7 | F-0027,F-0031 | phase3_verified_framework_candidate | high |
| hmac | 010 hmac_bug_003_sha512_outer_len | 1/1 | hit@147 | hit@147 | hit@93 | hit@5 | hit@5 | F-0025,F-0029,F-0032 | phase3_verified_framework_candidate | high |
| hmac | 011 hmac_bug_004_stale_completion | 3/3 | hit@16 | hit@16 | hit@8 | hit@23 | hit@23 | - | candidate_covered_not_confirmed | medium |
| hmac | 019 hmac_bug_005_alert_ping_skew | 1/2 | hit@140 | hit@140 | miss | hit@89 | hit@89 | - | candidate_covered_not_confirmed | low |
| aes | 004 aes_bug_002_state_clear_retention | 2/2 | miss | hit@32 | hit@32 | miss | hit@61 | F-0007 | phase3_verified_framework_candidate | high |
| aes | 005 aes_bug_001_key_clear_mux | 2/2 | miss | hit@25 | hit@25 | miss | hit@55 | - | candidate_covered_not_confirmed | medium |
| aes | 003 aes_bug_003_sw_key_clear_state_retention | 1/1 | miss | hit@25 | hit@25 | miss | hit@55 | F-0007 | phase3_verified_framework_candidate | high |
| aes | N-001 aes_key_words_sel_fault_fold | 2/2 | miss | hit@79 | hit@79 | hit@12 | hit@12 | F-0009 | phase3_discovery | high |
| aes | N-002 aes_iv_sel_fault_fold | 0/1 | miss | miss | miss | miss | miss | F-0040 | phase3_discovery | medium |
| keymgr | 026 keymgr_bug_001_invalid_stage_raw_key | 2/2 | miss | miss | miss | miss | miss | F-EXTRA-0001 | independent_phase3_discovery | high |
| keymgr | 031 KEYMGR-TRIAGE-004_data_en_illegal_redirect | 1/1 | hit@18 | hit@18 | hit@51 | hit@3 | hit@3 | - | candidate_covered_not_confirmed | medium |
| keymgr | N-003 keymgr_ecc_stale | 2/2 | hit@30 | hit@30 | miss | hit@1 | hit@1 | F-0002,F-0004 | phase3_discovery | high |
| kmac | 017 kmac_bug_001_static_mask | 2/2 | hit@88 | hit@88 | hit@55 | hit@3 | hit@3 | - | candidate_covered_not_confirmed | medium |
| kmac | 021 kmac_bug_003_alert_ping_skew | 2/2 | hit@217 | hit@217 | miss | miss | miss | - | candidate_covered_not_confirmed | low |
| kmac | 036 KMAC-BUG-002_sparse_fsm_error_delay | 1/1 | miss | hit@137 | hit@137 | hit@186 | hit@186 | - | candidate_covered_not_confirmed | medium |
| kmac | N-005 kmac_reduced_share_unpacker | 1/1 | miss | hit@313 | hit@313 | hit@221 | hit@221 | F-0073 | phase3_discovery | high |
| rv_dm | 022 rv_dm_bug_004 | 1/1 | hit@2 | hit@2 | hit@11 | hit@2 | hit@2 | - | candidate_covered_not_confirmed | low |
| rv_dm | 034 RV_DM-TRIAGE-003_pending_dmi_response_drop | 0/1 | miss | miss | miss | miss | miss | - | missed_or_unobservable | low |
| rv_dm | 046 rv_dm_bug_005_stale_debug_authorization | 1/1 | miss | miss | miss | miss | miss | - | missed_or_unobservable | low |
| rv_dm | 047 rv_dm_bug_002_late_debug_ndmreset | 0/1 | miss | miss | miss | miss | miss | - | missed_or_unobservable | low |
| uart | 033 uart_bug_002_lsio_trigger_watermark | 2/2 | miss | hit@8 | hit@8 | hit@6 | hit@6 | F-EXTRA-0001 | independent_phase3_discovery | high |
| uart | N-004 uart_break_interrupt | 1/2 | hit@3 | hit@3 | miss | hit@3 | hit@3 | F-EXTRA-0002 | phase3_discovery | high |

## Coverage Summary

| Module | Bugs | Confirmed | Legacy AG any | Legacy+U any | Semantic AG any | Semantic+U any | Unobservable |
|---|---:|---:|---:|---:|---:|---:|---:|
| aes | 5 | 4 | 0 | 4 | 1 | 4 | 1 |
| hmac | 4 | 2 | 4 | 4 | 4 | 4 | 0 |
| keymgr | 3 | 2 | 2 | 2 | 2 | 2 | 0 |
| kmac | 4 | 1 | 2 | 4 | 3 | 3 | 0 |
| rv_dm | 4 | 0 | 1 | 1 | 1 | 1 | 2 |
| uart | 2 | 2 | 1 | 2 | 2 | 2 | 0 |

## Work-Item Counts

| Module | legacy_ag_only | legacy+uncertain | semantic_paired | semantic+unmatched_uncertain |
|---|---:|---:|---:|---:|
| hmac | 265 | 428 | 135 | 232 |
| aes | 25 | 128 | 37 | 123 |
| keymgr | 59 | 187 | 51 | 156 |
| kmac | 923 | 1294 | 282 | 496 |
| rv_dm | 2 | 25 | 6 | 28 |
| uart | 10 | 42 | 6 | 36 |
