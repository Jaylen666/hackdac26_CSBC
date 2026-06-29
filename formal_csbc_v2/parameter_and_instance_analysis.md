# 参数和模块实例化信息对 Bug 发现的价值分析

**日期**: 2026-06-29  
**分析对象**: Formal CSBC v2.0 框架对参数和模块实例化的处理现状  
**数据源**: keymgr 的 342 个 query units (250 nonempty)

---

## 一、当前框架的处理现状

### 1.1 参数信息

**✅ 已部分提取**，但**不系统**：

| 类型 | 提取情况 | 数量统计 | 示例 |
|------|---------|---------|------|
| **参数值引用** | ✅ 在 assumption/guarantee 文本中出现 | 16 mentions | `MaxAdvDataWidth`, `CDIs`, `StageWidth`, `AdvLfsrCopies` |
| **参数声明** | ❌ 无独立提取 | 0 | `parameter int MaxAdvDataWidth = AdvDataWidth` |
| **参数约束** | ❌ 无编译时断言提取 | 0 | `ASSERT_INIT(AdvDataWidth <= KDFMaxWidth)` |
| **参数依赖** | ❌ 无依赖图 | 0 | `AdvLfsrCopies = AdvDataWidth / 32` |

**提取方式**：
- 当前是 LLM 在分析 chunk 时**顺带提到**参数（如"if Shares ≠ 2, the update skips higher shares"），不是**结构化提取**
- 没有专门的"参数 guarantee"类别，参数约束混杂在功能 guarantee 里

**典型案例**：
```
Assumption (keymgr_kmac_if__001::uncertain::1):
  "The parameter MaxAdvDataWidth allows override of the default AdvDataWidth, 
   but there is no local width check or documentation on allowed values."

Matched Guarantees (数据路径，不是参数约束):
  - adv_data is always assigned KDFMaxWidth'(adv_data_i), zero-padding...
  - kmac_data_o.data is assigned adv_data[adv_sel]
```

**问题**：应该配到的 guarantee 是"编译时断言 `AdvDataWidth <= KDFMaxWidth`"（Sample 10 有这个），但当前配对配到了数据处理逻辑，语义相关性 WEAK。

---

### 1.2 模块实例化信息

**✅ 已提取部分实例化描述**，但**缺乏结构化**：

| 类型 | 提取情况 | 数量统计 | 示例 |
|------|---------|---------|------|
| **实例化语句** | ✅ 在 guarantee 中描述 | 28 mentions | `u_edn_req instance of prim_edn_req` |
| **实例参数覆盖** | ⚠️ 部分提取（文本描述） | ~8 具体的 | `parameterized with OutWidth = LfsrWidth` |
| **模块间信号连接** | ⚠️ 部分提取 | 部分 | `connects edn_req to req_i, edn_ack from ack_o` |
| **子模块层次图** | ❌ 无 | 0 | keymgr → keymgr_ctrl → prim_flop_sparse_fsm |
| **跨模块依赖** | ❌ 无 | 0 | keymgr_ctrl.state_q → keymgr.working_state_o |

**提取方式**：
- LLM 看到 `prim_edn_req u_edn_req (...)` 会生成一条 guarantee 描述实例化
- 但没有**专门的"实例化关系"数据结构**，只是散落在 guarantee 文本里

**典型案例**：
```
Guarantee (keymgr_reseed_ctrl__declarations_or_instances__001):
  "u_edn_req instance of prim_edn_req is parameterized with 
   OutWidth = LfsrWidth, req_chk_i = 1'b1, and connects edn_req to req_i..."
```

**Bug 031 的关键缺失**：
```systemverilog
prim_flop_sparse_fsm #(
  .StateEnumT(keymgr_ctrl_pkg::keymgr_op_state_e),
  .Width($bits(keymgr_ctrl_pkg::keymgr_op_state_e)),
  .ResetValue($bits(keymgr_ctrl_pkg::keymgr_op_state_e)'(StIdle)),
  .ErrorState(StCtrlInvalid)  // <-- 错误：应该是 StCtrlDis，但 chunk 里看不到这个参数
) u_state_q (...);
```

当前框架**没有提取 `ErrorState` 参数**，因为：
1. Chunking 把 `always_comb` 和实例化语句分到不同 chunk
2. 即使在实例化 chunk 里，LLM 也只描述了"instance of prim_flop_sparse_fsm"，未提取参数覆盖列表

---

## 二、参数和实例化信息对 Bug 发现的价值

### 2.1 参数信息的价值：**HIGH** ⭐⭐⭐⭐⭐

