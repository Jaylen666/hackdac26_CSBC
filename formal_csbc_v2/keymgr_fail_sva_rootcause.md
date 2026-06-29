# Formal CSBC v2.0 — Keymgr 78 条 FAIL SVA 假阳性根因分析

**日期**: 2026-06-28
**对象**: `findings_keymgr.json` 中 78 条 `formal_result.verdict == FAIL` 的 SVA
**结论先行**: 这 78 条 FAIL **绝大多数是假阳性**,且 **0 条带反例波形**(`trace_file` 全为 null)。
根因不是"solver 算错了",而是 **SVA 在生成阶段就脱离了 RTL 的设计契约**——
solver 忠实地为一个"设计从未承诺过"的属性找到了反例,所以 FAIL 是**必然且无意义**的。

---

## 一、关键全局事实

| 指标 | 值 | 含义 |
|------|-----|------|
| FAIL 总数 | 78 | 全部 PENDING SVA 都 FAIL,**没有一条 PASS / UNKNOWN** |
| 带反例波形 (`trace_file`) | **0** | solver 没有产出可检视的 counterexample 波形 |
| 源 verdict = GAP | 64 (82%) | **绝大多数来自 GAP**——"缺少保证",不是"存在矛盾" |
| 源 verdict = CONTRADICTION | 7 | |
| 源 verdict = UNCERTAIN | 7 | |
| formalizability = direct | 58 | 即便标记 direct 也大量假阳性 |

**最重要的一条**: 78/78 全 FAIL 且 0 反例。一个健康的 formal 流程不可能所有属性都被违反。
这说明 FAIL 不是来自 RTL 缺陷,而是来自 **SVA 与 RTL 的系统性错配**——属性写的就是错的,
solver 只是诚实地报告"你要我证的这条,设计根本不满足"。

---

## 二、假阳性的 5 类根因（按出现频率排序）

### 类 1 — GAP 被强行转成"强保证断言"（最主要，~64 条的主体）

**机制**: Channel B 判一个 atom 为 GAP,意思是"消费者假设了 X,但没有 guarantee 覆盖 X"。
这本质是**规约层面的覆盖缺口**,不是 RTL 缺陷。但 Channel F/B 把这个缺口直接翻译成
一条 `assert (X)`,于是 solver 去证 X——而设计**从未承诺 X**,必然找到反例。

**实例 F-0024**:
```
SVA:  (en_i == 1'b0) |-> (key_o == '0)
RTL:  assign key_o = key_q;              // key_o 永远等于 key_q
      assign valid_o = valid_q & en_i;   // 门控的是 valid_o, 不是 key_o
```
设计契约是"**用 valid_o 告诉下游数据无效,key_o 本身不清零**"。SVA 却断言
"en_i=0 时 key_o 必须为 0"——这是 SVA **臆造的契约**,RTL 从没这么承诺。FAIL 无意义。

**同类**: F-0012(`key_state_q == $past(key_state_d)`,但设计里 key_state_q 本就不从 key_state_d 更新)、
F-0130(`!en_i |-> stage_sel_o==DISABLE`,RTL 只在部分状态这么做)、F-0136、F-0145、F-0181 等。

### 类 2 — 编译期参数被当成运行时信号断言（~6 条）

**机制**: 把 `localparam` / `parameter` 写进运行时 SVA。参数在编译期就是常量,
运行时断言它毫无意义;且 solver 把参数当自由变量求解时会直接构造出违反值 → FAIL。

**实例 F-0016 / F-0017**:
```
SVA:  (KeyWidth <= MaxWidth)
RTL:  parameter int KeyWidth = 256;   parameter int MaxWidth = 256;   // 都是编译期常量
```
`256 <= 256` 本应恒真,FAIL 说明 solver 把 `KeyWidth`/`MaxWidth` 当成了**可自由取值的信号**,
于是找到 `KeyWidth=257` 之类的"反例"。这类属性应该是 `初始断言/elaboration assertion`,
不该进 BMC。**同类**: F-0071、F-0195、F-0279、F-0293、F-0197(参数边界类)。

### 类 3 — SVA 与实际编码方案矛盾（~3 条）

**机制**: SVA 假设了一个 RTL 没采用的编码/结构。

**实例 F-0113**:
```
SVA:  $onehot(state_q)
RTL:  StCtrlReset = 10'b1101100001;  StCtrlEntropyReseed = 10'b1110010010; ...  // sparse FSM, 非 onehot
```
keymgr 的 FSM 用的是**汉明距离 sparse 编码**(抗故障注入),`StCtrlReset` 就有 5 位为 1。
SVA 断言 onehot 完全脱离事实,reset 态立刻 FAIL。这恰恰是 Bug 031 的反面教材——
SVA 连"这是 sparse FSM"都不知道,自然也提不出"非法编码该触发 fsm_err"的正确属性。

### 类 4 — 臆造的信号互斥/握手约束（~7 条 CONTRADICTION 的主体）

**机制**: Channel B 判 CONTRADICTION 时,假设两个信号互斥或有某种握手关系,但 RTL 里它们同源。

