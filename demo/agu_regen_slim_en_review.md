# Slim English A/G/U Prompt Review

This note compares the verbose structured A/G/U prompt with the slim English
prompt on four bug-critical chunks:

- `keymgr_ctrl__always_ff__line_304__001`
- `keymgr_ctrl__generate_for__gen_ecc_loop_cdi__001`
- `hmac__always_comb__cast_digest_size__001`
- `hmac_core__always_comb__assign_sha_message_length__001`

## Prompt Changes

- Removed `summary` and `behavior` generation.
- Switched the instruction and output schema to English.
- Kept a compact structured header plus unbounded `guarantees`,
  `assumptions`, and `uncertain_points`.
- Shortened field names and claim requirements.
- Added targeted extraction checks for bug-prone RTL patterns:
  reset-only data with updated ECC/tag/check bits, missing legal enum cases,
  write/use before error containment, and ineffective wipe/mask/entropy paths.
- Tightened generic lint noise: width, array-bound, and parameter claims should
  be emitted only when the chunk itself shows a concrete legal configuration
  reaching a security/control/data-write path.

Prompt sizes:

| Prompt | Size |
| --- | ---: |
| `chunk_spec_agu_structured.md` | 9,451 bytes |
| `chunk_spec_agu_structured_slim_en.md` | 5,108 bytes |

## Token Cost

| Run | Calls | Input Tokens | Output Tokens | Total Tokens | Wall Time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Verbose structured | 4 | 11,214 | 19,561 | 30,775 | 376.4s |
| Slim English v1 | 4 | 7,182 | 12,457 | 19,639 | 234.7s |
| Slim English v2 | 4 | 6,864 | 5,239 | 12,103 | 66.0s |

Compared with the verbose prompt, v2 reduces:

- Total tokens by 60.7%.
- Input tokens by 38.8%.
- Output tokens by 73.2%.
- Wall time by 82.5% in this run.

## Quality Check

### HMAC 010: SHA-512 Outer-Length Bug

The slim v2 output preserves the critical clue:

- File: `demo/agu_regen_specs_slim_en_v2/hmac_core__always_comb__assign_sha_message_length__001.json`
- Finding: high-priority `U_unhandled_legal_case`
- Condition: `hmac_en_i && (sel_msglen != SelIPadMsg) && (digest_size_i == SHA2_512)`
- Risk: SHA2_512 falls through to the SHA2_384 final length constant,
  computing `BlockSizeSHA512in64 + 384` instead of `+512`.

This is the desired behavior. The generated claim is concise and directly
actionable for Phase 3.

### Keymgr N-003: Data/ECC Divergence

The slim v2 output also preserves the key Keymgr clue:

- File: `demo/agu_regen_specs_slim_en_v2/keymgr_ctrl__always_ff__line_304__001.json`
- Finding: high-priority `U_fault_containment_gap`
- Condition: outside reset
- Risk: `key_state_q` is only reset, while `key_state_ecc_q` is refreshed from
  `key_state_ecc_words_d`, so data and integrity metadata may diverge.

This is stronger than the previous verbose result because it classifies the
issue as a direct containment gap instead of a generic cross-chunk dependency.

## Residual Noise

The v2 output still emits some context-dependent but useful uncertain points:

- HMAC digest-size fallback requires cross-chunk confirmation that `SHA2_None`
  is blocked.
- Keymgr ECC decode only drives `ecc_errs`; the containment action must be
  checked elsewhere.

These are acceptable Phase 3 candidates. The earlier mechanical parameter/width
claims were reduced substantially and no longer dominate the output.

## Recommendation

Use `chunk_spec_agu_structured_slim_en.md` as the next experimental spec
generation prompt. It keeps the two known bug signals in this test set while
substantially reducing token cost and output verbosity.

Do not treat this four-chunk test as a full replacement validation. Before
switching the main pipeline, run the same comparison on at least AES/HMAC/Keymgr
full module specs and measure:

- known-bug hit rate in A/G/U,
- uncertain-point volume,
- Phase 3 token cost,
- false-positive rate after Phase 3 verification.

Reproduction:

```bash
cd /home/smy/rtl_bug_agent
python3 demo/regenerate_agu_specs.py --out-dir demo/agu_regen_specs_slim_en_v2
```