#### 已发现的参数相关 bug

| Bug | 类型 | 当前框架表现 | 如果有结构化参数信息 |
|-----|------|-------------|---------------------|
| **F-0256** (MaxAdvDataWidth 无上界) | 参数约束缺失 | Phase 3 agent 发现（"look further"） | **直接配对命中** |
| **Sample 10** (AdvDataWidth 上界检查) | 参数约束存在 | SATISFIED（LLM 找到了断言） | 更快识别（参数约束 guarantee 独立类别） |

#### 参数相关 bug 的四种模式

1. **参数约束缺失**（F-0256）
   - Bug: `MaxAdvDataWidth` 可被外部覆盖，子模块无 `ASSERT_INIT(MaxAdvDataWidth <= KDFMaxWidth)`
   - 当前：配对 WEAK（配到数据路径而非参数约束）
   - 改进后：直接配到"参数上界断言缺失"guarantee

2. **参数依赖不一致**
   - Bug 假设: `AdvLfsrCopies = AdvDataWidth / 32` 向下取整，但 `AdvDataWidth` 不保证是 32 的倍数
   - 当前：16 个 assumption 提到参数依赖，但都是文本描述，无结构化检查
   - 改进后：提取参数依赖图，自动检查"除法 → 余数丢失"风险

3. **参数覆盖冲突**
   - Bug 假设: 顶层设置 `parameter Shares = 4`，但子模块 hardcode `share[0]` 和 `share[1]`
   - 当前：Sample 5 的 assumption 提到了这个（"KeyUpdateRoot hardcodes share indices 0 and 1"）
   - 改进后：参数传播分析可自动检测"顶层参数 vs 子模块 hardcode"不一致

4. **参数范围溢出**
   - Bug 假设: `cnt` 是 `CntWidth` 位，但 `for (i = 0; i < CDIs; i++)` 里 `CDIs > 2^CntWidth`
   - 当前：Sample 4 类 assumption 能描述这个（"cdi_sel_o must be in range [0, CDIs-1]"）
   - 改进后：参数依赖图 + 位宽分析可自动检测溢出

**结论**：参数信息是**编译时可静态验证的约束**，提取后能形成独立的"参数 guarantee"类别，显著提升配对精度和 bug 覆盖率。

---

### 2.2 模块实例化信息的价值：**CRITICAL** ⭐⭐⭐⭐⭐

#### 已漏检的实例化相关 bug

| Bug | 根因 | 当前框架表现 | 如果有实例化参数提取 |
|-----|------|-------------|---------------------|
| **Bug 031** | `prim_flop_sparse_fsm` 的 `ErrorState` 参数传错（应为 StCtrlDis，实为 StCtrlInvalid） | **完全漏检** | **直接发现** |

#### Bug 031 详细分析

**实际代码** (`keymgr_ctrl.sv`):
```systemverilog
prim_flop_sparse_fsm #(
  .StateEnumT(keymgr_ctrl_pkg::keymgr_op_state_e),
  .Width($bits(keymgr_ctrl_pkg::keymgr_op_state_e)),
  .ResetValue($bits(keymgr_ctrl_pkg::keymgr_op_state_e)'(StIdle)),
  .ErrorState(StCtrlInvalid)  // <-- BUG: 应该是 StCtrlDis
) u_state_q (...);
```

**为什么当前框架漏检**：
1. **Chunking 问题**：实例化语句和使用 `state_q` 的 `always_comb` 在不同 chunk
2. **参数不可见**：即使在实例化 chunk，当前 spec 生成也只描述"instance of prim_flop_sparse_fsm"，不提取参数列表
3. **跨模块语义丢失**：`prim_flop_sparse_fsm` 的设计意图是"非法编码时强制到 ErrorState"，但这个语义没有传递到 keymgr_ctrl 的 assumption 里

**如果有结构化实例化信息**：

可生成如下 guarantee：
```json
{
  "type": "instance_parameter",
  "spec_id": "keymgr_ctrl__instance__u_state_q__001",
  "property": "u_state_q (prim_flop_sparse_fsm) is parameterized with ErrorState = StCtrlInvalid",
  "output_signals": ["state_q"],
  "involved_parameters": ["ErrorState"],
  "parameter_values": {
    "ErrorState": "StCtrlInvalid"
  }
}
```

可生成如下 assumption（基于 FSM 语义）：
```json
{
  "spec_id": "keymgr_data_en_state__always_comb__001::assumption::0",
  "constraint": "When state_q is invalid, prim_flop_sparse_fsm should force it to a safe disabled state (StCtrlDis), not an invalid state marker (StCtrlInvalid)",
  "signal": "state_q",
  "risk": "StCtrlInvalid may expose key material; safe recovery should use StCtrlDis"
}
```

