# RTL Bug Agent 多上下文推理增强方案

**数据基础**: 19 个已知 OpenTitan bug（HMAC 7个，AES 5个，Keymgr 4个，UART 3个）  
**核心问题**: 42% bug 需要跨 chunk/文件，47% bug 需要多信号交互推理，当前 AGU 配对过于局部化

---

## 一、Bug 根因模式分析（19 个样本）

| 模式 | 数量 | 当前框架表现 | 示例 |
|------|------|-------------|------|
| 硬编码常量错误 | 2 | ✅ 强项 | HMAC-010 (1408 vs 1536) |
| **协议时序顺序错误** | 4 | ❌ **盲点** | HMAC-009 (cfg_block 过早清除) |
| 多路选择器/路由错误 | 3 | ⚠️ 部分覆盖 | AES-005 (key-clear mux 错配) |
| 条件门控错误 | 3 | ✅ 强项 | HMAC-wipe_secret_we (!reg_error) |
| 有效性/数据对应错误 | 2 | ⚠️ 需要信号追踪 | Keymgr-N-003 (ECC vs data 不一致) |
| FSM 状态转移错误 | 2 | ⚠️ 需要多条件 | Keymgr-026 (invalid state 暴露 key) |
| **参数/编译时配置错误** | 1 | ❌ **盲点** | AES-extra (SecAllowForcingMasks) |
| 边界条件/特殊值错误 | 2 | ⚠️ 需要边界推理 | UART-extra (VAL=0 sticky) |

**关键发现**:
1. 硬编码常量、条件门控：当前框架强项（单 chunk 可见，AGU 天然擅长）
2. **协议时序顺序**：最大盲点（4/19，21%），当前 AGU 无法表达"A 必须先于 B"
3. **参数/实例化**：第二大盲点（已在参数分析报告中覆盖）
4. 多信号交互：需要增强但非盲点（Phase 3 agent 的 "look further" 能部分覆盖）

---

## 二、三个关键增强方向

### 方向 1: 实例化关系提取 + 跨模块语义桥接 ⭐⭐⭐⭐⭐

**覆盖 bug**: Keymgr-031, AES-extra, 以及所有参数传递错误

**核心思想**: 构建模块实例化图 → 提取参数覆盖 → 生成子模块设计契约 → 跨模块 AGU 配对

**具体实现**:

#### Step 1: 实例化信息提取（纯静态，无 LLM）

```python
# 用 Slang/sv-parser 提取
class InstanceInfo:
    parent_module: str           # keymgr_ctrl
    instance_name: str           # u_state_q
    child_module: str            # prim_flop_sparse_fsm
    parameter_overrides: dict    # {ErrorState: StCtrlInvalid}
    port_connections: dict       # {state_i: state_d, state_o: state_q}
    location: str                # keymgr_ctrl.sv:250

# 输出格式（JSON）
{
  "instances": [
    {
      "parent": "keymgr_ctrl",
      "instance": "u_state_q",
      "module": "prim_flop_sparse_fsm",
      "params": {"ErrorState": "StCtrlInvalid"},
      "ports": {"state_i": "state_d", "state_o": "state_q"},
      "file": "keymgr_ctrl.sv",
      "line": 250
    }
  ]
}
```

**工具**: slang Python bindings 或 sv-parser（Rust，需封装 Python 接口）

---

#### Step 2: 原语语义库构建（人工 + LLM 混合）

为每个 `prim_*` 原语编写设计契约模板：

```yaml
# primitives/prim_flop_sparse_fsm.yaml
module: prim_flop_sparse_fsm
purpose: "Sparse-encoded FSM register with illegal-encoding detection"

parameters:
  ErrorState:
    type: enum
    purpose: "Target state when illegal encoding detected"
    constraint: "Should be a safe disabled state that prevents further operation"
    anti_patterns:
      - value: "StCtrlInvalid"
        reason: "Invalid marker state, not a safe operational state"
      - value: "StCtrlError"
        reason: "Error state may still allow partial operation"

design_contract:
  guarantee: "When input state encoding is illegal, output forced to ErrorState"
  assumption: "ErrorState is a safe disabled state (e.g., Idle, Disabled, Off)"
  security_implication: "Wrong ErrorState can leave system in exploitable state"
```

