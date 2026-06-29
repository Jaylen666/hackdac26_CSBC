# Formal CSBC v2.0 - Keymgr 真实测试审计报告

**日期**: 2026-06-27
**测试模块**: keymgr (OpenTitan Key Manager)
**框架版本**: Formal CSBC v2.0 (channel B semantic + channel F + solver)
**分析依据**: 本框架自身的输出 —— `specs_keymgr/`、`trace_keymgr.jsonl`、`findings_keymgr.json`、`semantic_ag_shadow_keymgr.json`

> 说明：本报告**完全基于本 formal 框架的实际运行产物**做逆向追溯。bug 的描述取自
> `bug_comparison_table_csbc_v2.csv`，但该表中的 In_AGU / In_pair_v2 等列是**旧框架**
> 的评估结果，与本框架无关，本次分析不引用它们作为任何判断依据。

---

## 一、运行管线与产物概览

| 阶段 | 产物 | 数量 |
|------|------|------|
| Chunk → Spec | `specs_keymgr/*.json` | 95 个 chunk spec |
| Atom 抽取 | trace `stage=atom` | 565 atoms（assumption / guarantee / uncertain） |
| Semantic AG 配对 | `semantic_ag_shadow_keymgr.json` | 721 atoms → **1058 pairs**（min_score=0.66, dense 0.8 / signal 0.2, exclude_same_spec=true） |
| Channel B | trace `stage=channel_b` | 342 query units，其中 **33 个 atom 判为 CONTRADICTION** |
| Channel F | trace `stage=channel_f` | 51 findings（3 PENDING, 33 gated out） |
| Fusion 聚类 | trace `stage=pair` | **109 个 cluster → 排序输出 56 findings** |
| Formal solver | findings `formal_result` | 26 PENDING 全部执行，verdict 全 FAIL |

**关键管线事实**：Channel B 在 342 个查询单元中只判出 **33 个 CONTRADICTION**，其余多为
GAP/UNCERTAIN。这 33 个 CONTRADICTION 经过 fusion 的**贪心信号聚类**，连同大量 GAP
一起被并入 109 个 cluster。**聚类阶段是本次所有 bug 丢失的共同环节。**

---

## 二、Fusion 聚类机制（丢失根因所在）

`fusion.py:_cluster_findings` 的合并条件（满足任一即并入同簇）：

```
- 共享 ≥1 个完全相同的信号名，OR
- 共享 ≥2 个 spec，OR
- 模糊信号重叠：两信号名存在任意 ≥5 字符公共子串（_fuzzy_signal_overlap）
```

合并后 `_merge_cluster` 选取簇内**verdict 最强、contradiction 文本最长**者作为代表，
其余 finding 的语义被丢弃（只保留信号/spec 的并集）。

这套规则在 keymgr 上产生了**巨型贪心簇**：

- `key_state_q`、`state_q`、`state_d` 等信号在 keymgr_ctrl 的几十个 chunk 中反复出现；
- 模糊匹配让 `key_state_q` / `key_state_d` / `vld_state_change_q` 因公共子串 `state`
  互相吸附；
- 结果：109 个 cluster 中有 **21 个含 8 个 spec 来源**（最大簇规模），代表文本只能体现其中一条。

**量化证据**（最终 56 findings 中信号的存活情况）：

| 信号 | 出现在几个最终 finding | 说明 |
|------|----------------------|------|
| `key_state_ecc_q` | **0** | N-003 核心信号，**彻底消失** |
| `key_state_q` | 1（F-0002，8-spec 巨簇） | 被 Wipe 语义顶替 |
| `data_en` | 0 | 031 信号消失 |
| `keys_en` | 1 | 026 信号，但簇代表讲 adv_dvalid |

---

## 三、Bug-by-Bug 逆向追溯

### Bug N-003: key_state_ecc_q 更新但 key_state_q 恒零 → 假阳性 ecc_errs

**位置**: `keymgr_ctrl.sv:303-326`（encoder 在 304-318，decoder 在 321-334）

#### 框架逐阶段命运