**配对后 LLM 分析**：
- Assumption 期望 ErrorState = StCtrlDis（安全禁用状态）
- Guarantee 显示 ErrorState = StCtrlInvalid（非法状态标记）
- **Verdict: CONTRADICTION** → Bug 031 被直接发现

---

#### 模块实例化信息的五种价值

1. **实例参数错误**（Bug 031）
   - 子模块参数传递错误
   - 当前：完全漏检（参数不在 chunk 可见范围）
   - 改进后：直接发现

2. **端口连接错误**
   - Bug 假设: `keymgr_ctrl.op_done_o` 连到 `keymgr_err.op_done_i`，但信号极性反了
   - 当前：28 个 guarantee 提到连接关系，但都是文本描述
   - 改进后：结构化端口映射 → 自动检查"信号方向 / 位宽 / 命名不一致"

3. **跨模块语义传播**
   - Bug 假设: 顶层 assumption "key_o.valid=0 时下游应忽略 key_o.key"，但子模块 guarantee 未保证
   - 当前：assumption 和 guarantee 在不同模块的 chunk 里，无法配对
   - 改进后：实例化图 → 跨模块 AG 配对

4. **层次化覆盖分析**
   - 顶层 assumption "state_q 必须 one-hot" → 应该由 `prim_flop_sparse_fsm` 保证，但实际未保证
   - 当前：Sample 5 类 GAP（guarantee 只描述错误检测，不保证预防）
   - 改进后：实例化图 → 自动追溯"谁应该保证这个不变量"

5. **实例化模式错误**
   - Bug 假设: `prim_lfsr` 的 `entropy_i` 端口被 tie-0，但设计意图要求连接 EDN
   - 当前：Sample 12 类 FALSE_ALARM（LLM 错误判断 tie-0 是合法的）
   - 改进后：提取实例化模式 + 原语语义库 → "prim_lfsr entropy_i tie-0 是 anti-pattern"

**结论**：模块实例化信息是**跨模块依赖和参数传播**的核心，直接影响 Bug 031 类缺陷的检测。这是当前框架的**最大盲点**。

---

## 三、改进方案

### 3.1 Spec 生成阶段增强

#### Phase 1A: 参数提取（新增独立步骤）

**目标**：从 RTL 文件中结构化提取所有参数信息

**提取内容**：
```json
{
  "parameters": [
    {
      "name": "MaxAdvDataWidth",
      "type": "int",
      "default_value": "AdvDataWidth",
      "scope": "keymgr_kmac_if",
      "declaration_line": 14,
      "used_in_expressions": ["AdvRounds = (MaxAdvDataWidth + ...) / ..."]
    }
  ],
  "parameter_constraints": [
    {
      "assertion_type": "ASSERT_INIT",
      "condition": "AdvDataWidth <= KDFMaxWidth",
      "location": "keymgr.sv:75"
    }
  ],
  "parameter_dependencies": [
    {
      "derived": "AdvLfsrCopies",
      "formula": "AdvDataWidth / 32",
      "risk": "division may lose remainder if AdvDataWidth not multiple of 32"
    }
  ]
}
```

**生成的新 guarantee 类型**：
```json
{
  "type": "parameter_constraint",
  "spec_id": "keymgr__parameter_constraint__001",
  "property": "Compile-time assertion ensures AdvDataWidth <= KDFMaxWidth",
  "involved_parameters": ["AdvDataWidth", "KDFMaxWidth"],
  "assertion_location": "keymgr.sv:75"
}
```

**实现方式**：
- **不用 LLM**：用 Slang parser 或正则表达式直接提取 `parameter` / `localparam` 声明
- **LLM 辅助**：对复杂的参数依赖公式（如 `AdvRounds = ...`）用 LLM 解释风险

---

#### Phase 1B: 实例化关系提取（新增独立步骤）

**目标**：构建模块层次图和实例参数映射

**提取内容**：
```json
{
  "instances": [
    {
      "instance_name": "u_state_q",
      "module_type": "prim_flop_sparse_fsm",
      "parent_module": "keymgr_ctrl",
      "parameter_overrides": {
        "StateEnumT": "keymgr_ctrl_pkg::keymgr_op_state_e",
        "ErrorState": "StCtrlInvalid"  // <-- 关键
      },
      "port_connections": {
        "clk_i": "clk_i",
        "rst_ni": "rst_ni",
        "state_i": "state_d",
        "state_o": "state_q"
      },
      "location": "keymgr_ctrl.sv:250"
    }
  ],
  "module_hierarchy": {
    "keymgr": {
      "children": ["keymgr_ctrl", "keymgr_err", "keymgr_kmac_if"],
      "signal_exports": ["working_state_o", "op_done_o"]
    }
  }
}
```

