# HMAC / KEYMGR / AES / UART / DMA / RV_DM / SOC_DBG_CTRL Phase3 命中与漏检分析

本文基于最新 dual-claim phase3 输出和 `bug_comparison_table_csbc_v2.csv` 的 `CSBC_v3` 严格命中标准整理。严格命中标准为：必须确认官方 bug 的精确代码位置和根因；只命中同一代码 chunk、同一类 countermeasure 或相邻问题，不算命中。

## 命中来源分析（按 bug 去重）

统计口径：

| 分类 | 定义 |
| --- | --- |
| 非 extra | 该 bug 的 primary confirmed finding 中 `is_extra_finding=false`，finding 字面/root cause 本身已经精确命中 bug。 |
| extra from ref | 该 bug 的 primary confirmed finding 中 `is_extra_finding=true` 且 `extra_finding_from_ref` 非空，bug 主要由 ref-derived check 找到。 |
| 纯 extra | 该 bug 的 primary confirmed finding 中 `is_extra_finding=true` 且 `extra_finding_from_ref=null`，即 finding 字面不准，但源码同路径/邻近逻辑检查发现真实 bug。 |
| 漏检 | `CSBC_v3=no`，没有 confirmed finding 严格命中精确代码位置和根因。 |

这里的“按 bug 去重”指每个 CSV bug 行只选一个最能代表严格命中的 primary confirmed finding；多个 confirmed finding 命中同一个 bug 时不重复计数。

| 模块 | 已知 bug 数 | CSBC_v3 命中 | 漏检 | 非 extra | extra from ref | 纯 extra |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| HMAC | 7 | 5 | 2 | 5 | 0 | 0 |
| KEYMGR | 4 | 4 | 0 | 3 | 0 | 1 |
| AES | 5 | 2 | 3 | 2 | 0 | 0 |
| UART | 3 | 3 | 0 | 3 | 0 | 0 |
| Total | 19 | 14 | 5 | 13 | 0 | 1 |

逐 bug primary hit 归类：

| 模块 | Bug | CSBC_v3 | Primary confirmed finding | 去重分类 | 说明 |
| --- | --- | --- | --- | --- | --- |
| HMAC | 010 | yes | `F-0020` | 非 extra | finding 字面精确命中 SHA-512 final outer length 落入 SHA-384 default branch。 |
| HMAC | 009 | yes | `F-0022` | 非 extra | finding 字面精确命中 `cfg_block` 在 `hash_stop` 时过早清除。 |
| HMAC | 011 | no | - | 漏检 | 最近的 `F-0130` 确认的是 `cool_down_ct_d` latch，不是 127-cycle stale completion。 |
| HMAC | `wipe_secret_we` | yes | `F-0004` | 非 extra | finding 字面精确命中 `wipe_secret_we` 被 `reg_error` 错误门控。 |
| HMAC | `err_code` | yes | `F-0102` | 非 extra | finding 字面精确命中 duplicate case item 和 missing `invalid_config_atstart` error-code branch。 |
| HMAC | `extra_hash_stop_msg_freeze` | no | - | 漏检 | finding/ref 没有组合出 `hash_stop` 后 MSG_FIFO/message_length freeze 的精确序列。 |
| HMAC | `extra_hash_stop_unlock_window` | yes | `F-0022` | 非 extra | 与 `cfg_block` stop/idle 窗口同根，primary finding 字面命中。 |
| KEYMGR | 026 | yes | `F-0020` | 非 extra | finding 字面精确命中 `StCtrlInvalid` raw `key_state_q` 输出。 |
| KEYMGR | 031 | yes | `F-0025` | 非 extra | finding 字面精确命中 data-enable FSM illegal-state default 不报 `fsm_err_o`。 |
| KEYMGR | N-003 | yes | `F-0011` | 非 extra | finding 字面精确命中 `key_state_q` 不更新但 ECC 由 `key_state_d` 更新。 |
| KEYMGR | `extra_flash_seed_validity_shift` | yes | `F-0115` | 纯 extra | `F-0115` 字面 premise 不准，但同一 owner seed 消费路径发现 `adv_dvalid[Owner]` 硬连 1 的真实 validity-shift bug；`F-0004/F-0005` 另命中 creator OTP seed valid-bit 子路径，但本行按 primary stage-shift 命中计一次。 |
| AES | 004 | no | - | 漏检 | `F-0066` 是强 finding，但 phase3 被 selector error/terminal mitigation 说服，未确认 state PRD wipe 违例。 |
| AES | 005 | yes | `F-0004` / `F-0051` | 非 extra | finding 字面精确命中 `KEY_DEC_CLEAR` 和 `KEY_FULL_CLEAR` 写 `key_expand_out`。 |
| AES | N-001 | no | - | 漏检 | 只命中同类 multi-rail/sparse 控制问题，未精确到 `key_words_sel` legal-encoding bypass。 |
| AES | N-002 | no | - | 漏检 | finding 分别覆盖 `iv_sel` 或 `iv_we` 局部，未组合出 CTR update 错源写入。 |
| AES | `extra_secallowforcingmasks_ignored` | yes | `F-0239` | 非 extra | finding 字面精确命中 top-level `.SecAllowForcingMasks(1)` 硬编码。 |
| UART | 033 | yes | `F-0059` | 非 extra | finding 字面精确命中 `lsio_trigger_o` 复位后恒为 1。 |
| UART | N-004 | yes | `F-0033` | 非 extra | primary finding 字面命中 `BRK_WAIT` 单周期 `rx_in` 高电平重新武装，缺少 half-bit high。 |
| UART | `extra_timeout_val0_sticky` | yes | `F-0040` | 非 extra | finding 字面精确命中 `TIMEOUT_CTRL.VAL=0 && EN=1` 使 `event_rx_timeout` sticky/asserted。 |

注意：这个表是 bug-level 去重统计。若按所有 confirmed finding-hit 统计，UART N-004、UART timeout、HMAC wipe/cfg_block、AES FORCE_MASKS、KEYMGR key-state/seed-validity 等 bug 都会被多个 finding 重复命中，其中会出现额外的 `extra from ref` 和纯 extra；但这些不应重复计入 bug 命中来源。

## 总览

| 模块 | 已知 bug 数 | CSBC_v3 命中 | 漏检 | 结论 |
| --- | ---: | ---: | ---: | --- |
| HMAC | 7 | 5 | 2 | 漏检集中在完成事件时序、`hash_stop` 后消息冻结这类跨状态/序列性质问题。 |
| KEYMGR | 4 | 4 | 0 | 本轮没有严格漏检；四个已知 bug 都有确认结果命中精确根因。 |
| AES | 5 | 2 | 3 | 漏检集中在有强 ref 或强 finding 但模型被已有 checker/terminal error 逻辑说服，未继续证明精确漏洞点。 |
| UART | 3 | 3 | 0 | 本轮三个 UART 已知 bug 都有严格命中，且 primary hit 均为非 extra。 |


最新 phase3 结果规模：

| 模块 | Finding 总数 | CONFIRMED | FALSE_ALARM | UNCERTAIN |
| --- | ---: | ---: | ---: | ---: |
| HMAC | 146 | 33 | 113 | 0 |
| KEYMGR | 237 | 32 | 203 | 2 |
| AES | 241 | 23 | 218 | 0 |
| UART | 61 | 21 | 40 | 0 |

## HMAC 漏检

### HMAC 011: stale completion / 127-cycle cool_down delay

官方 bug：

`hmac.sv` 的 done FSM 在 `DoneAwaitHashComplete` 中等待 `hash_running` 拉低后，并没有马上发出 `hash_done_event`，而是等 `cool_down_ct_q < 8'd127` 计满后才在 else 分支断言完成事件。关键代码在 `/home/smy/opentitan/hw/ip/hmac/rtl/hmac.sv:180-188`：

```systemverilog
DoneAwaitHashComplete: begin
  if (!hash_running) begin
    if (cool_down_ct_q < 8'd127) begin
      cool_down_ct_d = cool_down_ct_q + 1'b1;
      hash_done_event = 1'b0;
      done_state_d = DoneAwaitHashComplete;
    end else begin
      hash_done_event = 1'b1;
      done_state_d = DoneAwaitCmd;
    end
  end
end
```

Finding 情况：

| 项 | 结论 |
| --- | --- |
| `In_AGU_v2` | weak |
| `In_pair_v2` | weak@130/146 |
| 最相关 finding | `F-0130`、`F-0039` |
| 最新 phase3 | `F-0130 = CONFIRMED`，但确认的是 `cool_down_ct_d` 缺省赋值/锁存问题；`F-0039 = FALSE_ALARM` |
| 是否严格命中 | no |

为什么不是命中：