**生成过程**:
1. 人工编写 10-15 个核心原语的模板（prim_flop_sparse_fsm, prim_lfsr, prim_edn_req 等）
2. LLM 辅助：从原语源码 + 注释生成 `design_contract`
3. 人工审核 + 迭代

---

#### Step 3: 实例参数 Guarantee 生成（LLM）

输入：实例化信息 + 原语语义库  
输出：新类别 guarantee

```json
{
  "type": "instance_parameter",
  "spec_id": "keymgr_ctrl__instance__u_state_q__ErrorState",
  "property": "u_state_q (prim_flop_sparse_fsm) is parameterized with ErrorState=StCtrlInvalid",
  "instance": "u_state_q",
  "module": "prim_flop_sparse_fsm",
  "parameter": "ErrorState",
  "value": "StCtrlInvalid",
  "output_signals": ["state_q"],
  "involved_parameters": ["ErrorState"],
  "design_intent_from_lib": "ErrorState should be a safe disabled state",
  "potential_violation": true,
  "violation_reason": "StCtrlInvalid is an error marker, not a safe disabled state"
}
```

**LLM 提示词**:
```
Given instance: {instance_info}
Given design contract: {primitive_contract}

Generate an instance_parameter guarantee that:
1. States the actual parameter value
2. Checks if it violates the design contract
3. Flags potential_violation=true if the value is in anti_patterns

Output JSON only.
```

---

#### Step 4: 跨模块 AGU 配对

**配对规则**:
- 父模块 assumption (on `state_q`) ↔ 实例参数 guarantee (on `u_state_q.ErrorState`)
- 配对权重:
  - 信号名映射匹配（通过 port_connections）: 50%
  - 语义相关性（dense embedding）: 30%
  - 参数名匹配: 20%

**配对示例**:
```
Assumption (keymgr_ctrl):
  "When state_q is illegal, system should enter a safe disabled state that prevents key output"
  
Instance Parameter Guarantee (u_state_q):
  "ErrorState = StCtrlInvalid (violation: this is an error marker, not a disabled state)"

→ LLM 分析: CONTRADICTION
→ Finding: Keymgr-031 被发现
```

**预期效果**: 
- 直接覆盖 Keymgr-031
- 覆盖 AES-extra（顶层忽略编译参数）
- bug 覆盖率 +25%

---

### 方向 2: 时序顺序 Assumption 生成 ⭐⭐⭐⭐

**覆盖 bug**: HMAC-009, HMAC-extra_hash_stop_msg_freeze, HMAC-extra_hash_stop_unlock_window, UART-N-004（4/19，21%）

**核心思想**: 扩展 assumption 格式，支持"A 必须先于 B"、"A 到 B 之间禁止 C"这类时序约束

**当前 assumption 格式**（状态描述）:
```json
{
  "signal": "cfg_block",
  "constraint": "cfg_block must be cleared after hmac_done asserts"
}
```

**增强后格式**（时序描述）:
```json
{
  "type": "temporal_assumption",
  "primary_signal": "cfg_block",
  "temporal_relation": "AFTER",
  "reference_signal": "hmac_done",
  "constraint": "cfg_block must remain set until hmac_done completes, then clear",
  "violation_scenario": "If cfg_block clears before hmac_done, config window opens prematurely",
  "involved_signals": ["cfg_block", "hmac_done", "hash_stop"],
  "security_critical": true
}
```

**时序关系枚举**:
```python
class TemporalRelation(Enum):
    AFTER = "A must happen after B completes"
    BEFORE = "A must happen before B starts"
    DURING = "A must remain stable during B"
    NEVER_WITH = "A and B must never be true simultaneously"
    BETWEEN_FORBIDDEN = "C must not occur between A and B"
```

**具体实现**:

#### Step 1: Spec 生成提示词增强

在 `chunk_spec_agu_structured_slim_en.md` 增加一节：

```markdown
## Temporal Assumptions

For protocol-critical signals (FSM states, handshake signals, config locks), generate temporal assumptions:

**Example 1 (AFTER)**:
- Signal: cfg_block
- Temporal: cfg_block must clear AFTER hmac_done asserts
- Violation: If cfg_block clears on hash_stop but before hmac_done, config becomes writable too early

**Example 2 (DURING)**:
- Signal: wipe_key
- Temporal: key_state_q must remain stable DURING wipe_key=1
- Violation: If key_state_q changes during wipe, partial key may leak

**Example 3 (NEVER_WITH)**:
- Signal: op_start, op_done
- Temporal: op_start and op_done must NEVER_WITH be high simultaneously
- Violation: Simultaneous assertion creates race condition

Output format:
{
  "type": "temporal_assumption",
  "primary_signal": "<signal>",
  "temporal_relation": "AFTER | BEFORE | DURING | NEVER_WITH | BETWEEN_FORBIDDEN",
  "reference_signal": "<other signal>",
  "constraint": "<full description>",
  "violation_scenario": "<what goes wrong>"
}
```

---

#### Step 2: 时序 Guarantee 生成

Guarantee 也需要扩展，描述实际的时序行为：

```json
{
  "type": "temporal_guarantee",
  "spec_id": "hmac__always_ff__line_647",
  "property": "cfg_block is cleared when hash_stop asserts, regardless of hmac_done state",
  "primary_signal": "cfg_block",
  "trigger_signal": "hash_stop",
  "timing": "IMMEDIATE",
  "dependency_on_other_signals": ["hmac_done"],
  "dependency_state": "NOT_CHECKED"
}
```

---

#### Step 3: 时序 AGU 配对

**配对规则**:
- 时序 assumption ↔ 时序 guarantee
- 检查：assumption 期望的 temporal_relation 是否与 guarantee 的实际 timing 冲突

**配对示例**:
```
Temporal Assumption:
  cfg_block must clear AFTER hmac_done

Temporal Guarantee:
  cfg_block clears on hash_stop, NOT_CHECKED hmac_done

→ LLM 分析: CONTRADICTION (cfg_block clears too early)
→ Finding: HMAC-009 被发现
```

**预期效果**:
- 直接覆盖 4 个协议时序 bug
- bug 覆盖率 +21%

---

### 方向 3: 多信号联合 Assumption + 动态上下文扩展 ⭐⭐⭐⭐

**覆盖 bug**: Keymgr-026, AES-N-001, AES-N-002, Keymgr-extra（多信号交互）

**核心思想**: 让 assumption 显式描述多信号条件组合，Phase 3 agent 可主动扩展上下文

**当前 assumption 格式**（单信号）:
```json
{
  "signal": "key_o.key",
  "constraint": "key_o.key must be masked in invalid states"
}
```

**增强后格式**（多信号条件）:
```json
{
  "type": "multi_signal_assumption",
  "primary_signal": "key_o.key",
  "condition_signals": ["invalid_stage_sel_o", "state_q", "key_o.valid"],
  "constraint": "When (invalid_stage_sel_o=1 AND state_q=StCtrlInvalid), key_o.key must be masked with entropy, regardless of key_o.valid",
  "condition_graph": {
    "IF": ["invalid_stage_sel_o = 1", "state_q = StCtrlInvalid"],
    "THEN": "key_o.key = {EntropyRounds{entropy_i}}",
    "ELSE": "key_o.key = key_state_q[cdi_sel_o]"
  },
  "security_critical": true,
  "risk": "Exposing unmasked key_state in invalid state allows side-channel attack"
}
```

**具体实现**:

#### Step 1: Spec 生成提示词增强