| 阶段 | 结果 | 证据 |
|------|------|------|
| Chunk | ✅ encoder 与 decoder 各自成块 | `keymgr_ctrl__always_ff__line_304__001`、`keymgr_ctrl__generate_for__gen_ecc_loop_cdi__001` |
| Spec | ✅✅ **精确命中** | encoder spec 的 **U1(high)**：“key_state_q is reset to zero but **never updated after reset** … causing incorrect key usage and potential security compromise”；decoder spec 的 **A1**：“{key_state_ecc_q, key_state_q} **must be a valid SECDED codeword**” |
| Atom | ✅ 抽出 assumption / uncertain，formalizability=direct | trace `stage=atom` |
| Semantic AG | ✅ 形成配对（min_score≥0.66 通过） | — |
| **Channel B** | ✅✅ **判定 CONTRADICTION + 生成 SVA(PENDING)** | encoder `uncertain::0` → CONTRADICTION；decoder `assumption::0` → CONTRADICTION。两个 atom 都中了 |
| **Fusion** | ❌❌ **被拆散吞并** | encoder atom 被并入 **F-0001**（讲 lc_keymgr_en 生命周期）；decoder atom 被并入 **F-0021**（讲 cdi_sel 随机绑定）。两个本该配成一对的矛盾被分进两个无关巨簇 |
| 最终 finding | ❌ 无任何 finding 指向 N-003 | `key_state_ecc_q` 在 56 findings 中出现 0 次 |
| Formal | ⚠️ SVA 实际跑了但挂在错簇上 | F-0001/F-0021 的 formal=PENDING→solver=FAIL，但 finding 文本与 N-003 无关 |

#### 结论
1. **直接指向？否。** 没有一个最终 finding 描述 N-003。
2. **丢失环节：fusion 聚类（不是 spec、不是 channel_b）。** 这是最反直觉也最重要的发现——
   框架在 **spec 和 channel_b 两个阶段都成功、精确地抓到了 N-003 的双向矛盾**
   （encoder 不更新 data + decoder 要求 data 一致），SVA 也生成并被 solver 判 FAIL。
   但 fusion 的贪心信号聚类把这对矛盾**拆散并吞进了两个语义无关的巨簇**，代表文本选择
   规则（最长文本 / 最强 verdict）让 N-003 的描述被覆盖丢弃。
3. **可捕获性**：spec ✅ / atom ✅ / 配对 ✅ / channel_b ✅ / **fusion ❌** / formal（错挂）。
   **唯一失败点是 fusion。**

---

### Bug 031: data-enable FSM 非法稀疏编码被静默重定向

**位置**: data-enable FSM sparse encoding redirect path（`keymgr_data_en_state.sv`）

#### 框架逐阶段命运

| 阶段 | 结果 | 证据 |
|------|------|------|
| Chunk | ✅ FSM 区块成块 | `keymgr_data_en_state__continuous_region__int__001`、`__always_comb__line_78__001` |
| Spec | ⚠️ 行为描述有偏差 | spec G2 写的是 PRIM_FLOP_SPARSE_FSM “force state_q to **StCtrlDataIdle**”，而真实 bug 是重定向到 **StCtrlDataDis**。U1/U2 提到“error 机制不可见 / 静默 restart”，**未点中 redirect-to-Dis 而非 fsm_err 这一核心** |
| **Channel B** | ✅ `int__001::uncertain::1` 判为 **CONTRADICTION + SVA** | trace 确认 |
| **Fusion** | ❌ 被吞并 | `__always_comb__line_78__001` 的 atom 被并入 **F-0002**（8-spec 巨簇，讲 KeyUpdateWipe）；`int__001` chunk **未被任何最终 finding 引用**（其 spec 反查为空） |
| 最终 finding | ❌ 无 finding 涉及 data_en / fsm_err | — |

#### 结论
1. **直接指向？否。**
2. **丢失环节：spec 语义偏差 + fusion 吞并（双重损失）。**
   - spec 阶段已偏：把 sparse-FSM 错误处理理解成通用“回 Idle”，没有展开 `PRIM_FLOP_SPARSE_FSM`
     宏的 ErrorState 参数（=StCtrlDataDis），所以即便 channel_b 判了 CONTRADICTION，
     其矛盾点也不是“应触发 fsm_err”的那条。
   - 即使如此，channel_b 仍产出了一个 CONTRADICTION atom，但**又一次在 fusion 被并入
     F-0002 巨簇**而消失。
3. **可捕获性**：chunk ✅ / spec ⚠️（行为偏差）/ channel_b ✅（但矛盾点不精确）/ **fusion ❌**。

---

### Bug 026: StCtrlInvalid 状态暴露未掩码 key_state_q

**位置**: `keymgr_ctrl.sv` invalid-stage raw key output path

#### 框架逐阶段命运