`F-0130` 的源码位置很接近，而且 phase3 确认了真实 RTL 缺陷：`cool_down_ct_d` 在 `always_comb` 中没有全分支默认赋值，可能推断 latch。这个问题在 `/home/smy/opentitan/hw/ip/hmac/rtl/hmac.sv:149-207`，但它不是官方 bug 的精确漏洞点。官方 bug 的重点是 `hash_running` 已经结束后仍强制等待 127 cycle 才发 completion，属于 stale completion / 延迟完成语义问题。

`F-0039` 则把问题说成 `hash_done_event` 可能保持多周期。phase3 正确读到 `hash_done_event` 在 `always_comb` 开头默认清零，并且在发出事件后 `done_state_d` 回到 `DoneAwaitCmd`，因此判为 false alarm。这个判断对 `F-0039` 的字面 claim 是合理的，但它没有覆盖“完成事件过晚”的官方 bug。

Ref 情况：

| 项 | 结论 |
| --- | --- |
| `In_Spec` | weak |
| `In_ref_raw` | yes，但只是弱相关 |
| 相关 ref | `hmac.hjson_001`: `hmac_done` 表示 HMAC/SHA-2 已完成；`hmac.hjson_007`: `hash_stop` 后等待 `hmac_done` 再保存上下文 |
| 是否足够强 | 不足够 |

弱 ref 的原因：

`/home/smy/opentitan/hw/ip/hmac/data/hmac.hjson:53-54` 只说 `hmac_done` 表示完成；`/home/smy/opentitan/hw/ip/hmac/data/hmac.hjson:362-366` 只说 `hash_stop` 后当前 block hash 完成时设置 `hmac_done` 并保存 digest/message length。它们支持 completion 语义，但没有明确说 `hash_running` 结束后必须零延迟或不能有 127-cycle cooldown。因此 ref 不能强迫模型把这个 delay 判成 bug。

没有 reason 出官方 bug 的主因：

这是一个“弱 finding + 弱 ref”的组合。AGU/finding 只捕捉到 done FSM、`cool_down_ct` 和 `hash_done_event` 附近的局部现象，没有把“127-cycle 延迟本身违反完成语义或造成 stale completion”讲清楚。phase3 在验证具体 finding 时，一个被证明是附近 latch 问题，一个被证明不是多周期 pulse 问题；由于缺少强 spec 对完成延迟的约束，模型没有理由从源码中独立推出官方 bug。

改进方向：

需要 AGU 或 ref 层产生更精确的时序属性，例如：`hash_stop` 后当当前 block 已完成并且 `hash_running==0` 时，`hmac_done/hash_done_event` 不应再被固定 cooldown 计数延迟。否则 phase3 很难把 127-cycle delay 从“实现选择”提升为 security bug。

### HMAC extra_hash_stop_msg_freeze: hash_stop 后仍可注入消息 / 改变保存长度

官方 bug：

`hash_stop` 的语义是停止当前消息流，等待当前 block hash 完成后通过 `hmac_done` 保存 `DIGEST_*` 和 `MSG_LENGTH_*` 作为上下文。但 RTL 中 `msg_allowed` 和 `message_length` 的更新没有被明确绑定到 stop 后冻结语义。

关键代码：

`msg_allowed` 只在 start/continue 时打开，在 `packer_flush_done` 时关闭，见 `/home/smy/opentitan/hw/ip/hmac/rtl/hmac.sv:413-420`：

```systemverilog
always_ff @(posedge clk_i or negedge rst_ni) begin
  if (!rst_ni) begin
    msg_allowed <= '0;
  end else if (hash_start_or_continue) begin
    msg_allowed <= 1'b 1;
  end else if (packer_flush_done) begin
    msg_allowed <= 1'b 0;
  end
end
```

`msg_write` 仍由 `msg_allowed` 控制，见 `/home/smy/opentitan/hw/ip/hmac/rtl/hmac.sv:628`：

```systemverilog
assign msg_write = msg_fifo_req & msg_fifo_we & ~hmac_fifo_wsel & msg_allowed;
```

`message_length` 在 `msg_write && sha_en && packer_ready` 时继续增加，见 `/home/smy/opentitan/hw/ip/hmac/rtl/hmac.sv:656-660`：

```systemverilog
if (hash_start) begin
  message_length_d = '0;
end else if (msg_write && sha_en && packer_ready) begin
  message_length_d = message_length + 64'(wmask_ones);
end
```

Finding 情况：

| 项 | 结论 |
| --- | --- |
| `In_AGU_v2` | strong |
| `In_pair_v2` | weak@90/146 |
| 最相关 finding | `F-0090`、`F-0126`，另有 `F-0022/F-0033/F-0088/F-0091` 命中相邻 cfg_block unlock 窗口 |
| 最新 phase3 | `F-0090 = FALSE_ALARM`，`F-0126 = FALSE_ALARM`；cfg_block 相关 finding 多个 confirmed，但不是消息冻结 bug |
| 是否严格命中 | no |

为什么没有强 finding：

AGU 已经强相关地看到 `message_length/cfg_block`、`msg_allowed/msg_write` 等路径，但送到 phase3 的最相关 finding 没有准确描述官方 bug。`F-0090` 的 claim 是 `packer_flush_done` 时 `msg_allowed` 更新行为未定义或可能保持旧值；phase3 读源码后发现 `/home/smy/opentitan/hw/ip/hmac/rtl/hmac.sv:419-420` 明确把 `msg_allowed` 清零，因此判 false alarm。这个判断对 `F-0090` 字面 claim 是正确的。

`F-0126` 的 claim 是 `hash_start_or_continue` 和 `packer_flush_done` 同周期时 start/continue 优先，导致 flush clear 被覆盖。phase3 进一步发现 `hash_start_or_continue` 不是 raw CSR intent，而是被 `sha_en & ~cfg_block & ~invalid_config` 过滤；active operation 下 raw start 会走 error path，因此判 false alarm。这个也没有覆盖官方 bug。

强 ref 情况：

| 项 | 结论 |
| --- | --- |
| `In_Spec` | strong |
| `In_ref_raw` | yes |
| 相关 ref | `hmac.hjson_007`, `hmac.hjson_013`, `hmac.hjson_014`, programmers guide |
| 是否足够强 | 足够强，但没有被 phase3 转化为精确源码属性 |

强 ref 的依据：

`/home/smy/opentitan/hw/ip/hmac/data/hmac.hjson:362-366` 明确说明 `hash_stop` 后 `hmac_done` 才表示当前 block 已 hash 完成，之后 `DIGEST_*` 和 `MSG_LENGTH_*` 共同形成 context save 信息。`/home/smy/opentitan/hw/ip/hmac/doc/programmers_guide.md:92-98` 进一步给出 stop/wait/save/restore 的流程。`/home/smy/opentitan/hw/ip/hmac/data/hmac.hjson:491-514` 说明 `MSG_LENGTH_*` 是 HMAC 计算出的 received message length，并且只允许在 `STATUS.hmac_idle` 时由 SW 写入。

没有 reason 出官方 bug 的主因：

phase3 仍然以 finding 的字面 claim 为主线。它把 `F-0090/F-0126` 的局部说法否掉后，没有从强 ref 独立构造并检查这个属性：`hash_stop` 后直到 `hmac_done/context save` 之前，`MSG_FIFO` 不应再接受新的 SW message，`MSG_LENGTH` 不应继续被新写入改变。也就是说，ref 是强的，但没有形成“stop window 内 msg_write/message_length 必须冻结”的源码级检查。

另一个重要现象是 phase3 已经多次确认了相邻的 `cfg_block` unlock bug，例如 `F-0022/F-0033/F-0088/F-0091`。这说明模型理解了 `hash_stop` 会打开一个非 idle 窗口，但它只把该窗口用于 KEY/CFG/CSR update 分析，没有扩展到 MSG_FIFO injection 和 `MSG_LENGTH` context 状态。

改进方向：

对这类问题，phase3 不能只问“finding 的局部 claim 是否成立”，还需要在 ref 强相关时强制写出 ref-derived sequence check。这里应要求模型建立时间线：`CMD.hash_stop` 写入、core drain current block、`hmac_done` 前的窗口、`msg_allowed/msg_write` 是否仍能为真、`message_length` 是否可能变化、最终 context save 是否污染。

## KEYMGR 漏检情况

KEYMGR 本轮没有严格漏检。

| Bug | CSBC_v3 | 主要命中 finding | 结论 |
| --- | --- | --- | --- |
| 026 | yes | `F-0020` rank 20/237 | 精确命中 `StCtrlInvalid` 暴露 raw `key_state_q`、绕过 entropy mask。 |
| 031 | yes | `F-0025` rank 25/237 | 精确命中 data-enable FSM illegal state default 到 `StCtrlDataDis` 且 `fsm_err_o=0`。 |
| N-003 | yes | `F-0011` rank 11/237 | 精确命中 `key_state_q` 不更新但 ECC 用 `key_state_d` 重算。 |
| extra_flash_seed_validity_shift | yes | `F-0115` rank 115/237 等 | 精确命中 owner/creator seed valid 检查偏移和消费阶段不一致。 |