**实例 F-0040**:
```
SVA:  !(op_done_o && op_update_o)        // 假设互斥
RTL:  assign op_done_o = op_req ? op_ack : ...;   // 二者都是 op_req 的派生, 可同时为高
```
"对端 keymgr_err 要求二者不同时高"是 SVA 臆想的输入约束,RTL 驱动端根本没保证互斥 → FAIL。
**同类**: F-0042(enables_sub/enables_d one-hot 假设)、F-0107(advance_sel/disable_sel 互斥)。

### 类 5 — 复位语义自相矛盾（~5 条）

**机制**: SVA 用 `disable iff (!rst_ni)` 排除了复位周期,**却又去断言复位时的行为**,逻辑上永远抓不到。

**实例 F-0063**:
```
SVA:  disable iff (!rst_ni)  $rose(rst_ni) |-> (fault_err_req_q==0 && op_err_req_q==0)
```
`disable iff (!rst_ni)` 已经把 rst_ni 为低的所有周期屏蔽掉;`$rose(rst_ni)` 是复位**释放沿**,
此时要验证的"复位期间已清零"恰恰发生在被屏蔽的窗口里 → 属性要么 vacuous 要么 FAIL,均无意义。
**同类**: F-0114、F-0095、F-0209、F-0185(复位与断言窗口错位)。

---

## 三、为什么"没问题的地方"会 FAIL —— 三句话总结

1. **GAP ≠ Bug,但被当 Bug 证了**。82% 的 FAIL 源自 GAP。GAP 是"规约没覆盖",
   把它翻译成 `assert(缺失的保证)`,等于让 solver 去证一条**设计从未承诺的命题**,FAIL 是定义使然。

2. **SVA 脱离 RTL 事实**。参数当信号(类2)、onehot vs sparse(类3)、臆造互斥(类4)、
   复位窗口错位(类5)——这些 SVA 在**语义上就是错的**,跟 RTL 实际行为无关,solver 只是诚实报错。

3. **0 反例波形是铁证**。如果是真 bug,solver 会给出具体的 counterexample trace。
   78 条全 FAIL 且 `trace_file` 全 null,说明这些属性大多在**初始态/第0拍**就被违反
   (参数错配、复位态 sparse 编码),根本不是需要时序展开才能触发的真实缺陷。

---

## 四、这对框架意味着什么

### FAIL 的当前价值 ≈ 0,甚至是负的
- 把 FAIL 当"CONFIRMED 证据"喂给 Phase 3 会**误导** agent(好在这次没喂)。
- 真正的 N-003(F-0001)反而**没有有效 SVA**——它的矛盾是"data 不更新而 ECC 更新",
  需要跨两个 always 块的关系断言,现有 SVA 生成器表达不了,所以 N-003 是靠**文本描述**
  被 Phase 3 抓到的,不是靠 formal。

### 根因在 SVA 生成,不在 solver
- solver(sby+z3)工作正常:0 error,平均 0.39s,它忠实地证伪了喂给它的（错误）属性。
- 问题在 **Channel B/F 的 SVA 合成**: 它把"规约缺口/文本怀疑"机械地转成断言,
  没有校验①属性是否对应 RTL 真实契约 ②信号是参数还是运行时 ③复位窗口 ④编码方案。

---

## 五、改进建议（针对 SVA 假阳性）

| 优先级 | 措施 | 解决的类别 |
|--------|------|-----------|
| **P0** | **GAP 不生成"强保证断言"**。GAP 是覆盖缺口,应生成 *cover* 或交 Phase 3 文本核查,不要 `assert(缺失保证)` | 类1（64条主体）|
| **P0** | **参数/localparam 走 elaboration assertion,禁止进 BMC**。生成前查符号是 parameter 还是 signal | 类2 |
| P1 | **SVA 生成前注入 RTL 事实**: FSM 编码方式(sparse/onehot/gray)、复位值、信号驱动来源,作为约束 | 类3、类4 |
| P1 | **复位断言模板化**: 复位行为用 `$fell(rst_ni)` 前的状态或专门的 reset-check,不要混用 `disable iff` + `$rose(rst_ni)` | 类5 |
| P2 | **FAIL 必须带 counterexample 才算证据**: 无 trace 的 FAIL 标记为 "INCONCLUSIVE",不进 Phase 3 证据链 | 全部 |
| P2 | **PASS 才是有价值信号**: 当前 0 条 PASS 本身说明 SVA 质量差;健康流程应有相当比例 PASS(证明属性成立→FALSE_ALARM 佐证) | 全部 |

---

## 六、一句话回答你的问题

> "为什么明明没问题的地方会有 FAIL 的 SVA?"

**因为这些 SVA 写的就是错的——不是描述 RTL 真实承诺的契约,而是把"规约缺口"和"文本怀疑"
机械地翻译成了设计从未承诺的强断言。** solver 诚实地为这些错误属性找到了反例(FAIL),
但反例对应的"违反"恰恰是设计的**正常行为**(key_o 不清零、参数本就是常量、FSM 本就是 sparse 编码)。
所以 FAIL 是 **SVA 假阳性**,不是 RTL 缺陷。佐证:78 条全 FAIL、0 条 PASS、0 条反例波形——
这是典型的"属性系统性错配"特征,而非"设计系统性出错"。

---

**分析完成**: 2026-06-28 · Claude Opus 4.8