| 阶段 | 结果 | 证据 |
|------|------|------|
| Chunk | ✅ | `keymgr_ctrl__always_comb__key_output_ctrl__001` |
| Spec | ✅✅ **精确命中** | A2：“must **avoid** the simultaneous condition where state_q == StCtrlInvalid and stage_sel_o invalid while key output enabled”；G2：“if invalid_stage_sel_o and state_q == StCtrlInvalid, then **key_state_q[cdi_sel_o][i] (unmasked)**” |
| **Channel B** | ⚠️ 判为 **UNCERTAIN**（非 CONTRADICTION） | `key_output_ctrl::assumption::1` → UNCERTAIN + SVA(NAME_UNVERIFIED)；其余 atom → GAP |
| **Fusion** | ❌ 分散吞并 | 该 chunk 的 spec 出现在 F-0003/F-0006/F-0010/F-0011/F-0018/F-0021/F-0029/F-0030 等 8+ 个簇，但每个簇代表文本都讲别的（msb_extend、adv_dvalid、kmac_masking…） |
| 最终 finding | ❌ 无 finding 描述 invalid-stage key leak | `keys_en` 仅存活于 1 个簇但代表讲 adv_dvalid |

#### 结论
1. **直接指向？否。**
2. **丢失环节：channel_b 判级偏弱 + fusion 分散。**
   spec 完美捕获了 026（A2 否定式 + G2 肯定式正好构成矛盾），但 channel_b 只判了
   **UNCERTAIN** 而非 CONTRADICTION ——很可能是因为 assumption 是**否定式**（“must avoid X”）
   而 guarantee 是**肯定式**（“if X then unmasked”），LLM 未识别出这正是同一条件 X 的
   正反两面。UNCERTAIN 强度仅 0.35，随后又在 fusion 被分散进多个无关簇。
3. **可捕获性**：chunk ✅ / spec ✅✅ / channel_b ⚠️（误判为 UNCERTAIN）/ **fusion ❌**。

---

### Bug extra: flash-seed validity 检查错位到不消费它的 stage

**位置**: `keymgr.sv:445-467`

#### 框架逐阶段命运

| 阶段 | 结果 | 证据 |
|------|------|------|
| Chunk | ✅ seed 相关逻辑成块 | — |
| Spec | ⚠️ 抓到 seed validity，但未抓到“错位”语义 | F-0005 来源 spec 涉及 `creator_seed_vld` / `owner_seed_vld` / `UseOtpSeedsInsteadOfFlash`，描述的是 **OTP vs Flash 条件分支**，不是“validity 检查被 shift 到错误 stage” |
| Channel B | ⚠️ 形成 F-0005（CONTRADICTION, score=0.537） | 但语义是 OTP seed 场景 |
| Fusion | — 进入最终 finding 但语义偏移 | F-0005 / F-0010（stage_sel 越界） |
| Formal | ❌ F-0005 = NAME_UNVERIFIED 未运行 | SVA 含无法解析信号名 |

#### 结论
1. **直接指向？否**（F-0005 信号相关但语义是 OTP 分支，非 stage 错位）。
2. **丢失环节：spec 阶段。** “validity 检查被错位到不消费它的 stage”需要理解
   **stage 序列与 seed 消费关系的跨位置映射**，超出单 chunk 局部视野，spec 阶段
   根本没有形成这条命题，后续环节自然无从配对。
3. **可捕获性**：chunk ✅ / **spec ❌（命题缺失）** / 之后 N/A。

---

## 四、跨 bug 的统一结论

### 4.1 丢失环节归类

| Bug | spec 抓到？ | channel_b 抓到矛盾？ | 真正丢失环节 |
|-----|------------|--------------------|------------|
| **N-003** | ✅✅ 精确(U1+A1) | ✅✅ 双 atom CONTRADICTION+SVA | **fusion 聚类**（拆散+吞并） |
| **026** | ✅✅ 精确(A2+G2) | ⚠️ 误判 UNCERTAIN | **channel_b 判级** + fusion 分散 |
| **031** | ⚠️ 行为偏差(Idle≠Dis) | ✅ 一个 CONTRADICTION | **spec 偏差** + fusion 吞并 |
| **extra** | ❌ 命题缺失 | — | **spec 阶段**（局部视野） |

### 4.2 最重要的发现：**瓶颈在 fusion，不在 spec 或 formal**

这是本次真实测试最反直觉、也最有价值的 insight：

- **框架的"前端"很强**。spec 提取在 3/4 的 bug 上给出了精确或接近精确的命题
  （N-003 的 U1、026 的 A2/G2 几乎是教科书级的 bug 描述）；channel_b 在 N-003 和 031
  上甚至直接判出了 CONTRADICTION 并生成了可被 solver 证伪的 SVA。
