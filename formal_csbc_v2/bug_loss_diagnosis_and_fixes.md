# CSBC Bug 逐级丢失诊断与改动方案

> 范围:最新一轮 5 模块(dma/kmac/rv_dm/soc_dbg_ctrl/tlul)只确认 4 个 bug,且全部 `is_extra_finding=true`。
> 本文档基于对 6 个 strong-AGU bug 的逐阶段追踪(spec_gen → pairing → mismatch_reasoning → ranking → phase3),定位丢失环节并给出**不改代码前的方案评估**。

---

## 1. 诊断结论(有证据支撑)

对 6 个"AGU 已捕获信号路径"的 bug 做了完整链路追踪,丢失阶段分布如下:

| Bug | spec_gen 捕获机制? | pairing 描述对? | rank | 送进 phase3? | 最终 | **丢失环节** |
|-----|:---:|:---:|:---:|:---:|:---:|---|
| KMAC F-0039 | ✅ | ❌ 配错对 | 39 | ✅ | CONFIRMED(extra) | **失配推理** |
| TLUL-029 | ✅ | ❌ 配错对 | 1 | ✅ | CONFIRMED(extra) | **失配推理** |
| TLUL-028 | ✅ | ❌ 配错对 | 42 | ⛔ | 漏 | **失配推理 + 排名** |
| DMA F-0021 | ⚠️ 仅提信号 | ❌ | 21 | ✅ | CONFIRMED(extra) | **spec_gen(轻)** |
| KMAC-036 | ✅ | ✅ 描述正确 | 65 | ⛔ | 漏 | **排名截断** |
| KMAC N-005 | ✅ | ✅ 描述正确 | 96 | ⛔ | 漏 | **排名截断** |

**两个独立瓶颈,均不在检索:**

- **瓶颈 A — 失配推理"配错对"(3/6)**:spec 里**同时存在**对的 atom(描述真实机制)和邻近的干扰 atom。channel-B 在配对时把 guarantee 配到了**错误的 assumption**,生成了机制错误的 contradiction 文本。真正的 bug 描述要么沦为低分尾部 finding(F-0039 的正确版本 F-0495 排在 ~494 位、score 0.254),要么被错误机制覆盖。phase3 之所以能救回,正是因为 agent **抛开 finding 描述自由读 RTL**——这恰好证明 pairing 的产物不可信。

- **瓶颈 B — 排名截断挡掉正确 finding(2/6)**:KMAC-036、N-005 的 finding **描述完全正确、verdict=GAP/HIGH、甚至自带可直接形式化的 SVA**,但 score 都是 0.6,落在一个巨大的并列带里(**119 个 finding 并列 0.6 分**),被 top-50 截断挡在门外。phase3 从未见过它们。

> `is_extra_finding=true` 占满 4/4,本质含义就是:**"定位对了、描述错了、靠 phase3 自由审查补救"**。系统当前的"检索"能力没问题,问题在"配对推理"和"排名"两段。

---

## 2. 根因分析(代码级)

### 2.1 排名:scoring 公式无区分度 → 大量并列 → 截断成了随机

`fusion.py:134-140`:

```python
f.score = (
    0.60 * f.signal_criticality       # 主导项
    + 0.25 * f.contradiction_strength
    + 0.15 * min(f.cross_channel_hits / 2.0, 1.0)
)
```

- `signal_criticality`(`fusion.py:328-351`)对"关键信号占比"取值,且 crypto 关键词只给 0.5 部分分。对 kmac 这种几乎全是 `msg/key/fifo/error` 信号的模块,绝大多数 finding 的 criticality 落在同一档。
- `cross_channel_hits` 在 `cluster=False` 下恒为 1(每个 finding 自成一条),`min(1/2,1)=0.5` 对所有 finding 都一样 → 该项变成常数 `0.15*0.5=0.075`,**完全不提供区分度**。
- 结果:大量 GAP finding 的 score collapse 到 0.6。**119 个并列 0.6**,top-50 边界落在并列带内部,谁进谁出由数组位置(tie-break)决定,**与质量无关**。

KMAC-036(rank 65)、N-005(rank 96)就是这样被挤出去的——它们明明是 HIGH+可形式化。

### 2.2 phase3 截断:固定 top-N,无"质量保底"

`phase3.py:116,127-129`:

```python
top_n: int = 10
...
sorted_findings = sorted(...)[:top_n]   # 硬截断
```

本轮人工设了 dma=25/kmac=50/tlul=30。任何排在 N 之后的 finding 一律不审,**无论 verdict 多强、是否自带 SVA**。排名一旦失真,截断就直接漏。

### 2.3 失配推理:配对粒度是"单 assumption × 候选 guarantees",看不到邻近的对的 atom

`channel_b.py` 的语义单元(`run_channel_b_semantic`)= 一条 query atom + 它的 top-k 候选 guarantee。问题:

- spec_gen 对同一段 RTL 常产出**多条 atom**(一条对的 uncertain_point 讲真机制 + 一条 g_no_msg_mask 的 assumption 讲"高位未驱动")。
- pairing 按 atom 独立成 query。当"高位未驱动"那条 assumption 与某 guarantee 的 dense 相似度更高时,它赢得配对,生成**机制错误**的 contradiction(F-0039 正是如此:配到了 `declarations_or_instances__011` 的"EnMasking=0 高位未驱动",而非 `line_915` 的"static mask 取代 LFSR")。
- 那条**对的 atom**(line_915 U1)单独成 query 时,因为是 uncertain_point、缺乏强 guarantee 对手,只能走 dense_fallback,得低分、沉到尾部。