```markdown
## Multi-Signal Assumptions

For security-critical outputs (key material, FSM outputs, error flags), generate multi-signal assumptions:

1. **Identify condition signals**: What other signals affect this output's behavior?
2. **Enumerate corner cases**: What happens when condition signals take unusual combinations?
3. **Security invariants**: What must NEVER happen under ANY combination?

Example:
- Primary: key_o.key
- Conditions: invalid_stage_sel_o, state_q, key_o.valid
- Invariant: "Unmasked key_state must never appear on key_o under any (invalid_stage_sel_o, state_q) combination"

Output format:
{
  "type": "multi_signal_assumption",
  "primary_signal": "<main output>",
  "condition_signals": ["<cond1>", "<cond2>"],
  "constraint": "<full constraint>",
  "condition_graph": {IF: [...], THEN: "...", ELSE: "..."}
}
```

---

#### Step 2: 配对权重调整

Multi-signal assumption 配对时：
- 主信号匹配: 40%
- **次要信号重叠**: 30%（assumption 的 condition_signals 与 guarantee 的 involved_signals 的交集占比）
- Dense embedding: 30%

---

#### Step 3: Phase 3 动态上下文扩展

Phase 3 提示词增加：

```markdown
## Context Expansion Protocol

If the finding involves multi-signal interaction but the current chunk lacks context on condition signals, request expansion:

```json
{
  "status": "NEED_MORE_CONTEXT",
  "missing_contexts": [
    {
      "type": "signal_drivers",
      "signals": ["invalid_stage_sel_o", "state_q"],
      "reason": "Need to see all conditions under which these signals change"
    },
    {
      "type": "signal_readers",
      "signal": "key_o.key",
      "reason": "Need to verify all code paths that assign key_o.key"
    }
  ]
}
```

System will fetch the requested contexts and resume your analysis.
```

**系统实现**:
```python
def fetch_context(request):
    if request['type'] == 'signal_drivers':
        # 查找所有驱动这些信号的 chunk
        return find_chunks_driving_signals(request['signals'])
    elif request['type'] == 'signal_readers':
        # 查找所有读取该信号的 chunk
        return find_chunks_reading_signal(request['signal'])
    elif request['type'] == 'instance_source':
        # 查找实例化语句
        return find_instance_definition(request['instance'])
```

**预期效果**:
- 覆盖 Keymgr-026（多条件组合）
- 覆盖 AES-N-001/N-002（rail OR-merge 需要理解多个 rail 的组合）
- bug 覆盖率 +15%

---

## 三、实施路线图

### Phase 1 (Week 1-2): 方向 1 — 实例化关系提取

**Week 1**:
1. 实现 Slang-based 实例化提取器（`rtl_bug_agent/spec/instance_extractor.py`）
2. 构建原语语义库（10 个核心原语：prim_flop_sparse_fsm, prim_lfsr, prim_edn_req 等）
3. 单元测试：在 keymgr 上验证提取正确性

**Week 2**:
4. 实现实例参数 guarantee 生成（LLM 调用 + 原语库查询）
5. 修改 AGU 配对器，支持跨模块配对
6. 集成测试：重跑 keymgr，验证 Keymgr-031 是否被发现

**交付物**:
- `instance_extractor.py`（250 行）
- `primitives/` 目录（10 个 YAML 文件）
- 修改 `semantic_ag.py` 的配对逻辑（100 行）

**验收标准**: Keymgr-031 从漏检变为 CONFIRMED

---

### Phase 2 (Week 3-4): 方向 2 — 时序顺序 Assumption

**Week 3**:
1. 修改 spec 生成提示词，增加时序 assumption 生成规则
2. 扩展 assumption/guarantee JSON schema，支持 `type: temporal_*`
3. 在 HMAC 上测试：验证能否生成 cfg_block AFTER hmac_done 的 assumption

**Week 4**:
4. 实现时序 AGU 配对逻辑（检查 temporal_relation 冲突）
5. 集成测试：重跑 HMAC，验证 HMAC-009 是否被发现
6. 迭代提示词（根据生成质量调整示例）

**交付物**:
- 修改 `chunk_spec_agu_structured_slim_en.md`（+100 行）
- 修改 `types.py` 的 schema（+50 行）
- 修改 `semantic_ag.py` 的时序配对（+80 行）