- **框架的"后端"也能用**。26 个 PENDING SVA 全部成功在 sby+z3 上执行，全 FAIL。
- **真正的失败在中间的 fusion 聚类**。`_cluster_findings` 的"共享≥1信号 / 模糊5字符
  子串"规则在 keymgr 这种**信号名高度同源**（key_state_*, state_*, *_en, *_q/d）的模块上
  形成了巨型贪心簇；`_merge_cluster` 的"取最长文本/最强 verdict 为代表"又让真正精确的
  bug 描述被无关的长文本覆盖。**N-003 这个 channel_b 已经判定 CONTRADICTION 的 bug，
  就是在这一步被拆进 F-0001 和 F-0021 两个无关簇而彻底消失的。**

### 4.3 次要瓶颈

1. **channel_b 对"否定式 assumption vs 肯定式 guarantee"识别不足**（026 误判 UNCERTAIN）：
   "must avoid X" 与 "if X then unsafe" 是同一条件的正反面，LLM 未识别为矛盾。
2. **spec 对宏/库语义不展开**（031）：`PRIM_FLOP_SPARSE_FSM` 的 ErrorState 参数在 chunk
   截取范围外，spec 用通用理解填充成"回 Idle"，偏离真实的"重定向到 Dis"。
3. **spec 对跨位置时序关系无能为力**（extra）：stage 序列与 seed 消费的映射超出单 chunk。

---

## 五、改进建议（按对真实丢失的针对性排序）

### P0 — 修 fusion 聚类（直接挽回 N-003，最高 ROI）
1. **收紧聚类条件**：
   - 取消"共享 1 个信号即合并"，改为要求**多信号重叠或共享 spec**；
   - 删除或大幅收紧 `_fuzzy_signal_overlap`（5 字符子串过于激进，`state`/`key_s` 会误合）；
   - 对 `key_state_q` 这类高频信号建立**停用词表**，不作为聚类键。
2. **保护 channel_b 已判 CONTRADICTION 的 atom**：CONTRADICTION + 有 SVA 的 finding
   应**优先单独成簇**或作为簇代表，禁止被 GAP/UNCERTAIN 的长文本顶替。
3. **合并代表选择改为语义优先**：不要单纯"取最长 contradiction 文本"，而应优先保留
   verdict 最强且 formal 可证（solver=FAIL）的那条描述。

### P1 — 修 channel_b 判级（挽回 026）
4. **prompt 增加逻辑对立性提示**：显式要求识别"assumption 说 must-not-X / guarantee 说
   does-X"这类正反矛盾，避免误判 UNCERTAIN。

### P2 — 修 spec 前端（挽回 031 / extra）
5. **宏实例化参数随 chunk 一起喂给 spec**（`PRIM_FLOP_SPARSE_FSM` 的 ErrorState 等）。
6. **引入跨 chunk 时序/序列分析通道**，处理 extra 这类 stage 映射类 bug。

---

## 六、最终总结

- **精确捕获 0/4**，但根因分布很不一样，**不能一概而论为"框架弱"**：
  - **N-003 是被 fusion 误杀的**——前端（spec+channel_b+SVA）全部成功，纯属聚类问题，**修
    fusion 即可挽回**；
  - **026 是 channel_b 判级偏弱**（spec 已完美）；
  - **031 是 spec 行为偏差 + fusion 吞并**；
  - **extra 是 spec 命题缺失**（真正的能力边界）。
- **本框架真正的能力远比"0/4"看起来强**：在 N-003 上，spec 给出了 U1（key_state_q never
  updated）和 A1（ECC+data must be consistent）这对教科书级矛盾，channel_b 判了
  CONTRADICTION，SVA 被 solver 判 FAIL——**完整的发现链条已经跑通，只是最后被 fusion 截断**。
- **最高优先级、最高 ROI 的改进是收紧 fusion 聚类**：keymgr 信号名高度同源，现有的
  "共享 1 信号 / 模糊子串"贪心聚类必然产生巨型簇并吞掉精确 finding。仅此一项修复，
  预计可直接救回 N-003，并显著改善 026/031 的存活。
- **formal solver 与已知 bug 脱节的真相**：26 个 FAIL 不是没价值，而是它们挂在了**被
  fusion 打乱后的错簇**上（如 N-003 的 SVA 挂在 F-0001/F-0021）。修好 fusion 后，
  这些 solver 证据才能正确归属到对应 bug。

---

**审计完成**: 2026-06-27 · Claude Opus 4.8
**核心动作项**: 优先重构 `fusion.py` 的 `_cluster_findings` / `_merge_cluster`，
再以 keymgr 复跑验证 N-003 能否作为独立高分 finding 浮现。
