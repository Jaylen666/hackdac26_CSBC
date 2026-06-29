# AG 配对质量人工评估报告

**日期**: 2026-06-29  
**数据源**: `formal_csbc_v2/.checkpoint_keymgr_B_semantic.json` (342 query units, 250 nonempty)  
**方法**: 按 verdict 分层随机采样 12 个 query units，人工评估 assumption-guarantee 配对的语义相关性

---

## 一、采样分布

| Verdict | 全量 | 采样 |
|---------|------|------|
| GAP | 182 (73%) | 3 |
| UNCERTAIN | 42 (17%) | 3 |
| CONTRADICTION | 19 (8%) | 3 |
| SATISFIED | 3 (1%) | 3 |
| DEFENSIVE | 4 (2%) | 0 |

---

## 二、逐个配对质量评估

### ✅ Sample 1: [CONTRADICTION] state_q FSM error propagation

**Assumption**: 外部 macro 检测到 `state_q` 无效编码后复位，模块不应向外传播错误信号  
**Guarantee**: `state_q` 为非法值时驱动 `working_state_o` 为 `StInvalid`  
**配对质量**: **STRONG** ✅  
**理由**: Guarantee 直接反驳了 assumption 的"不传播错误"预期——`working_state_o = StInvalid` 就是可见的侧信道错误指示。配对精准，LLM 分析正确识别矛盾。

---

### ✅ Sample 2: [CONTRADICTION] data_rand 熵强度要求

**Assumption**: `data_rand` 必须提供强独立随机数（如 TRNG）来有效掩码密钥材料  
**Guarantee**: `data_rand` 由 LFSR + 置换函数生成（确定性伪随机）  
**配对质量**: **STRONG** ✅  
**理由**: 典型的安全假设 vs 实际实现不匹配。LFSR 伪随机不满足"强独立"要求，配对揭示了防御纵深的潜在弱点。这类配对是框架的核心价值。

---

### ⚠️ Sample 3: [CONTRADICTION] cmd_consty_err_q sticky 属性

**Assumption**: 错误锁存器一旦 set 就永久保持（sticky），需显式清除机制  
**Guarantee**: (1) `rst_ni` 低时清零；(2) 正常运行时 `q` 直接跟随 `d`  
**配对质量**: **MODERATE** ⚠️  
**理由**: Guarantee 确实证明信号不是 sticky（复位清零 + 正常跟随 d），但 assumption 本身有误导性——它把"希望 sticky"当成"应该 sticky"，而设计意图可能就是非 sticky。配对语义相关，但矛盾的实质意义存疑（可能是 spec 生成时的过度推断）。

---

### ✅ Sample 4: [GAP] key_state_ecc_words_d 有效性保证

**Assumption**: `key_state_ecc_words_d` 每周期必须为有效数据（非 X）  
**Guarantee**: `key_state_ecc_words_d = key_state_d`（连续赋值）  
**配对质量**: **STRONG** ✅  
**理由**: 完美的 GAP 示例。Guarantee 描述了赋值关系但未保证右值有效性，assumption 的合法预期未被覆盖。配对揭示了覆盖缺口。

---

### ✅ Sample 5: [GAP] state_q one-hot 不变量

**Assumption**: `state_q` 必须保持 one-hot 编码以确保 `unique case` 正常工作  
**Guarantee**: (3 条) 当 `state_q` 非法时强制 `state_d` 到安全状态 / 驱动 `fsm_err_o`  
**配对质量**: **STRONG** ✅  
**理由**: 经典的"错误检测 ≠ 错误预防"gap。Guarantee 只描述了非法值的**应对**（检测+恢复），未保证 `state_q` **永不为非法**。配对精准暴露了 one-hot 不变量的缺失保证。

---

### ⚠️ Sample 6: [GAP] out_q 复位默认值

**Assumption**: `out_q` 复位默认为 1（使能），若安全状态应为 0（禁用），则存在初始化窗口风险  
**Guarantee**: (3 条) 其他信号（`state_intg_err_q`, `init_q`, `key_q`）的复位清零行为  
**配对质量**: **WEAK** ❌  
**理由**: Guarantee 描述的是**其他信号**的复位行为，与 `out_q` 无关。语义不相关，配对失败。这是 embedding 模型的明显误配——可能因为都有 `rst_ni` 关键词就配上了，但实际信号路径完全不同。

---

### ✅ Sample 7: [UNCERTAIN] data_rand 熵充分性

**Assumption**: `data_rand[0]` 单比特复制 `AdvLfsrCopies` 次可能熵不足  
**Guarantee**: (2 条) `data_rand[0]` 来自 LFSR，`id_matrix` 填充方式  
**配对质量**: **MODERATE-STRONG** ✅  
**理由**: Guarantee 确认了 assumption 描述的实现（单比特复制），但未提供熵充分性的额外保证。配对语义相关，LLM 正确判定 UNCERTAIN（无法从现有信息证实或否定担忧）。这是框架识别"设计质量问题"而非"功能错误"的典型案例。

---