需要注意的是，`extra_flash_seed_validity_shift` 的最高配对 `F-0010` 曾是 weak pair 且 phase3 判 false alarm，但最新结果中 `F-0115` 和其他 finding 已经确认了精确消费阶段问题，所以严格 `CSBC_v3` 仍为 yes。这类情况说明“最高 rank finding”不一定是最终命中项，统计时必须按所有 confirmed finding 查精确位置。

## AES 漏检

### AES 004: state clear default branch for AES-128/192 self-hold

官方 bug：

AES cipher state clear path要求 internal state register 用 PRD 清除。但 `aes_cipher_core.sv` 的 `state_mux` default 分支对非 AES-256 使用 self-hold，见 `/home/smy/opentitan/hw/ip/aes/rtl/aes_cipher_core.sv:247-254`：

```systemverilog
unique case (state_sel)
  STATE_INIT:  state_d = state_init_i;
  STATE_ROUND: state_d = add_round_key_out;
  STATE_CLEAR: state_d = prd_clearing_state_i;
  default:     state_d = (key_len_i == AES_256) ? prd_clearing_state_i : state_d;
endcase
```

Finding 情况：

| 项 | 结论 |
| --- | --- |
| `In_AGU_v2` | strong |
| `In_pair_v2` | strong@66/241 |
| 最相关 finding | `F-0066` |
| 最新 phase3 | `F-0066 = FALSE_ALARM` |
| 是否严格命中 | no |

强 finding 存在：

`F-0066` 的 finding 已经指到同一源码位置和同一局部现象：当 `state_sel` 非法且 `key_len_i != AES_256` 时，default 分支会让 state self-hold，而不是 PRD clear。这是强 finding，rank 66/241。

强 ref 存在：

`/home/smy/opentitan/hw/ip/aes/data/aes.hjson:362-365` 明确写明 internal state register 在最后一轮结束时用 pseudo-random data 清除。`aes_sec_cm_testplan.hjson` 也有针对 state PRD clear 的 SVA/testplan 约束。因此 `In_Spec=strong`，`In_ref_raw=yes`。

为什么 phase3 判 false alarm：

phase3 的核心 reasoning 是：这个 default path 需要非法 `state_sel`，而非法 selector 会被 `aes_sel_buf_chk` 检测，并通过 `/home/smy/opentitan/hw/ip/aes/rtl/aes_cipher_core.sv:712-714` 汇总到 `mux_sel_err`，再由 `/home/smy/opentitan/hw/ip/aes/rtl/aes_cipher_control_fsm.sv:442-447` 进入 terminal error，且输出释放被 gating。因此它认为“不会静默保留并释放 state”。

漏检原因：

这是强 finding + 强 ref 下的 reasoning 失败。模型把“错误会被检测并阻止输出释放”当成充分缓解，但官方 bug 的核心不是只看 output release，而是 state clear countermeasure 本身要求 secret internal state 被 PRD wipe。`state_d = state_d` 会保留旧 state，和 strong ref 的“internal state register cleared with pseudo-random data”直接冲突。也就是说，terminal error/no-output-release 不能自动替代 PRD clear；phase3 没有继续验证“发生 fault/error 后 state 是否仍按 SEC_WIPE 语义清除”。

改进方向：

对 SEC_WIPE/SCA 类 ref，需要提示模型区分两种性质：数据不释放、状态被清除。前者满足不代表后者满足。遇到 `*_CLEAR`、`SEC_WIPE`、`prd_clearing_*` ref 时，应强制检查目标寄存器最终是否写入 PRD，而不是只检查是否 alert 或 terminal。

### AES N-001: key_words_sel redundant rail OR merge legal-encoding bypass

官方 bug：

`key_words_sel` 属于 cipher control 中的 multi-rail sparse selector。`aes_cipher_control.sv` 对各 rail 的 `mr_key_words_sel` 做 OR 合并，见 `/home/smy/opentitan/hw/ip/aes/rtl/aes_cipher_control.sv:311-326`：

```systemverilog
key_words_sel_o = key_words_sel_e'({KeyWordsSelWidth{1'b0}});
for (int i = 0; i < Sp2VWidth; i++) begin
  key_words_sel_o = key_words_sel_e'({key_words_sel_o} | {mr_key_words_sel[i]});
end
```

合并后的 selector 进入 `aes_cipher_core.sv` 的 key word mux，见 `/home/smy/opentitan/hw/ip/aes/rtl/aes_cipher_core.sv:517-524`，并且又经过 sparse selector checker，见 `/home/smy/opentitan/hw/ip/aes/rtl/aes_cipher_core.sv:686-697`。官方 bug 的精确点是：single-rail fault 可以通过 OR 合并产生另一个 legal encoding，从而绕过预期 error 路径并选择错误 key words。

Finding 情况：

| 项 | 结论 |
| --- | --- |
| `In_AGU_v2` | weak |
| `In_pair_v2` | weak@165/241 |
| 最相关 finding | `F-0165`，另有 `F-0140` 触及 `key_words_sel` |
| 最新 phase3 | `F-0165 = FALSE_ALARM`；`F-0140 = FALSE_ALARM` |
| 是否严格命中 | no |

没有强 exact finding：

`F-0165` 讲的是 redundant select rail mismatch 导致 OR-combined 多条 key/IV/state paths，是同类问题，但没有明确指到 `key_words_sel`，也没有说明“single-rail fault -> another legal encoding -> bypass error”的官方根因。`F-0140` 触及 `key_words_sel`，但它关注的是 default-to-zero 和 invalid selector 是否被 checker 检测，也没有覆盖 legal-encoding bypass。

强 ref 存在：

`/home/smy/opentitan/hw/ip/aes/data/aes.hjson:395-396` 说明 critical handshake/MUX control signals 使用 sparse encodings。`/home/smy/opentitan/hw/ip/aes/doc/theory_of_operation.md:374-382` 说明 multi-rail/fault countermeasure 检测到 fault 后应触发 fatal alert、terminal error、停止释放数据并锁住。`/home/smy/opentitan/hw/ip/aes/data/aes_sec_cm_testplan.hjson:153-157` 也明确要求 forcing redundant rails 的 states/inputs/outputs 到 valid 或 invalid encodings 时，DUT 停止处理、lock up 并触发 fatal alert。因此 `In_Spec=strong`。

为什么 phase3 判 false alarm：

phase3 看到 `aes_cipher_control.sv:329-336` 对合并后的 selector 和每个 rail 做比较，认为合法编码 mismatch 会被 `mr_err` 检出；又看到 `aes_cipher_core.sv:686-714` 的 selector checker 会检出 invalid encoding，于是认为 OR combine 不是漏洞。这个 reasoning 对“普通 rail disagreement 是否有 checker”是合理的，但它没有构造官方 bug 的精确 fault 模型，也没有验证 fault 是否可以作用在 checker 覆盖不到的边界或使合并结果仍落在 legal selector 上。

漏检原因：

这是“强 ref + 弱 finding”的典型漏检。ref 能说明正确行为：多轨/稀疏控制 fault 必须导致 fatal/terminal，不能产生 silent wrong key selection。但 finding 没有准确描述 `key_words_sel` 的具体编码绕过路径，phase3 就停在“源码里看起来有 `mr_err` 和 `aes_sel_buf_chk`”这一层，没有进一步做 adversarial fault placement 和 legal-encoding 反证。

改进方向：

对 multi-rail sparse selector 类 bug，需要要求 phase3 显式列出 fault 注入点和 checker 覆盖边界：fault 在 individual rail 前、OR combine 后、`mr_err` compare 前后、`aes_sel_buf_chk` 前后分别是否会被检测。只说“有 downstream checker”不应视为完成分析，必须证明官方 bug 指定的 legal-encoding bypass 不存在。

### AES N-002: iv_sel rail OR merge + iv_we ungated during CTR update

官方 bug：

官方 bug 是组合性质：`iv_sel` 的 redundant rail OR merge 可能选择错误 IV source，而 `iv_we` 在 CTR update 路径仍然允许写入，导致 wrong IV source 在 error 被处理前写进 IV register。

关键代码：

`aes_control.sv` 对 `iv_sel` 做 OR combine，并用 `mr_err` 比较 rail mismatch，见 `/home/smy/opentitan/hw/ip/aes/rtl/aes_control.sv:470-505`。

`aes_core.sv` 的 IV mux 和 IV register update 见 `/home/smy/opentitan/hw/ip/aes/rtl/aes_core.sv:341-359`：

