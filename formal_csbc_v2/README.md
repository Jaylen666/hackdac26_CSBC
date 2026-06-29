# Formal CSBC v2.0 端到端测试 - Keymgr 模块

## 测试环境

- **模型**: DeepSeek v4-pro (via GUOCHUANG_ANTHROPIC_* 环境变量)
- **模块**: keymgr (OpenTitan)
- **输出目录**: `/home/smy/rtl_bug_agent/formal_csbc_v2`

## 执行测试

### 1. 运行完整测试

```bash
cd /home/smy/rtl_bug_agent/formal_csbc_v2
./run_keymgr_e2e.sh
```

**预计耗时**: 10-30 分钟（取决于 specs 数量和 LLM API 速度）

### 2. 实时监控进度

在另一个终端窗口执行：

```bash
cd /home/smy/rtl_bug_agent/formal_csbc_v2
./monitor_progress.sh
```

或者实时跟踪日志：

```bash
tail -f /home/smy/rtl_bug_agent/formal_csbc_v2/keymgr_e2e_run.log
```

### 3. 检查 Formal Solver 执行情况

测试完成后：

```bash
cd /home/smy/rtl_bug_agent/formal_csbc_v2
./check_formal_solver.sh
```

## 输出文件

测试完成后会生成以下文件：

| 文件 | 说明 |
|------|------|
| `findings_keymgr.json` | 最终 findings（包含 formal_result 和 phase3 结果） |
| `trace_keymgr.jsonl` | 完整追踪记录（JSONL 格式） |
| `keymgr_e2e_run.log` | 完整执行日志 |
| `semantic_ag_shadow_keymgr.json` | Semantic AG 配对摘要 |

## 关键执行阶段

执行过程包含以下阶段，可在日志中看到进度：

### Pass 0: Signal Dependency Graph
- 构建信号依赖图
- 输出：`SignalGraph: N signals, M A-G pairs`

### Semantic AG Pairing
- 使用 BGE-M3 进行语义配对
- 输出：`Semantic AG: X pairs, Y query units, Z unmatched uncertain`

### Channel B: Assumption-Guarantee Pairing
- LLM 判断 A-G 配对是否矛盾
- 生成 SVA 属性（如果判断为 BUG/UNCERTAIN）
- 输出：每个配对的 verdict

### Channel F: Unpaired Item Processing
- 处理未配对的 uncertain 和 assumption
- 生成 SVA 属性
- 输出：`Channel F: X candidates, Y PENDING, Z GATED_OUT`

### Formal Solver Execution ⭐
- 对 status=PENDING 的 findings 执行 sby+z3
- 输出：`formal_result.verdict = PASS/FAIL/UNKNOWN/ERROR`
- **这是 v2.0 的核心新增功能**

### Phase 3: Source Verification
- LLM 读取 RTL 源码验证 findings
- **现在能看到 formal_result 作为证据** ⭐
- 输出：`CONFIRMED/FALSE_ALARM/NEEDS_MORE_CONTEXT/UNCERTAIN`

## 调试信息说明

### 终端输出关键信息

1. **Signal Graph 构建**
   ```
   SignalGraph: 156 signals (89 driven, 120 consumed), 234 A-G pairs
   ```

2. **Semantic AG 配对**
   ```
   Semantic AG: 45 pairs, 12 query units, 3 unmatched uncertain → Phase 3
   ```

3. **Channel B 进度**
   ```
   Channel B semantic: batch 1/3, 8 queries
   ```

4. **Channel F 执行**
   ```
   Channel F: 15 candidates
     Direct/security: 8
     Low-severity: 2
     GATED_OUT: 5
   Channel F: 10 SVA generated, 7 PENDING
   ```

5. **Formal Solver 执行** ⭐
   ```
   Formal solver: 7 PENDING findings
   [1/7] F-001: running sby+z3 (depth=30, timeout=60s)...
   formal_result: FAIL (counterexample found, 2.3s)
   ```