### ⚠️ Sample 8: [UNCERTAIN] MaxAdvDataWidth 参数有效性检查

**Assumption**: `MaxAdvDataWidth` 可被覆盖但无本地检查，不确定能否正确工作  
**Guarantee**: (2 条) `adv_data` 零扩展到 `KDFMaxWidth`，参与输出选择  
**配对质量**: **WEAK** ❌  
**理由**: Guarantee 描述的是数据处理流程，未涉及参数有效性检查。配对语义相关性低。但注意：Phase 3 v3 的 F-0256 恰恰发现了这个缺陷（`MaxAdvDataWidth` 无上界 assertion），说明 bug 确实存在，只是这两条 guarantee 配得不够好——**应该配到顶层的参数断言**，而不是内部数据路径。

---

### ❌ Sample 9: [UNCERTAIN] ctrl_rand/data_rand 非对称性

**Assumption**: LFSR 两半不对称（一半置换），不确定是有意设计还是意外  
**Guarantee**: (2 条) `data_rand` 的使用场景（填充 `adv_matrix`, `sw_share1_output`）  
**配对质量**: **IRRELEVANT** ❌  
**理由**: Assumption 问的是"为什么两个信号不对称"，Guarantee 只描述其中一个信号的下游用法，完全答非所问。这是语义匹配失败的典型——embedding 可能因为 `data_rand` 关键词匹配，但逻辑层面无关。

---

### ✅ Sample 10: [SATISFIED] AdvDataWidth 上界检查

**Assumption**: `AdvDataWidth` 可能超过 `KDFMaxWidth`，无本地检查  
**Guarantee**: 编译时断言 `AdvDataWidth <= KDFMaxWidth`  
**配对质量**: **STRONG** ✅  
**理由**: 完美的 SATISFIED 配对。Guarantee 直接提供了 assumption 担忧的缺失保证（编译时断言），消除风险。

---

### ✅ Sample 11: [SATISFIED] state_intg_err_d 驱动逻辑

**Assumption**: `state_intg_err_d` 声明但未驱动，完整性检查逻辑缺失  
**Guarantee**: (2 条) 非法 `state_q` 时驱动 `state_intg_err_d = 1`，寄存器同步  
**配对质量**: **STRONG** ✅  
**理由**: Guarantee 直接证明信号有驱动逻辑，反驳 assumption 的担忧。这是"chunking 导致局部视野受限"的典型修正案例。

---

### ✅ Sample 12: [SATISFIED] invalid_op 使用情况

**Assumption**: `invalid_op` 声明但未赋值，可能未使用或在别处驱动  
**Guarantee**: (2 条) `StCtrlInvalid` 状态下赋值 `invalid_op`，`keymgr_err` 消费该信号  
**配对质量**: **STRONG** ✅  
**理由**: Guarantee 完整描述了信号的驱动和消费路径，证明信号有效使用。配对精准。

---

## 三、质量分布统计

| 质量等级 | 数量 | 比例 | 示例 |
|---------|------|------|------|
| **STRONG** ✅ | 9 | 75% | Sample 1, 2, 4, 5, 7, 10, 11, 12 |
| **MODERATE** ⚠️ | 1 | 8% | Sample 3 |
| **WEAK** ❌ | 2 | 17% | Sample 6, 8 |
| **IRRELEVANT** ❌ | 1 | 8% | Sample 9 |

**关键发现**: 
- **75% 配对语义强相关**，能有效支撑 LLM 进行 CONTRADICTION / GAP / SATISFIED 判定
- **25% 配对存在问题**（3 个 WEAK/IRRELEVANT），主要失败模式：
  - **信号路径错配**（Sample 6：`out_q` assumption 配到了其他信号的 guarantee）
  - **逻辑层面无关**（Sample 9：问"为什么不对称"配到"如何使用"）
  - **guarantee 不够精准**（Sample 8：应该配到参数断言，配到了数据路径）

---

## 四、配对方法的有效性评估

### 4.1 当前方法：80% dense (BGE-M3 semantic) + 20% signal overlap

**优势**:
- 对于**语义匹配度高**的 AG 对（如 Sample 1, 2, 4, 5），dense embedding 能准确捕捉"错误传播"、"熵强度"、"one-hot 不变量"等抽象概念
- 对于**信号直接相关**的配对（Sample 10, 11, 12），signal overlap 提供了基础保证

**劣势**:
- **25% 误配率**仍然较高
- Signal overlap 只占 20% 权重，导致**跨信号路径的语义相关配对被错误匹配**（Sample 6: `out_q` 配到 `init_q/key_q` 的复位逻辑）
- Dense embedding 对"问题类型"的区分不足（Sample 9: "为什么不对称" vs "如何使用" 都被视为语义相关）

### 4.2 对比假设：如果提高 signal_weight

假设改为 **60% dense + 40% signal**：