```systemverilog
unique case (iv_sel)
  IV_INPUT:        iv_d = iv;
  IV_DATA_OUT:     iv_d = data_out_d;
  IV_DATA_OUT_RAW: iv_d = aes_transpose(state_out);
  IV_DATA_IN_PREV: iv_d = data_in_prev_q;
  IV_CTR:          iv_d = ctr;
  IV_CLEAR:        iv_d = prd_clearing_data;
  default:         iv_d = prd_clearing_data;
endcase

for (int i = 0; i < NumSlicesCtr; i++) begin
  if (iv_we[i] == SP2V_HIGH) begin
    iv_q[i] <= iv_d[i];
  end
end
```

CTR 相关更新中，`iv_sel_o` 和 `iv_we_o` 在 control FSM 内组合产生。`CTRL_PRNG_UPDATE` 中 `/home/smy/opentitan/hw/ip/aes/rtl/aes_control_fsm.sv:438-441` 使用 `IV_CTR` 和 `ctr_we_i`；`CTRL_FINISH` 中 `/home/smy/opentitan/hw/ip/aes/rtl/aes_control_fsm.sv:526-540` 也对 CTR 使用 `IV_CTR` 和 `ctr_we_i`。

Finding 情况：

| 项 | 结论 |
| --- | --- |
| `In_AGU_v2` | weak |
| `In_pair_v2` | weak@77/241 |
| 最相关 finding | `F-0077`, `F-0093`, `F-0114`, `F-0165` |
| 最新 phase3 | 这些候选均为 `FALSE_ALARM` |
| 是否严格命中 | no |

没有强 exact finding：

`F-0077` 关注 `iv_sel` one-hot/sparse guarantee；`F-0093/F-0114` 关注 `log_iv_we` 到 `iv_we_o` 的 illegal sparse encoding；`F-0165` 关注 generic OR-combined rail mismatch。这些 finding 各自触及 bug 的一部分，但没有把 `iv_sel` 错源选择和 `iv_we/ctr_we` 写使能在 CTR update 同一时间线中组合起来。

强 ref 存在：

与 AES N-001 类似，`aes.hjson:395-396`、`theory_of_operation.md:374-382`、`aes_sec_cm_testplan.hjson:153-157` 都支持“multi-rail/sparse control fault 必须 fatal/terminal/lockup”的强约束。CSV 中还记录了 `aes_testplan.hjson:239-242` 一类 busy DUT 不应被修改 IV/config/key 的测试约束。因此 `In_Spec=strong`，`In_ref_raw=yes`。

为什么 phase3 判 false alarm：

phase3 把问题拆成了两个独立局部 claim。对 `iv_sel`，它看到 `aes_control.sv:470-505` 有 rail compare/mr_err，且 `aes_core.sv:773-788` 有 `aes_sel_buf_chk`，于是判 `F-0077/F-0165` false。对 `iv_we`，它看到 `/home/smy/opentitan/hw/ip/aes/rtl/aes_core.sv:817-849` 把每个 `iv_we_ctrl` 通过 `aes_sel_buf_chk` 重新检查后再用于 `/home/smy/opentitan/hw/ip/aes/rtl/aes_core.sv:353-359` 的 IV register write，于是判 `F-0093/F-0114` false。

漏检原因：

这是“组合 bug 被拆成两个单点 false alarm”的问题。官方 bug 不是单纯说 `iv_sel` 没 checker，也不是单纯说 `iv_we` illegal encoding 没 checker，而是说在 CTR update 的具体时序里，wrong `iv_sel` source 和 active `iv_we/ctr_we` 可以组合导致 IV 被错误写入。phase3 没有建立 `CTRL_PRNG_UPDATE/CTRL_FINISH -> iv_sel_o -> iv_d -> iv_we -> iv_q` 的同周期数据/控制时间线，也没有检查 error signal 是否能在写入前阻止该次 IV update。

改进方向：

对这类组合性质 finding，phase3 需要强制写出“数据源选择 + 写使能 + error gating”的同周期路径。只分别证明 selector 有 checker、write enable 有 checker，不足以排除“错误源在被检测前被合法写使能采样”的 bug。

## 跨模块结论

### 漏检类型归类

| 类型 | 对应 bug | 特征 | 当前机制问题 |
| --- | --- | --- | --- |
| 弱 finding + 弱 ref | HMAC 011 | 局部代码可疑，但 spec 没有强时延约束 | phase3 没有足够依据把实现延迟判成 bug |
| 强 ref + finding 说偏 | HMAC extra_hash_stop_msg_freeze | ref 明确，finding 只覆盖局部错误 claim | 模型否掉局部 claim 后没有从 ref 独立构造 sequence check |
| 强 finding + 强 ref 但过度信任 mitigation | AES 004 | finding/ref 都准，但模型认为 terminal/no-release 足够 | 模型把 alert/no output release 误当成 PRD wipe 的替代 |
| 强 ref + 弱 finding | AES N-001 | ref 说明 fault 必须 fatal，finding 未精确到 `key_words_sel` bypass | 模型看到 checker 后未做 legal-encoding/fault-placement 反证 |
| 强 ref + 多点组合 bug | AES N-002 | 单点 finding 都能被局部 checker 解释 | 模型没有把 selector、write enable、error gating 合成同一时序路径 |

### 对 ref 机制的判断

ref 机制对 HMAC/AES 的漏检不是没有价值。HMAC extra、AES 004、AES N-001、AES N-002 都有 strong ref，说明 ref 确实把正确硬件行为提供给了 phase3。问题主要出在 phase3 的使用方式：模型仍倾向于先验证 finding 的字面 claim，一旦 finding claim 被局部源码反驳，就停止或弱化 ref-derived 检查。

因此，下一步不一定要增加更多 ref 字段，而是要让 phase3 在 reasoning 中显式执行两个并列检查：

| 检查 | 要求 |
| --- | --- |
| Finding-derived check | 验证 finding 字面描述是否被源码支持；如果不支持，要说明它错在哪里。 |
| Ref-derived check | 对 relevant/strong ref 单独写出应满足的源码级 property；即使 finding 字面 claim 被否，也要检查 RTL 是否满足 ref。 |

### Prompt / 流程改进建议

1. 对 strong ref，要求模型生成一条源码级 obligation。例如 `hash_stop` ref 应生成“stop 后到 done/context save 前 MSG_LENGTH 不应被新 msg_write 改变”；AES state wipe ref 应生成“internal state register 必须写 PRD，不只是阻止 output release”。

2. 对 countermeasure/fault 类 bug，要求模型区分 detection、blocking、cleanup 三种性质。检测到 fault 和停止 release 不等于状态已 wipe，也不等于本周期写入已被阻止。

3. 对 multi-rail/sparse selector，要求模型做 fault-placement 表。至少区分 fault 在 rail output、OR combine 后、checker 前、checker 后、write enable 同周期路径上的影响。

4. 对跨状态序列 bug，要求模型写 timeline。HMAC `hash_stop`、AES CTR IV update 这类 bug 不适合只看单个 always block 或单个 checker。

5. 统计命中时继续保持严格标准。HMAC 011 的 `F-0130` 是同位置 confirmed，但不是官方 bug；AES N-001/N-002 的 `F-0128/F-0138` 是相邻 multi-rail confirmed，但不是精确 bug。严格口径能准确暴露 phase3 reasoning 的真实缺口。

## DMA / RV_DM / SOC_DBG_CTRL 命中来源与漏检分析

本节基于最新三模块 dual-claim phase3 输出：

| 模块 | Phase3 输入数 | CONFIRMED | FALSE_ALARM | NEEDS_MORE_CONTEXT |
| --- | ---: | ---: | ---: | ---: |
| DMA | 84 | 5 | 78 | 1 |
| RV_DM | 54 | 8 | 46 | 0 |
| SOC_DBG_CTRL | 44 | 5 | 38 | 1 |

### 命中来源分析（按已知 bug 去重）

统计口径仍然沿用前文：每个 CSV bug 行只选一个 primary confirmed finding；严格命中要求 confirmed defect 落在官方 bug 的精确代码位置和根因，或者至少同一精确代码位置。只命中同一代码 chunk、同类 countermeasure 或邻近逻辑不算严格命中。

| 模块 | 已知 bug 数 | CSBC_v3 命中 | 漏检 | 非 extra | extra from ref | 纯 extra |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DMA | 3 | 0 | 3 | 0 | 0 | 0 |
| RV_DM | 4 | 0 | 4 | 0 | 0 | 0 |
| SOC_DBG_CTRL | 2 | 1 | 1 | 1 | 0 | 0 |
| Total | 9 | 1 | 8 | 1 | 0 | 0 |

逐 bug primary hit 归类：