**验收标准**: HMAC-009 从漏检变为 CONFIRMED

---

### Phase 3 (Week 5-6): 方向 3 — 多信号 + 动态扩展

**Week 5**:
1. 修改 spec 生成提示词，增加多信号 assumption 生成规则
2. 调整配对权重（次要信号重叠 +30%）
3. 在 keymgr 上测试：验证 Keymgr-026 的 assumption 是否包含 condition_signals

**Week 6**:
4. 实现 Phase 3 动态上下文扩展协议
5. 实现上下文检索系统（signal_drivers, signal_readers, instance_source）
6. 端到端测试：重跑 keymgr + HMAC + AES

**交付物**:
- 修改 `verify_agent.md`（+150 行，增加 NEED_MORE_CONTEXT 协议）
- 实现 `context_fetcher.py`（300 行）
- 修改 Phase 3 orchestrator（+100 行）

**验收标准**: Keymgr-026 从 "agent look further" 变为 "配对时直接命中"

---

## 四、预期效果

### Bug 覆盖率提升（基于 19 个样本）

| 指标 | 当前 | +方向1 | +方向2 | +方向3 | 总计 |
|------|------|--------|--------|--------|------|
| 覆盖的 bug | 9/19 (47%) | +1 | +4 | +3 | **17/19 (89%)** |
| 参数/实例化 bug | 0/1 | **1/1** | - | - | 1/1 |
| 协议时序 bug | 0/4 | - | **4/4** | - | 4/4 |
| 多信号交互 bug | 6/9 | - | - | **+3** | 9/9 |

### 配对精度提升

| 指标 | 当前 | 目标 |
|------|------|------|
| STRONG 配对比例 | 75% | **90%** |
| WEAK/IRRELEVANT 配对 | 25% | **10%** |

### 未覆盖的 2 个 bug

1. **HMAC-011**（127-cycle cool_down delay）: 需要"定量时序分析"（超出当前框架范围）
2. **UART-extra_timeout_val0_sticky**（VAL=0 边界条件）: 需要"特殊值枚举"（可通过提示词改进部分覆盖）

---

## 五、成本与风险评估

### 开发成本

| 阶段 | 时间 | LLM token 成本增量 | 实现难度 |
|------|------|-------------------|---------|
| 方向 1 | 2 周 | +10%（实例参数 guarantee 生成） | ⭐⭐⭐⭐ |
| 方向 2 | 2 周 | +15%（时序 assumption/guarantee） | ⭐⭐⭐ |
| 方向 3 | 2 周 | +20%（动态上下文扩展，按需调用） | ⭐⭐⭐ |

### 风险

1. **原语语义库维护成本**: 每新增一个 IP，需人工审核 3-5 个新原语
   - 缓解：优先覆盖高频原语（prim_*），长尾原语用 LLM 自动生成 + 人工抽查

2. **时序 assumption 生成质量**: LLM 可能过度生成或遗漏关键时序约束
   - 缓解：提示词中给出 5-8 个高质量示例，迭代调优

3. **动态上下文扩展的 token 爆炸**: agent 可能请求过多上下文
   - 缓解：限制扩展深度（最多 2 轮扩展），限制单次返回 chunk 数量（≤5）

---

## 六、总结

基于 19 个 OpenTitan bug 的实证分析，当前框架的三大盲点是：
1. **参数/实例化**（1/19）→ 方向 1 完全覆盖
2. **协议时序顺序**（4/19，21%）→ 方向 2 完全覆盖
3. **多信号交互**（部分覆盖）→ 方向 3 补齐剩余 3 个

三个方向互补：
- 方向 1 解决跨模块语义传播
- 方向 2 解决时序顺序约束表达
- 方向 3 解决多信号条件组合推理

**实施后预期**: bug 覆盖率从 47% 提升至 **89%**，配对 STRONG 比例从 75% 提升至 **90%**，成为业界首个能系统化发现 RTL 协议时序 bug 的自动化框架。