6. **Phase 3 验证**
   ```
   Phase3 [1/5] F-001 ... CONFIRMED (confidence=0.9)
   formal_alignment: FAIL verdict aligns with CONFIRMED
   ```

## 验证 Formal CSBC v2.0 是否正常工作

### 检查点 1: SVA 生成
```bash
grep -c '"status": "PENDING"' findings_keymgr.json
```
**期望**: 至少有几个 findings 的 `formal.status` 为 PENDING

### 检查点 2: Solver 执行
```bash
grep -c '"formal_result"' findings_keymgr.json
```
**期望**: 与 PENDING 数量相同（每个 PENDING 都应该有 solver 结果）

### 检查点 3: Phase 3 可见
```bash
./check_formal_solver.sh | grep -A 5 "Solver verdict"
```
**期望**: 能看到 PASS/FAIL/UNKNOWN 等 verdict，且 Phase 3 的 `formal_alignment` 字段有内容

### 检查点 4: 数据流完整
```python
import json
findings = json.load(open('findings_keymgr.json'))
for f in findings[:5]:
    formal_status = f.get('formal', {}).get('status')
    has_result = 'formal_result' in f
    has_phase3 = 'phase3' in f
    alignment = f.get('phase3', {}).get('formal_alignment', 'N/A')
    print(f"Status:{formal_status} Result:{has_result} Phase3:{has_phase3} Alignment:{alignment[:50]}")
```

## 常见问题

### 1. 没有 PENDING findings
**原因**: SVA 验证失败（signal name 错误）或 formalizability 太低
**检查**: 
```bash
grep -E '"status": "(INCOMPLETE|NAME_UNVERIFIED|NO_PROPERTY)"' findings_keymgr.json | wc -l
```

### 2. Solver 没有执行
**原因**: `--run-solver` 参数缺失或没有 PENDING findings
**检查**: 日志中是否有 "Formal solver:" 字样

### 3. Phase 3 看不到 formal 证据
**原因**: v2.13 之前的版本 prompt 不支持
**检查**: 
```bash
grep "formal_alignment" findings_keymgr.json
```
如果为空，说明 Phase 3 prompt 没有更新

## 预期结果

对于 keymgr 模块，预期看到：

- **Total findings**: 20-50 个（取决于 semantic AG 配对结果）
- **PENDING findings**: 5-15 个（有效的 SVA 属性）
- **Solver results**: 与 PENDING 数量相同
  - PASS: 属性成立（可能是误报）
  - FAIL: 找到反例（强证据，疑似真实 bug）
  - UNKNOWN: 超时或无界设计
- **Phase 3 verified**: top-5（由 `--phase3-top-n 5` 控制）
  - 应该能看到 `formal_alignment` 字段
  - CONFIRMED findings 中如果有 formal_result=FAIL，说明工具证据与人工判定一致

## N-003 ECC Bug 检测

如果测试覆盖了 keymgr_ctrl.sv 的 key_state 相关代码，期望看到：

**Finding 特征**:
- **Signals**: key_state_q, key_state_ecc_q
- **Contradiction**: ECC width mismatch 或 key state 可能为零
- **SVA**: `assert property (@(posedge clk) key_state_q != 0)`
- **Formal verdict**: FAIL（如果 shares 相同时确实产生零值）
- **Phase 3 verdict**: CONFIRMED

## 成功标准

Formal CSBC v2.0 端到端测试成功的标志：

✅ **数据流完整**: PENDING → solver → formal_result → Phase 3  
✅ **工具执行**: sby+z3 成功运行并返回 verdict  
✅ **LLM 可见**: Phase 3 的 `formal_alignment` 字段有内容  
✅ **零成本追踪**: trace.jsonl 记录完整，但不影响 LLM token 消耗  

---

**架构版本**: Formal CSBC v2.13  
**测试日期**: 2026-06-27  
**维护者**: claude (Opus 4.8)