| 模块 | Bug | CSBC_v3 | Primary confirmed finding | 去重分类 | 说明 |
| --- | --- | --- | --- | --- | --- |
| DMA | 007 | no | - | 漏检 | phase3 找到 TLUL d_error status/alert mismatch，但没有命中 `DmaError -> DmaIdle` 后继续读的精确 bug。 |
| DMA | 032 | no | - | 漏检 | 最近 finding `F-0060` 只描述 `clear_index` missing/double clear 风险，未覆盖同周期 response 使第二个 clear write 丢失。 |
| DMA | `dma_F-0021` | no | - | 漏检 | finding 命中 ctrl_state register/fault-hardening 区域，但 phase3 只反驳 invalid encoding，未确认 dense valid-to-valid flip / no sparse FSM bug。 |
| RV_DM | 022 | no | - | 漏检 | 只有 generic alert sender finding/ref，没有 `SkewCycles(AlertSkewCycles)` 或 ping-skew 精确约束。 |
| RV_DM | 034 | no | - | 漏检 | 没有 finding/ref 指向 `dmi_rsp_valid_i & dmi_en` 丢失 `tlul_resp_pending` 的 pending response bug。 |
| RV_DM | 046 | no | - | 漏检 | `F-0001` 相关 lifecycle/DMI gating 被确认，但位置是 `rv_dm_dmi_gate.sv:230-245/274-278`，不是 stale `lc_hw_debug_clr` latch 的 `165-178`。 |
| RV_DM | 047 | no | - | 漏检 | NDM reset 相关 finding 都停留在 sync/pending/ack 邻近逻辑，没有命中 live `reset_req_en` revocation bug。 |
| SOC_DBG_CTRL | 024 | no | - | 漏检 | `F-0019` 确认的是 alert_test bit-order bug，不是 prim_alert_sender ping-skew/SkewCycles bug。 |
| SOC_DBG_CTRL | 045 | yes | `F-0004` | 非 extra | `F-0004/F-0013/F-0033` 确认 relocked invalid-code fail-open，落在同一精确赋值位置 `soc_dbg_ctrl.sv:203/205/206`；语义不是 `lc_rma_state_i/lc_cpu_en_i` gating，所以 pair 仍是 weak。 |

### Confirmed finding 的 extra 类型（未按已知 bug 去重）

这个表统计 phase3 输出中的所有 confirmed finding，而不是已知 bug 覆盖率。它反映 ref 机制是否产生了新发现。

| 模块 | CONFIRMED finding | 非 extra | extra from ref | 纯 extra | 主要真实缺陷 |
| --- | ---: | ---: | ---: | ---: | --- |
| DMA | 5 | 3 | 2 | 0 | TLUL d_error 未设置 aborted/alert；`OtAgentId` 越界；地址 auto-increment 条件反了。 |
| RV_DM | 8 | 2 | 6 | 0 | TLUL-DMI request-valid lifecycle gating；SBA `host_r_other_err` 未接 fatal alert。 |
| SOC_DBG_CTRL | 5 | 3 | 0 | 2 | Prod debug policy relocked invalid-code fail-open；ALERT_TEST fatal/recoverable bit-order 反了。 |

需要注意：

| 现象 | 解释 |
| --- | --- |
| DMA/RV_DM 的 extra from ref 很多 | ref 机制确实能在 finding 字面不准时挖出真实新 bug，例如 DMA TLUL d_error status/alert mismatch 和 RV_DM SBA fatal alert gap。 |
| 这些 extra 没有提升 known-bug 覆盖 | 它们大多是相邻或同模块新 bug，不在 CSV 对应已知 bug 的精确代码位置/根因上。 |
| SOC_DBG_CTRL 045 是特殊命中 | 命中来自同一精确赋值位置，但 confirmed root 是 relocked invalid-code fail-open，不是 lifecycle input gating；因此 `CSBC_v3=yes`，`In_pair_v2=weak@4/44`。 |

## DMA 漏检

### DMA 007: `DmaError -> DmaIdle` 后继续读，error status 仍保持

官方 bug：

`dma.sv:1031-1036` 附近的 fault-injection 情况会让 DMA FSM 从 `DmaError` 回到 `DmaIdle`，随后在 `STATUS.error` 仍为 1 时继续发起新的 system read。这个 bug 的安全点不是普通 TLUL error 进入 `DmaError`，而是错误状态未被软件清除时硬件恢复 post-error progress。

Finding / ref 情况：

| 项 | 结论 |
| --- | --- |
| `In_AGU_v2` | weak |
| `In_pair_v2` | no |
| `In_Spec` | strong |
| `In_ref_raw` | yes: `dma.hjson_017`, `dma.hjson_018`, `dma_testplan.hjson_005` |
| 最新 phase3 | 没有 strict hit；`F-0002/F-0038` confirmed 了相邻 TLUL d_error status/alert bug |

phase3 找到的相关 bug：

`F-0002` 和 `F-0038` 都是 `extra from ref`，来源是 `dma_testplan.hjson_005`。它们确认了普通 TLUL read/write `d_error` 会在 `/home/smy/opentitan/hw/ip/dma/rtl/dma.sv:930-933` 和 `:957-960` 进入 `DmaError`，但 `/home/smy/opentitan/hw/ip/dma/rtl/dma.sv:1239-1243` 只对 `cfg_abort_en` 设置 `STATUS.aborted`，且 `/home/smy/opentitan/hw/ip/dma/rtl/dma.sv:180-183` 的 alert 只覆盖 integrity/state error，不覆盖 ordinary `d_error`。

为什么不是 DMA 007 命中：

这个 confirmed defect 是“TLUL d_error 后 status/alert reporting 不符合 testplan”，不是“`DmaError` 后恢复到 `DmaIdle` 并继续发新 read”。它没有命中 `dma.sv:1031-1036` 的 fault-induced post-error progress，也没有证明 `STATUS.error` 仍为 1 时会继续发起系统读。因此按精确位置/根因标准是漏检。

漏检原因：

| 方面 | 具体原因 |
| --- | --- |
| finding 问题 | 送入 phase3 的 finding 没有准确描述 `DmaError -> DmaIdle -> new read while STATUS.error=1` 这条序列；最近的是 generic state/error finding。 |
| ref 问题 | `dma_testplan.hjson_005` 强约束了 TLUL error 后 no further TLUL transaction，但它没有直接给出 `STATUS.error` 未清除时 FSM 必须保持 blocked 的源码级 property。 |
| reasoning 问题 | phase3 从 ref 中挖出了 ordinary `d_error` alert/status mismatch 后停止，没有继续把 ref obligation 映射到 `DmaError` 后是否还能恢复发 transaction。 |

改进方向：

需要把 strong ref 转成时序 obligation：一旦 DMA 因 TLUL/error 进入 error state，在 software 清除 `STATUS.error` 或重新配置前，不允许从 `DmaError` 恢复并发起任何 source/dest TLUL transaction。

### DMA 032: same-cycle interrupt-clear response 丢失第二个 clear write

官方 bug：

`dma.sv:694-742` 的 interrupt clear FSM 需要对 `CLEAR_INTR_SRC` 中每个置位 source 发 clear write。bug 是同周期 interrupt-clear response 使 `clear_index` 未正确前进，导致第二个配置的 interrupt clear write 没有发出。

Finding / ref 情况：

| 项 | 结论 |
| --- | --- |
| `In_AGU_v2` | weak |
| `In_pair_v2` | weak@60/84 |
| `In_Spec` | strong |
| `In_ref_raw` | yes: `dma.hjson_019` |
| 最新 phase3 | `F-0060/F-0068/F-0069` 均为 false alarm |

强 ref：

`dma.hjson_019` 说明 `CLEAR_INTR_SRC` 每个有效 bit 都会让 DMA 使用对应 `INTR_SRC_ADDR` 和 `INTR_SRC_WR_VAL` 发 write。官方 bug 丢掉第二个 configured source clear write，直接违反这个 per-set-bit clear 行为。

为什么 phase3 没命中：

`F-0060` 只说 `clear_index_en/clear_index_d` 依赖外部 sequencing，可能 double-clearing 或 missing clear。phase3 读到 `/home/smy/opentitan/hw/ip/dma/rtl/dma.sv:683-686` 初始化 index、`:694-724` 跳过或发送 clear source、`:727-740` 在边界内递增，于是判 false alarm。这个判断反驳了 generic bounds/sequencing claim，但没有检查“grant/response 与 index advance 同周期”的具体时间线。

漏检原因：

| 方面 | 具体原因 |
| --- | --- |
| finding 问题 | finding 只弱描述 `clear_index`/missing clear，没有说明同周期 response race。 |
| ref 问题 | ref 是强的，但只说“每个 bit 应发 clear write”，没有直接暴露同周期 response ordering。 |
| reasoning 问题 | phase3 验证了 steady-state bounds 和 mutual exclusion，却没有建立 `DmaClearIntrSrc -> DmaWaitClearIntrSrcRsp -> response same cycle -> clear_index_d/en` 的 cycle-level timeline。 |

改进方向：