**生成的新 guarantee 类型**：
```json
{
  "type": "instance_parameter",
  "spec_id": "keymgr_ctrl__instance__u_state_q__ErrorState__001",
  "property": "u_state_q (prim_flop_sparse_fsm) ErrorState parameter is set to StCtrlInvalid",
  "output_signals": ["state_q"],
  "involved_parameters": ["ErrorState"],
  "parameter_value": "StCtrlInvalid",
  "instance_location": "keymgr_ctrl.sv:250"
}
```

**实现方式**：
- **Slang parser**：提取实例化语句和参数覆盖（纯静态分析，不需要 LLM）
- **LLM 辅助**（可选）：对 `prim_*` 原语，生成"设计意图 assumption"（如"ErrorState 应为安全禁用状态"）

---

### 3.2 配对阶段增强

#### 新增配对类型：参数 AG 配对

**当前**：所有 assumption 和 guarantee 混在一起，用 dense embedding 配对

**改进**：
1. **Pre-filter by type**：
   - 参数相关 assumption → 只配"参数 constraint" guarantee
   - 实例化相关 assumption → 只配"instance parameter" guarantee
   - 功能 assumption → 只配功能 guarantee

2. **参数 AG 配对权重调整**：
   - `parameter_name_match`: 0.5（参数名完全匹配）
   - `dense_embedding`: 0.3（语义相关性）
   - `signal_overlap`: 0.2（信号重叠）

**效果**：
- F-0256 的 assumption "MaxAdvDataWidth 无上界检查" → 直接配到"参数约束缺失"guarantee（而不是数据路径）
- Bug 031 的 assumption "ErrorState 应为 StCtrlDis" → 直接配到"ErrorState = StCtrlInvalid"guarantee

---

### 3.3 跨模块 AG 配对（长期目标）

**目标**：让不同模块的 assumption 和 guarantee 可以配对

**当前障碍**：
- keymgr_ctrl 的 assumption: "state_q 必须 one-hot"
- prim_flop_sparse_fsm 的 guarantee: "非法编码时强制到 ErrorState"
- **两者在不同文件/模块，当前无法配对**

**改进**：
1. 构建信号传播图：`keymgr_ctrl.state_q ← u_state_q.state_o (prim_flop_sparse_fsm)`
2. 跨模块配对：
   - 顶层 assumption on `state_q` → 子模块 guarantee on `state_o`（通过实例化关系映射）
3. 生成跨模块 finding：
   ```json
   {
     "title": "keymgr_ctrl expects state_q to be one-hot, but prim_flop_sparse_fsm only detects violations without preventing them",
     "involved_modules": ["keymgr_ctrl", "prim_flop_sparse_fsm"],
     "assumption_module": "keymgr_ctrl",
     "guarantee_module": "prim_flop_sparse_fsm",
     "instance_path": "keymgr_ctrl.u_state_q"
   }
   ```

---

## 四、投入产出比评估

### 4.1 参数提取

| 维度 | 评分 | 说明 |
|------|------|------|
| **实现难度** | ⭐⭐ (EASY) | 纯静态分析，Slang parser 即可，无需 LLM |
| **bug 覆盖增量** | ⭐⭐⭐ (MODERATE) | 覆盖 F-0256 类参数约束缺失，约占 5-10% bug |
| **配对精度提升** | ⭐⭐⭐⭐ (HIGH) | 解决 Sample 8 类误配（WEAK → STRONG） |
| **token 成本** | 0 | 无 LLM 调用 |

**建议优先级**：**HIGH** ✅  
**理由**：低成本高收益，直接解决 25% 误配中的一类。

---

### 4.2 实例化关系提取

| 维度 | 评分 | 说明 |
|------|------|------|
| **实现难度** | ⭐⭐⭐ (MODERATE) | Slang parser + 少量 LLM（生成设计意图 assumption） |
| **bug 覆盖增量** | ⭐⭐⭐⭐⭐ (CRITICAL) | **直接发现 Bug 031**，覆盖实例参数传递错误（约 10-20% bug） |
| **配对精度提升** | ⭐⭐⭐⭐⭐ (CRITICAL) | 新增"实例参数 AG 配对"类别，解决跨模块语义丢失 |
| **token 成本** | 低 | 仅对 prim_* 原语生成设计意图 assumption（约 10-20 次 LLM 调用/IP） |