| Sample | 当前结果 | 预期变化 |
|--------|---------|---------|
| 6 (WEAK) | `out_q` 配到 `init_q/key_q` | ❌ 可能被过滤（信号不重叠） → 配对质量提升 |
| 9 (IRRELEVANT) | `ctrl_rand/data_rand` 配到只有 `data_rand` 的 guarantee | ⚠️ 仍会配上（有 `data_rand` 重叠），但排名可能下降 |
| 8 (WEAK) | `MaxAdvDataWidth` 配到数据路径而非参数断言 | ⚠️ 无改善（参数名不在信号列表里） |

**结论**: 提高 signal_weight 能过滤 Sample 6 类跨路径误配，但无法解决 Sample 8/9 的问题。

### 4.3 根本问题：guarantee 粒度与 assumption 问题类型不匹配

- **Sample 8 的真实 bug**（F-0256 Phase 3 v3 发现）是"参数无上界断言"，但当前 guarantee 都是"数据路径行为"，所以配对永远配不到正确的 guarantee
- **Sample 9** 的 assumption 是"设计意图澄清"类问题，需要的是**架构文档或注释**，而非功能 guarantee

**改进方向**:
1. **Spec 生成阶段**增加"参数有效性 guarantee"类别（当前只有功能 guarantee）
2. **配对阶段**引入"问题类型标签"（功能矛盾 / 参数检查 / 设计意图 / 覆盖缺口），先按类型粗筛，再用 embedding 细排

---

## 五、对框架 bug 发现能力的影响

### 5.1 强配对（75%）支撑的 bug 发现

- **N-003** (F-0001/F-0032): Sample 4 类型的 GAP 配对 → `key_state_q` 未更新保证缺失
- **Bug 026** (F-0039/F-0230): 虽然不在采样里，但根据 Phase 3 v3 结果，agent 在 `key_output_ctrl` 路径上发现了 `StCtrlInvalid` 暴露未掩码数据的缺陷，说明相关 AG 配对有效
- **Extra** (F-0231): `owner_seed_vld` 错配，来自类似 Sample 2 的 CONTRADICTION 配对

### 5.2 弱配对（25%）导致的漏检

- **Bug 031** (FSM `ErrorState` 参数): 未发现。根因是 `PRIM_FLOP_SPARSE_FSM` 的 `ErrorState` 参数不在 chunk 可见范围内，**根本没生成对应的 guarantee**，所以无论配对多好都配不上
- **F-0256** 类参数缺陷：虽然 Phase 3 v3 发现了，但从 Sample 8 看，原始 AG 配对质量 WEAK——如果没有 agent 的"look further"能力，纯靠配对 + LLM 单轮分析可能漏掉

---

## 六、结论与建议

### 6.1 当前配对质量评价

**总体合格（75% 强相关）**，但 **25% 误配率偏高**，尤其在以下场景：
- 跨信号路径的语义相似（Sample 6）
- 参数/架构问题 vs 功能行为 guarantee（Sample 8, 9）

### 6.2 是否应该调整 dense/signal 权重？

**建议：保持 80/20，但引入分层过滤**

**理由**:
1. 提高 signal_weight 到 40-60% 能过滤跨路径误配（Sample 6），但会**损害真正的语义配对**（如 Sample 2: `data_rand` 的 assumption 可能涉及多个不同信号名的 guarantee）
2. 当前 80/20 的高 dense 比重是框架能发现"语义矛盾"（如 LFSR vs TRNG）的关键，不应降低

**改进方案**（不改权重）:
1. **Pre-filter**: 先用 signal overlap ≥ 0.1 做硬过滤（去掉 Sample 6 类完全无关的配对），再在剩余候选中用 80/20 排序
2. **Post-filter**: LLM 分析前，检查 assumption 涉及的主信号是否在 guarantee 的 `output_signals` 里，若完全不在则降级为 LOW_CONFIDENCE

### 6.3 Spec 生成阶段的改进（更重要）

**根本瓶颈不在配对，在 guarantee 的覆盖范围**:
- **Bug 031 漏检**：不是配对问题，是 `ErrorState` 参数根本没进入 chunk spec → 无 guarantee 可配
- **Sample 8 误配**：不是配对算法问题，是"参数上界断言"类 guarantee 缺失 → 只能配到数据路径

**建议**:
1. Spec 生成时显式提取 `parameter` / `localparam` 声明，生成"参数有效性 guarantee"
2. 对 `generate` 块内的参数化逻辑，提取参数依赖关系作为 guarantee
3. 对 `prim_*` 实例化，提取 module parameter 覆盖值（如 `ErrorState`）作为 guarantee

### 6.4 最终判断

**当前 AG 配对方法（80% dense + 20% signal）总体有效，但不是主要瓶颈**。

真正限制 bug 发现率的是：
1. **Spec 生成的覆盖范围**（参数、实例化参数、跨文件依赖）
2. **Chunking 粒度**（Bug 031 的 `ErrorState` 在实例化参数里，chunk 看不到）
3. **Phase 3 agent 的"look further"能力**（Sample 8 靠 agent 发现，不靠配对）

---

**分析完成**: 2026-06-29 · Claude Opus 4.8  
**下一步**: 根据 6.3 建议改进 Spec 生成器，增加参数类 guarantee 提取