需要要求 phase3 对 handshake/rsp 类 finding 显式画 cycle timeline，尤其是 request grant、response valid、index update 三者同周期/相邻周期的优先级。

### DMA `dma_F-0021`: dense FSM / plain flop 而不是 sparse FSM

官方 bug：

`dma.sv:265-268` 将 `ctrl_state_logic` 直接 cast 成 `ctrl_state_q`，底层 state register 是普通 flop/dense binary encoding，而不是 `prim_sparse_fsm_flop`。TODO/comment 已经暗示 FSM hardening 缺口；fault 可以把 state 翻到另一个 valid dense code，从而绕过 invalid-state default。

Finding / ref 情况：

| 项 | 结论 |
| --- | --- |
| `In_AGU_v2` | strong |
| `In_pair_v2` | strong@49/84 |
| `In_Spec` | no |
| `In_ref_raw` | no |
| 最新 phase3 | `F-0044/F-0049/F-0079` 均为 false alarm |

为什么 phase3 没命中：

`F-0049` 已经很接近：它指出 `ctrl_state_q` 没有防御由 `ctrl_state_d` 装载的 illegal state encoding。phase3 反驳时引用 `/home/smy/opentitan/hw/ip/dma/rtl/dma.sv:1038-1041` 的 default branch 会 assert `dma_state_error`，并且 `/home/smy/opentitan/hw/ip/dma/rtl/dma.sv:180-183` 把 `dma_state_error` 接到 fatal alert。因此它认为 illegal encoding 被处理，不是 bug。

这个 reasoning 漏掉了官方 bug 的关键：dense binary FSM 的单比特 fault 不一定进入 illegal encoding，可能直接变成另一个 valid enum state。default invalid-state alert 只能覆盖 undefined encodings，不能覆盖 valid-to-valid transition。没有 `DMA.FSM.SPARSE` spec/ref 约束时，phase3 更容易把 default alert 当成充分 mitigation。

漏检原因：

| 方面 | 具体原因 |
| --- | --- |
| finding 问题 | finding 说到了 state fault hardening，但仍偏向 illegal encoding，而不是 dense valid-to-valid flip。 |
| ref 问题 | DMA raw refs 没有 `FSM.SPARSE` 或 sparse FSM countermeasure 要求，无法从官方 spec 强压这个 bug。 |
| reasoning 问题 | phase3 混淆了 invalid encoding detection 和 dense FSM valid-to-valid fault detection。 |

改进方向：

对 FSM hardening bug，prompt 需要强制区分三类 fault：undefined encoding、valid-to-valid code flip、state flop/control logic transient。不能用 default branch 覆盖 undefined encoding 来反证 dense FSM no-sparse 的所有风险。

## RV_DM 漏检

### RV_DM 022: `prim_alert_sender` 缺少 `SkewCycles(AlertSkewCycles)`

官方 bug：

`rv_dm.sv:160-173` 的 alert sender 实例缺少 reference 中的 `SkewCycles(AlertSkewCycles)` 参数，降低 alert ping/ack skew 容忍。

Finding / ref 情况：

| 项 | 结论 |
| --- | --- |
| `In_AGU_v2` | weak |
| `In_pair_v2` | weak@38/54 |
| `In_Spec` | weak |
| `In_ref_raw` | yes: `rv_dm.hjson_001` |
| 最新 phase3 | 没有 strict hit；SBA alert 相关 confirmed 与该 bug 无关 |

漏检原因：

RV_DM raw ref 只定义 `fatal_fault` alert 语义，没有指定 `prim_alert_sender` 的 `SkewCycles` 参数或 ping-skew handshake 行为。当前 finding 也只是 generic alert sender/open output，`F-0038` 关注 `alert_ack_o/alert_state_o` 未连接，confirmed 的 `F-0025/F-0034/F-0037/F-0038/F-0039/F-0040/F-0043` 都转向 SBA `host_r_other_err` 未接 fatal alert。没有任何输入把 `SkewCycles(AlertSkewCycles)` 精确带到 phase3。

改进方向：

这类 shared primitive parameter bug 需要 ref/raw 中有 primitive-level parameter obligation，或者 AGU 直接比较 reference instance 参数和当前 instance 参数。模块本地 hjson 的 alert 描述太弱。

### RV_DM 034: `dmi_rsp_valid` 丢失 `tlul_resp_pending`

官方 bug：

`rv_dm_dmi_gate.sv:244-245` 中 `dmi_rsp_valid = dmi_rsp_valid_i & dmi_en` 缺少 reference 的 `tlul_resp_pending` OR term。当 `dmi_en` 在 completion pulse 期间下降时，最后一个 pending DMI response 会被 suppress。

Finding / ref 情况：

| 项 | 结论 |
| --- | --- |
| `In_AGU_v2` | no |
| `In_pair_v2` | no |
| `In_Spec` | no |
| `In_ref_raw` | no |
| 最新 phase3 | 没有相关 confirmed |

漏检原因：

这是输入侧彻底缺失。没有 finding 描述 `dmi_rsp_valid_i & dmi_en`、`tlul_resp_pending` 或 final response preservation；raw refs 也只描述 DMI/JTAG/lifecycle gating 高层功能，不描述这个 pending-response expression。`F-0041` 看起来接近，但它是 JTAG-mode `dmi_rsp_ready` gating 的 false alarm，不是 TLUL-DMI gate 的 pending response bug。

改进方向：

需要 AGU 在源码层直接比较 reference expression 或 trace `tlul_resp_pending`，否则 phase3 不会凭空发现这个低层 handshake bug。

### RV_DM 046: stale `lc_hw_debug_clr` authorization

官方 bug：

`rv_dm_dmi_gate.sv:165-178` 的 hardware debug enable latch 只在 `lc_check_byp_en` / `lc_escalate_en` 等路径清除，缺少 `lc_hw_debug_clr` 类行为；当 lifecycle debug permission 回到 Off 后，旧的 debug authorization 仍可能保留。

Finding / ref 情况：

| 项 | 结论 |
| --- | --- |
| `In_AGU_v2` | weak |
| `In_pair_v2` | weak@1/54 |
| `In_Spec` | strong |
| `In_ref_raw` | yes: `rv_dm.hjson_003`, `rv_dm.hjson_028`, `rv_dm.hjson_033`, `rv_dm_sec_cm_testplan.hjson_001` |
| 最新 phase3 | `F-0001` confirmed，但不是该 bug |

phase3 找到的相邻 bug：

`F-0001` confirmed：lifecycle escalation 清掉 latched debug enable 后，TLUL-DMI request-valid path 没有和 `dmi_en` coherently gated。root 是 `/home/smy/opentitan/hw/ip/rv_dm/rtl/rv_dm_dmi_gate.sv:230-245` 的 `dmi_req_valid_o` 直接进 dm_top，以及 `:274-278` strap clear；这不是 `:165-178` 的 stale authorization latch。

漏检原因：

| 方面 | 具体原因 |
| --- | --- |
| finding 问题 | finding 只弱描述 lifecycle/debug gating，没有精确说 `lc_hw_debug_en_i` 返回 Off 后 latch 应清除。 |
| ref 问题 | ref 很强，说明 debug 在非允许 lifecycle states 必须 disabled，但没有自动生成“permission deassert -> clear existing strap/latch”的 obligation。 |
| reasoning 问题 | phase3 聚焦 escalation/check-bypass 和 DMI request gating，没检查普通 lifecycle debug permission Off transition 对 `strap_hw_debug_en_q` 的影响。 |

改进方向：

需要将 lifecycle gating ref 转成动态 property：`lc_hw_debug_en_i` 从 On/True 变为 Off 后，所有 latched debug enables 必须失效，不能只在 escalation/check-bypass 时失效。

### RV_DM 047: NDM reset 期间 live debug authorization 被撤销

官方 bug：

`rv_dm.sv:286-351` 的 NDM reset path 使用 live `lc_hw_debug_en_gated` 形成 `reset_req_en`。当 NDM reset 已经开始后，live debug authorization 被撤销会影响 reset request/ack path；reference 设计本应使用 latched `pinmux_hw_debug_en` 保持 JTAG/debug session 在 NDM reset 期间稳定。

Finding / ref 情况：

| 项 | 结论 |
| --- | --- |
| `In_AGU_v2` | weak |
| `In_pair_v2` | weak@3/54 |
| `In_Spec` | strong |
| `In_ref_raw` | yes: `rv_dm.hjson_005`, `rv_dm.hjson_022`, `rv_dm.hjson_028`, `rv_dm_testplan.hjson_012` |
| 最新 phase3 | NDM reset 相关 finding 均为 false alarm |

强 ref：

`rv_dm.hjson_005` 明确说 `pinmux_hw_debug_en` 是 latched `lc_hw_debug_en`，用于在系统其余部分执行 NDM reset 时保持 JTAG/TAP debug session live。`rv_dm_testplan.hjson_012` 还明确要求 NDM reset 期间可以把 `lc_hw_debug_en` 设成非 On，同时保持 `pinmux_hw_debug_en` On 来维持 JTAG side。