**建议优先级**：**CRITICAL** ⭐⭐⭐⭐⭐  
**理由**：**这是框架的最大盲点**，Bug 031 完全漏检的根因。实现难度适中，收益极高。

---

### 4.3 跨模块 AG 配对

| 维度 | 评分 | 说明 |
|------|------|------|
| **实现难度** | ⭐⭐⭐⭐⭐ (HARD) | 需构建信号传播图 + 跨模块语义映射，架构级改动 |
| **bug 覆盖增量** | ⭐⭐⭐⭐ (HIGH) | 覆盖跨模块不一致（约 15-25% bug） |
| **配对精度提升** | ⭐⭐⭐⭐⭐ (CRITICAL) | 解决 Sample 5 类 GAP（guarantee 只在子模块，assumption 在顶层） |
| **token 成本** | 中 | 需生成跨模块 finding 的综合描述 |

**建议优先级**：**MEDIUM-LONG TERM** ⏰  
**理由**：收益高但实现复杂，作为 Phase 2 改进。先做 4.1 和 4.2。

---

## 五、总结与建议

### 5.1 当前框架的关键缺失

1. **参数信息**：部分提取（16 文本 mentions），但**无独立类别**，配对精度低（Sample 8: WEAK）
2. **实例化关系**：部分提取（28 文本 mentions），但**无结构化提取**，Bug 031 **完全漏检**

### 5.2 改进优先级

| 改进项 | 优先级 | 预计工作量 | 预期收益 |
|-------|--------|----------|---------|
| **参数提取 + 参数 AG 配对** | **P0** | 2-3 天 | 解决 F-0256 类，配对精度 +10% |
| **实例化关系提取 + 实例参数 guarantee** | **P0** | 5-7 天 | **发现 Bug 031**，bug 覆盖 +15% |
| **跨模块 AG 配对** | **P1** | 2-3 周 | bug 覆盖 +20%，架构级提升 |

### 5.3 回答你的问题

**Q1: 当前框架是不是既不分析参数，也不分析模块实例化关系？**

**A**: 
- **参数**：❌ 不系统。有 16 个文本 mentions，但无独立提取，无"参数 guarantee"类别
- **实例化**：❌ 不结构化。有 28 个文本 mentions，但无实例参数映射，Bug 031 的 `ErrorState` 参数完全不可见

**Q2: 这些对 debug 作用大吗？**

**A**: **CRITICAL** ⭐⭐⭐⭐⭐
- **参数信息**：解决 Sample 8 类误配（WEAK → STRONG），覆盖 F-0256 类参数约束缺失（约 5-10% bug）
- **实例化信息**：**直接发现 Bug 031**（当前完全漏检），覆盖实例参数传递错误（约 10-20% bug）

**Q3: 能不能形成更充分的分析上下文来提高 pair 阶段的 bug 命中率？**

**A**: **YES**，且是**最高 ROI 的改进方向**

改进后的配对上下文：
```
当前（Sample 8 误配）:
  Assumption: MaxAdvDataWidth 无上界检查
  Guarantee (WEAK): adv_data 零扩展到 KDFMaxWidth  ❌ 语义不相关

改进后:
  Assumption: MaxAdvDataWidth 无上界检查
  Guarantee (STRONG): 编译时断言 AdvDataWidth <= KDFMaxWidth ✅ 直接命中

当前（Bug 031 漏检）:
  无 guarantee 描述 ErrorState 参数

改进后:
  Assumption: ErrorState 应为安全禁用状态 (StCtrlDis)
  Guarantee: ErrorState = StCtrlInvalid
  Verdict: CONTRADICTION → Bug 031 被发现 ✅
```

### 5.4 最终建议

**立即启动** P0 改进（参数 + 实例化提取）：
1. Week 1-2: 实现参数提取 + 参数 guarantee 生成
2. Week 3-4: 实现实例化关系提取 + 实例参数 guarantee 生成
3. Week 5: 重跑 keymgr 完整流程，验证 Bug 031 是否被发现

**预期效果**：
- Bug 覆盖率：从当前 50%（2/4 bug）提升到 **75%（3/4 bug）**
- 配对精度：从 75% STRONG 提升到 **85% STRONG**
- 误配减少：WEAK/IRRELEVANT 从 25% 降到 **15%**

---

**分析完成**: 2026-06-29 · Claude Opus 4.8  
**下一步**: 实现参数提取器 (`rtl_bug_agent/spec/parameter_extractor.py`)