→ 即"对的信息在 spec 里存在,但被配对粒度切散,且评分机制偏向了错误那条"。

---

## 3. 三处改动方案

> 按"性价比"排序。每条都标注:改哪个文件、怎么改、救回哪些 bug、风险、验证方式。

### 改动 ① 排名:给 score 注入区分度 + 打破并列(低成本、立竿见影)

**目标**:让"HIGH verdict + 可形式化 + 安全信号"的 finding 稳定排在并列带之上,使 top-N 截断不再随机。

**文件**:`rtl_bug_agent/phase2/fusion.py:134-146`

**改法**(方案,不改代码):
1. 重新加权,提升 verdict 强度的权重,降低恒定项:
   ```
   score = 0.45*signal_criticality
         + 0.40*contradiction_strength     # 0.25 → 0.40,让 CONTRADICTION/GAP 拉开
         + 0.15*has_formal_sketch          # 新增:自带可形式化 SVA 的 +满分
   ```
   (移除恒为常数的 cross_channel_hits 项,或在 cluster=False 时跳过。)
2. **加确定性 tie-break**:并列时按 `(verdict_strength, has_sva, num_security_signals, -len(text))` 二级排序,杜绝"靠数组位置决定"。
3. `_signal_criticality` 增加"精确命中 security_signals 计满分"而非关键词 0.5 封顶,提高分辨率。

**救回**:KMAC-036(自带正确 SVA)、KMAC N-005(HIGH+精确机制)——这两个当前纯因并列被截。

**风险**:低。纯排序变化,不改 finding 内容;可能把一些 MEDIUM 往下压,但 phase3 本就该先看强的。

**验证**:对 5 个模块重算 score,确认 F-0065/F-0097 进入各自 top-N;统计"score 并列簇"最大尺寸应从 119 显著下降。

---

### 改动 ② phase3 截断:从"固定 top-N"改为"top-N ∪ 质量保底集"(低成本)

**目标**:即使排名仍不完美,也不让"HIGH + 可形式化 + CONTRADICTION/GAP"的 finding 漏审。

**文件**:`rtl_bug_agent/phase2/phase3.py:112-129`(`verify_top_findings`)

**改法**(方案):
- 送审集合 = `top_n` ∪ `{ verdict ∈ (CONTRADICTION,VIOLATION,GAP) 且 (has_sva 或 severity=HIGH) }`,对保底集设上限(如 +30)防爆。
- 或更简单:`top_n` 之外,把"自带 formal_sketch 的 finding"无条件并入。

**救回**:任何"描述对但排名差"的 bug(本轮的 KMAC-036/N-005,以及未来同类)。与改动①互补:①让排名更准,②兜住①没兜住的。

**风险**:中。会增加 phase3 的 agent 调用量(成本/时间)。需给保底集设硬上限。

**验证**:对比开关前后 phase3 处理条数与新增 CONFIRMED 数;确认保底集上限不被击穿。

---

### 改动 ③ 失配推理:配对时带"同信号邻近 atom"上下文(中成本、价值最高)

**目标**:解决"配错对"——让 channel-B 在判断时能看到同一信号路径上的**所有** atom,而不是被切散的单条。

**文件**:`rtl_bug_agent/phase2/semantic_ag.py:188-260`(`pair_atoms`,构造 query 单元处)+ `channel_b.py:_check_semantic_unit` 的 prompt 组装 + `config/prompts/phase2/channel_b_ag_pairing.md`

**改法**(方案,两选一或叠加):

- **3a(轻)上下文增强**:在 `pair_atoms` 为每个 query 附带"同信号的其它 atom"(已有 `graph.get_structural_facts` 的钩子,`semantic_ag.py:252-257`,可扩展为附带 sibling atoms)。channel-B prompt 增加一段:"以下是与本信号相关的其它声明/不确定点,请优先判断哪条 assumption 才是该 guarantee 真正违背的对象。"
- **3b(重)配对粒度改造**:把 query 单元从"单 assumption"升级为"单信号的 atom 簇"(同 signal 的所有 assumption + uncertain_point 合并成一个推理单元),让 LLM 在簇内自己挑机制。本质是把 phase3 的自由审查能力前移到 pairing。

**救回**:F-0039、TLUL-029、TLUL-028 这类"配错对"——让 pairing 直接产出正确机制,不再依赖 phase3 补救(从而 `is_extra_finding` 比例下降,说明 pipeline 自身变强)。

**风险**:中高。3b 改动配对数据结构,影响面广,需回归现有已命中的 bug(HMAC/AES/Keymgr)确保不退化。建议先做 3a 验证收益。

**验证**:对 F-0039/TLUL-029 重跑 channel-B,确认 contradiction 文本机制正确(`describes_bug_correctly` 从 false→true);对老模块回归确认 CONFIRMED 数不降。

---

## 4. 建议执行顺序

1. **先做 ① + ②**(都在排序/截断层,互补,低风险),立即重跑 5 模块 + phase3,预期至少救回 KMAC-036、N-005,且不依赖 extra。
2. 用 ①② 的结果做基线,**再做 ③a**(上下文增强),对比 `is_extra_finding` 比例是否下降——这是衡量"pairing 自身变强"的关键指标。
3. ③b(配对粒度改造)作为更大投入的后续,需配套老模块回归。

> 衡量成功的核心指标不是"CONFIRMED 总数",而是 **`is_extra_finding=false` 的 CONFIRMED 占比上升** —— 它直接反映 pairing/排名是否真的把 bug 描述对、送对。