为什么 phase3 没命中：

`F-0003/F-0014/F-0026` 等 NDM reset finding 主要讨论 `lc_rst_asserted` synchronization、`lc_rst_pending_q`、`ndmreset_ack` 等问题。phase3 读到同步器和 pending bits 后判 false alarm。这些判断没有覆盖官方 bug 的精确 claim：`reset_req_en` 是否应该在 NDM reset start 后使用 latched debug authorization，而不是 live `lc_hw_debug_en_gated`。

漏检原因：

| 方面 | 具体原因 |
| --- | --- |
| finding 问题 | finding 只弱触及 reset_req/ack/pending，没有准确描述 live-vs-latched debug enable。 |
| ref 问题 | ref 很强，但需要模型把 `pinmux_hw_debug_en` 的“keep session live”语义映射到 `reset_req_en` 和 NDM reset ack path。 |
| reasoning 问题 | phase3 验证了 reset tracking 同步正确性，但没有检查 authorization source 是否在 reset window 内稳定。 |

改进方向：

对 NDM reset 类 ref，phase3 应建立 reset window：`dmcontrol.ndmreset` assert 后到 deassert/ack 完成前，哪些 gates 必须使用 latched pinmux debug enable，哪些不能跟随 live lifecycle debug enable。

## SOC_DBG_CTRL 命中与漏检

### SOC_DBG_CTRL 024: shared `prim_alert_sender` ping-skew

官方 bug：

`soc_dbg_ctrl.sv:66-79` 的 alert sender loop 存在 shared primitive ping-skew/SkewCycles 类问题；本质是 `prim_alert_sender/prim_diff_decode` 在 skewed ping 下 suppress local sender handshake。

Finding / ref 情况：

| 项 | 结论 |
| --- | --- |
| `In_AGU_v2` | weak |
| `In_pair_v2` | weak@19/44 |
| `In_Spec` | weak |
| `In_ref_raw` | yes: `soc_dbg_ctrl.hjson_001`, `soc_dbg_ctrl.hjson_002` |
| 最新 phase3 | `F-0019` confirmed 了 alert_test bit-order bug，但不是 ping-skew bug |

为什么不是命中：

`F-0019` 的 root 是 `/home/smy/opentitan/hw/ip/soc_dbg_ctrl/rtl/soc_dbg_ctrl.sv:55-60` 的 `alert_test` bit packing 和 `:66-79` loop 中 `alert_test_i(alert_test[i])` 的 fatal/recoverable sender mapping 反了。它确实经过同一个 alert sender loop，但漏洞点是 test bit-order，不是缺少 skew tolerance 或 `SkewCycles(AlertSkewCycles)`。按精确漏洞点标准，不能算 024 命中。

漏检原因：

本地 hjson refs 只说明 fatal/recoverable alert 的存在和触发条件，不能约束 shared primitive ping-skew。AGU/finding 也是 generic alert sender/open output，没有把 missing `SkewCycles` 或 ping-skew handshake 机制带进 phase3。模型自然转向更容易验证的 alert_test packing bug。

### SOC_DBG_CTRL 045: SocDbgStProd policy-copy 精确位置命中，但语义 pair weak

官方 bug：

`soc_dbg_ctrl.sv:202-206` 的 `SocDbgStProd` 分支直接从 shadowed CSR fields 发布 production debug policy，没有用 `lc_rma_state_i/lc_cpu_en_i` 做 lifecycle gating。

本轮命中：

| 项 | 结论 |
| --- | --- |
| `CSBC_v3` | yes |
| Primary confirmed finding | `F-0004` |
| 去重分类 | 非 extra |
| `In_pair_v2` | weak@4/44 |
| 说明 | 同一精确代码位置命中，但语义不是 missing lifecycle gating。 |

confirmed root：

`F-0004/F-0013/F-0027/F-0033` 都确认了同一组 `SocDbgStProd` policy-copy 赋值附近的 relocked invalid-code fail-open：`/home/smy/opentitan/hw/ip/soc_dbg_ctrl/rtl/soc_dbg_ctrl.sv:203` cast category，`:205` copy valid，`:206` copy `DEBUG_POLICY_RELOCKED`。随后 decoder 只把 exact `MuBi4True` 当作 relocked，任何 non-True/invalid relocked encoding 都被解释为 unlocked。

为什么记录为 yes：

之前约定的 `CSBC_v3` 口径是“严格命中或同一精确代码位置算 yes；同一代码 chunk 但不是精确位置算 no”。这里 confirmed root 落在 `soc_dbg_ctrl.sv:203/205/206`，和官方 bug 的 `soc_dbg_ctrl.sv:202-206` 精确赋值位置一致，因此填 `yes`。

为什么 `In_pair_v2` 仍是 weak：

送入 phase3 的 finding 主要描述 `SocDbgStProd` 下非法/未初始化 category/valid/relocked 传播、relocked fail-open、trace/decoder 问题；没有准确描述“缺少 `lc_rma_state_i/lc_cpu_en_i` lifecycle gating”。因此它能作为同位置弱配对，但不是语义上的 strong pair。

## 三模块综合结论

### 漏检类型归类

| 类型 | 对应 bug | 特征 | 当前机制问题 |
| --- | --- | --- | --- |
| weak finding + strong ref，但缺少 sequence obligation | DMA 007, DMA 032, RV_DM 046, RV_DM 047 | ref 能说明正确行为，但 finding 没有给出精确状态/时序路径 | phase3 挖到相邻 bug 或验证局部 false alarm 后，没有从 ref 独立生成源码级时序检查。 |
| strong/weak finding 命中同区域，但 fault model 错位 | DMA `dma_F-0021` | finding 说 illegal encoding，官方 bug 是 dense valid-to-valid flip | phase3 用 invalid-state default alert 反驳了 sparse FSM hardening bug。 |
| no finding + no ref | RV_DM 034 | 输入侧没有相关描述 | phase3 没有任何抓手，除非源码级 AGU 直接发现 expression diff。 |
| weak alert ref + shared primitive bug | RV_DM 022, SOC_DBG_CTRL 024 | hjson 只定义 alert 功能，不定义 primitive skew 参数 | ref/raw 不包含 shared primitive contract，phase3 会转向更容易验证的 alert 邻近 bug。 |
| exact location hit but semantic mismatch | SOC_DBG_CTRL 045 | confirmed root 与官方 bug 同一赋值行，但不是同一 gating 根因 | `CSBC_v3` 可以按同位置记 yes，但 pair/detail 必须标 weak，避免误读为语义完全命中。 |

### 对 ref 补充机制的判断

这三模块里 ref 机制有实际作用，但主要体现在“发现新 bug”而不是“覆盖原 known bug”：

| 模块 | ref 起到的作用 | 对 known bug 覆盖的影响 |
| --- | --- | --- |
| DMA | `dma_testplan.hjson_005/006` 直接驱动了 TLUL d_error status/alert mismatch 的 extra confirmed。 | 没有覆盖 DMA 007/032/F-0021 的精确 bug；DMA 007/032 需要更强 sequence obligation。 |
| RV_DM | `rv_dm_testplan.hjson_008` 和 `rv_dm.hjson_001` 驱动了 SBA `host_r_other_err` missing fatal alert 的多个 extra confirmed。 | 没有覆盖 022/034/046/047；046/047 的 strong refs 没有被转成对应 latch/reset-window property。 |
| SOC_DBG_CTRL | alert refs 帮助确认 alert_test bit-order；policy refs帮助定位 relocked fail-open。 | 024 未覆盖；045 因同一精确赋值位置计为 hit，但不是 lifecycle gating 语义命中。 |

### Prompt / 流程改进建议

1. 对 strong ref 要求模型显式生成一条 temporal/source obligation，而不是只在 finding claim 被否后简单说 ref adjacent。DMA 007/032、RV_DM 046/047 都属于这个问题。

2. 对 FSM hardening 类问题要求区分 invalid encoding 和 valid-to-valid dense flip。DMA `dma_F-0021` 不能用 default invalid-state alert 直接反证。

3. 对 reset/lifecycle latch 类问题要求建立 window：permission deassert、NDM reset start、pending response completion 等事件之间哪些 signal 必须 latched，哪些可以 live。

4. 对 shared primitive parameter bug，需要在 ref extraction 中引入 primitive-level contract 或 reference instance 参数对比；模块 hjson 的 alert 描述不够。

5. 覆盖统计继续保留两层口径：`CSBC_v3` 按精确位置/根因；`In_pair_v2` 按 finding 是否准确描述 bug。SOC_DBG_CTRL 045 说明这两列不能合并，否则会把“同位置不同语义”的 hit 误读成 strong semantic hit。

## KMAC/TLUL 最新 phase3 命中与漏检分析

本轮输出文件：

| 模块 | 输出 | finding 数 | CONFIRMED | FALSE_ALARM |
| --- | --- | ---: | ---: | ---: |
| KMAC shard1 | `rtl_bug_agent/output/phase3_kmac_shard1_rerun_gpt55_xhigh_dualclaim.json` | 53 | 7 | 46 |
| KMAC shard2 | `rtl_bug_agent/output/phase3_kmac_shard2_rerun_gpt55_xhigh_dualclaim.json` | 53 | 10 | 43 |
| KMAC shard3 | `rtl_bug_agent/output/phase3_kmac_shard3_rerun_gpt55_xhigh_dualclaim.json` | 53 | 6 | 47 |
| TLUL | `rtl_bug_agent/output/phase3_tlul_rerun_gpt55_xhigh_dualclaim.json` | 38 | 14 | 24 |

### KMAC 命中统计

KMAC finding-level confirmed 共 23 条：

| 类型 | 数量 | 说明 |
| --- | ---: | --- |
| 非 extra | 5 | `F-0019`, `F-0080`, `F-0086`, `F-0101`, `F-0155` |
| extra from ref | 6 | `F-0014`, `F-0061`, `F-0116`, `F-0120`, `F-0152`, `F-0158` |
| 纯 extra | 12 | 其余 confirmed extra，主要来自 same-path 源码追踪而非 ref |

按 confirmed root 去重后，本轮 KMAC 找到 3 类真实问题：

| root | 代表 finding | 类型 | 是否覆盖表内已知 bug |
| --- | --- | --- | --- |
| `kmac.sv:906-927` static_mask 代替 entropy `msg_mask` | `F-0019`, `F-0101` | 非 extra；另有 ref-driven extra | yes，覆盖 KMAC 017 和 `kmac_F-0039` |
| `kmac.sv:680-687` `event_error` 遗漏 `msgfifo_err.valid` | `F-0008`, `F-0102`, `F-0120` | mostly pure extra / ref extra | no，表内没有对应 known bug |
| `kmac.sv:1006-1034` MSG_FIFO `tlul_adapter_sram.intg_error_o` 未连接 | `F-0155`, `F-0116` | 非 extra + ref extra | no，表内没有对应 known bug |

KMAC 表内 bug 覆盖：

| Bug | CSBC_v3 | Primary finding | 结论 |
| --- | --- | --- | --- |
| KMAC 017 | yes | `F-0019` rank 19/159 | 精确命中 static_mask/msg_mask 根因。 |
| KMAC 021 | no | none | 没有 ping-skew/SkewCycles finding；只有 generic alert sender/open-output。 |
| KMAC 036 | no | `F-0044` rank 44/159 | finding 精确描述 100-cycle gate，但 phase3 判 false alarm。 |
| KMAC `kmac_N-005` | no | `F-0062` rank 62/159 | 只有 EnMasking/NumShares 弱 pair，没有直接描述 unconditional `u_msg_unpacker_share1` 越界。 |
| KMAC `kmac_F-0039` | yes | `F-0019` rank 19/159 | 与 KMAC 017 同一 static_mask 根因，精确命中。 |

### KMAC 漏检原因

#### KMAC 021: shared `prim_alert_sender` ping-skew

官方 bug 是 `kmac.sv:1466-1480` shared `prim_alert_sender/prim_diff_decode` ping-skew/SkewCycles 类问题。

| 方面 | 结论 |
| --- | --- |
| finding | 本轮没有任何 finding 提到 `SkewCycles`、`AlertSkewCycles`、ping skew 或 `prim_diff_decode`。最近的是 `F-0131`，只说 `alert_ack_o/alert_state_o` 未连接。 |
| ref | `kmac.hjson_005` 只说明 fatal alert 覆盖 KMAC 内部 fatal faults；它不描述 shared primitive skew contract。 |
| reasoning | phase3 没有源码抓手去比较 shared primitive 参数或 ping handshake，所以没有命中。 |

这个漏检主要是 finding/ref 覆盖不足，不是 phase3 已看到准确信息后推理失败。

#### KMAC 036: `StTerminalError` 100-cycle `sparse_fsm_error_o` gate

官方 bug 是 `kmac_core.sv:227-236` 在 `StTerminalError` 下用 `st_err_ct < 100` suppress `sparse_fsm_error_o`。

| 方面 | 结论 |
| --- | --- |
| finding | `F-0044` rank 44/159 已经准确描述：`sparse_fsm_error_o` 在 `StTerminalError` 下保持低直到 `st_err_ct` 到 100。 |
| ref | raw refs 只有 weak 支持：`kmac.hjson_005` 说 internal FSM invalid state 触发 fatal fault，`kmac.hjson_010/011` 说 escalation/local fatal faults move sparse FSMs into invalid state；没有明确禁止 100-cycle delay。 |
| phase3 reasoning | phase3 用 `kmac_core.sv:238-242` default illegal-state immediate pulse 和 `kmac.sv:1457-1464/1485-1493` top-level fatal latches 反驳，认为运行中 invalid-state transition 不会静默丢失。 |

这个漏检更像 phase3 reasoning/安全语义判断问题：它看到了准确 finding，但接受了“进入 terminal 前已有一次 immediate pulse/latch”这个反证，没有把 `StTerminalError` 内部 100-cycle low window 本身当作漏洞。

#### KMAC `kmac_N-005`: `u_msg_unpacker_share1` unconditional instantiation

官方 bug 是 `kmac_reduced.sv:123-139` 在 `EnMasking=0` / `NumShares=1` 配置下仍无条件实例化 share1 unpacker，访问 `msg_i[1]`。

| 方面 | 结论 |
| --- | --- |
| finding | 最近的是 `F-0062`，只说 `EnMasking=0` 应推出 `NumShares=1`；`F-0046` 提到 `msg_i[1]` handshake，但不是参数越界。没有 finding 同时描述 `NumShares=1` 和 unconditional `u_msg_unpacker_share1`。 |
| ref | `theory_of_operation.md_021`、`theory_of_operation.md_031`、`kmac.hjson_037` 支持 unmasked/one-share 行为，但不约束 `kmac_reduced` 的 generate 结构。 |
| reasoning | phase3 在 `F-0062` 中只验证了 `NumShares = EnMasking ? 2 : 1` localparam，未继续检查 `kmac_reduced.sv:123-139` 的 share1 instance 是否也被同一条件保护。 |

这个漏检主要是 finding 不够精确，加上 ref 只提供模式语义，没有生成“所有 share1 结构必须随 NumShares 条件化”的源码 obligation。

### TLUL 命中统计

TLUL finding-level confirmed 共 14 条：

| 类型 | 数量 | 说明 |
| --- | ---: | --- |
| 非 extra | 2 | `F-0024`, `F-0027` |
| extra from ref | 0 | 本轮 TLUL confirmed 没有 ref-driven extra |
| 纯 extra | 12 | 大多是从 same-path 源码追踪出的 `tlul_bug_001/002` 真实路径 |

按已知 bug 去重后，TLUL 2/2 命中：

| Bug | CSBC_v3 | Primary finding | 类型 | 结论 |
| --- | --- | --- | --- | --- |
| TLUL 028 | yes | `F-0027` rank 27/38 | 非 extra | 精确确认 `tlul_fifo_sync.sv:57-82` 的 `tlul_bug_001` request FIFO corruption path。 |
| TLUL 029 | yes | `F-0011` rank 11/38 | 纯 extra | 原 claim 说 late_error 未接入 rdata 是错的，但 phase3 顺着同一路径确认 `rdata` 被置全 1 而 `d_error` 未置位。 |

TLUL ref 对命中帮助有限。TLUL 028 的 raw refs 只有 `TlulProtocolChecker.md_012/014/015` 这类 A-channel knownness/handshake 约束，TLUL 029 的 raw refs 是 `TlulProtocolChecker.md_016/023/024` 这类 response/data/error knownness 约束；它们支持相关协议背景，但 confirmed 主要来自 finding/source path，而不是 ref 独立驱动。

### 本轮结论

KMAC 的 phase3 对容易源码追踪的 static_mask、error aggregation、adapter integrity 输出断开表现较好，但对 shared primitive parameter bug、terminal-state delay 语义、parameterized structural OOB 仍不足。KMAC 036 说明“强 finding + weak ref”时，LLM 可能因为找到相邻 latch/默认分支反证而错过官方漏洞语义。

TLUL 本轮覆盖很好，两个已知 bug 都被精确代码位置命中。需要注意 TLUL 029 是从错误 literal claim 旁边挖出的 pure extra；如果统计“finding 是否准确描述 bug”，它仍应记录为 strong pair，因为 `F-0011` 指向同一 late_error/rdata 路径并引导出了精确 bug，但 confirmed verdict 的形式是 extra。
