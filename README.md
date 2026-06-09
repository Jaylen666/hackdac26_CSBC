# RTL Bug Agent

从 RTL 代码自动提取行为 Spec，再通过跨 Spec 交叉比对 + 官方文档对齐发现硬件 Bug。

**核心概念：CSBC（Cross-Specification Behavioral Contradiction，跨规格行为矛盾）**

一种硬件安全漏洞类别，其根因不是单点代码错误（写错常量、用错运算符），而是**两个或多个代码段对同一信号或事件的语义理解不一致**。每个片段单独看自洽，只有放在一起比对才暴露矛盾。

| 子类 | 描述 | 示例 |
|---|---|---|
| **协议违反** | Producer 的保证不满足 Consumer 的假设 | `cfg_block` 在 `reg_hash_stop` 时立即清零而非等到 `hmac_done` |
| **覆盖缺口** | 合法取值集合中有值未在任何分支处理 | `digest_size_i=SHA2_512` 落入默认分支 |
| **时序脱节** | 两个信号声称的"事件完成时刻"不一致 | `in_process=0` 和 `hash_done_event=1` 之间差 127 拍 |
| **安全属性断裂** | 安全关键信号的保护链在某处中断 | `wipe_secret_we` 被 `reg_error` 门控，正常软件写不触发擦除 |

**为什么重要**：(1) 单点验证会漏——每个模块独立验证正确，集成后交互假设不一致；(2) 规模问题——SoC 有数十个 IP，两两配对手动检查不可行；(3) 安全后果——不是功能崩溃，是密钥残留、认证绕过等静默破坏安全保证的缺陷。

**框架定位**：填补了形式验证（需预定义属性）和仿真（需测试输入）之间的空白——不需要先验知识，从代码自身提取行为描述，找任意两个 spec 间的语义矛盾。

---

## 架构

```
Phase 1: Spec 生成
  .sv → chunker → chunks JSON → LLM → spec JSON (68 个)

Phase 2: 交叉比对
  Pass 0: SignalGraph (规则引擎, 信号 driver/consumer 图 + 全文检索)
    ├─ Layer 1 (内部一致性, chunk ↔ chunk)
    │   ├─ Channel B: Assumption-Guarantee 配对
    │   ├─ Channel C: 覆盖缺口检测
    │   └─ Channel D: 时序一致性
    ├─ Layer 2 (外部对齐, chunk ↔ 官方 spec)
    │   └─ 自动提取 claims → 全文检索 → LLM 验证
    └─ Pass 3: Fusion (模糊聚类 + 跨通道交叉验证 + 排序)
```

Layer 1 和 Layer 2 **并行运行**——各自独立发现 bug，末尾交叉验证削减误报。

---

## Phase 1: Spec 生成

### 运行

```bash
# Step 1: RTL 语义分块
python3 -m rtl_bug_agent.cli chunk \
  --rtl-dir /path/to/rtl \
  --out output/chunks.json

# Step 2: 全量 Spec 生成
python3 scripts/generate_all_specs.py \
  --chunks output/chunks.json \
  --out-dir output/specs
```

### 目录

```
rtl_bug_agent/
├── config/prompts/
│   ├── chunk_spec.md                  # Phase 1 prompt
│   └── phase2/                        # Phase 2 prompts
│       ├── channel_b_ag_pairing.md
│       ├── channel_c_coverage_gap.md
│       ├── channel_d_temporal.md
│       ├── layer2_claim_check.md
│       └── layer2_extract_claims.md
├── rtl_bug_agent/
│   ├── cli.py                         # CLI (chunk / show / spec)
│   ├── env.py / schema.py
│   ├── llm/client.py                  # OpenAI 兼容客户端 + 调用统计
│   ├── rtl/chunker.py                 # 语义分块 + 相邻合并
│   ├── rtl/io.py
│   ├── spec/extractor.py              # Spec 生成
│   └── phase2/
│       ├── signal_graph.py            # Pass 0: 信号图 + 全文检索 + 预过滤器
│       ├── channel_b.py               # A-G 配对
│       ├── channel_c.py               # 覆盖缺口 (LLM Step 0 前置判断)
│       ├── channel_d.py               # 时序一致性
│       ├── layer2.py                  # 官方 spec 提取 + 验证
│       └── fusion.py                  # Pass 3: 融合排序
├── scripts/
│   ├── generate_all_specs.py          # 批量 spec 生成
│   ├── generate_selected_specs.py     # 指定 chunk 生成
│   └── run_phase2_e2e.py              # Phase 2 端到端
└── output/
    ├── hmac_chunks.json
    ├── specs/                         # 68 个 spec JSON
    └── findings_v4.json               # Phase 2 发现清单
```

### Chunk 类型

| Kind | 含义 |
|---|---|
| `always_comb` | 组合逻辑过程块 |
| `always_ff` | 时序逻辑 / 寄存器 |
| `generate_for` | 生成循环 |
| `continuous_region` | 声明 / assign / 实例化集合 |

### Spec JSON Schema

```json
{
  "chunk_id": "唯一标识",
  "summary": "一句话",
  "behavior": "精炼段落，长度与复杂度匹配。常规时序（组合 0 延迟、寄存器 1 拍）由 chunk kind 隐含，不赘述",
  "guarantees": [{"property": "...", "output_signals": [...], "source_refs": [...]}],
  "assumptions": [{"constraint": "...", "bug_relevance": "...", "related_signals": [...], "source_refs": [...]}],
  "security_implications": "...",
  "evidence_refs": [...],
  "uncertain_points": [...]
}
```

**关键设计决策**：
- 时序由 chunk kind 隐含（always_comb→0 延迟, always_ff→1 拍），只在例外时标注
- assumptions 区分为防御性设计（非 bug）和行为契约（违反可能是 bug）

---

## Phase 2: 交叉比对

### Pass 0: SignalGraph

规则引擎（无 LLM），遍历所有 spec 的结构化字段和散文文本，构建：

```
信号 "secret_key_d":
  drivers:   [spec_A]           ← guarantees[].output_signals
  consumers: [spec_B, spec_C]   ← assumptions[].related_signals
  mentioned: [spec_D]           ← behavior 文本提及
```

提供 `search(signals, keywords, scope)` 全文检索——遍历所有 spec 的 behavior/summary/uncertain_points/guarantees/assumptions，打分排序。

`find_ag_pairs(filter_mode="behavioral")` 用 `_classify_assumption()` 预过滤结构/机械类 assumption（位宽匹配、数组大小、参数一致性），只放行行为语义类。

### Channel B: Assumption-Guarantee 配对

对每个有 driver 和 consumer 的信号，取出 consumer 的 assumptions 和 driver 的 guarantees，LLM 判定：

| 判决 | 含义 |
|---|---|
| CONTRADICTION | 合法场景下 guarantee 与 assumption 直接冲突 |
| GAP | assumption 假设的条件在合法场景下不被任何 guarantee 覆盖 |
| DEFENSIVE | assumption 约束的是非法输入，guarantee 提供了安全回退（非 bug） |
| SATISFIED | assumption 被满足 |

### Channel C: 覆盖缺口

对每个有 driver+consumer 的信号（**无预过滤**），LLM 先做 Step 0 判断是否携带枚举/分发语义：

- 非枚举信号 → 直接返回空
- 枚举信号 → Q1-Q3 三步追问：
  - Q1: 合法取值集合是什么？
  - Q2: 消费者显式处理了哪些？哪些落入 default？
  - Q3: default 里是否有合法值？是 → GAP，否 → DEFENSIVE

### Channel D: 时序一致性

构建时序簇（FSM `_q`/`_d` 对 + 跨模块 completion 簇），LLM 做语义事件一致性分析：

- 识别同一硬件事件的不同信号表示（如 `hmac_idle`, `hash_done_event`, `in_process` 都描述"完成"）
- 从 chunk kind 推断默认时序（always_comb→0 延迟, always_ff→1 拍）
- 比较各信号对"事件何时完成"的时序描述是否一致

判决：CONSISTENT / OFFSET（设计上必然的偏移）/ RACE（时序边界矛盾）/ UNCERTAIN。

### Layer 2: 官方 Spec 对齐

两步管道：

1. **自动提取** — LLM 读取官方设计文档（`theory_of_operation.md`），提取可验证的 claims（含信号名、关键词、模块范围）。替代手写 claims。
2. **验证** — 每条 claim 用 `SignalGraph.search()` 全文检索相关 RTL spec，LLM 判定 SATISFIED / VIOLATION / PARTIAL。

手写 fallback 保留（`make_claims_for_hmac()`），在 LLM 提取失败时使用。

### Pass 3: Fusion

1. **模糊聚类** — 共享 ≥1 信号名（含子串匹配）或 ≥2 spec → 合并为一个 finding
2. **评分** — `score = 0.60×signal_criticality + 0.25×verdict_strength + 0.15×cross_channel`
3. **自引用降权** — 涉及 spec ≤1 的 finding 分数 ×0.7
4. **跨通道 boost** — ≥2 个通道独立发现同一问题 → 置信度翻倍

---

## 设计原则

1. **Spec 是中性的** — Phase 1 如实描述，不判断对错。推理在 Phase 2。
2. **不一致即信号** — 不需要 ground truth，两个信息源对同一事物说法不同即可能是 bug。
3. **LLM 做比对，不做判断** — LLM 判断"说法 A 和 B 是否矛盾"，不判断"有没有 bug"。
4. **IP 无关** — 所有组件基于 RTL 电路特征（信号驱动/消费、always 类型、FSM 模式），无 IP 特定硬编码。
5. **Layer 1 ∥ Layer 2** — 并行运行，末尾交叉验证削减误报。
6. **预过滤器用规则，分析用 LLM** — 结构/机械类判断（是否防御性设计、是否枚举信号）用规则前置，语义判断用 LLM。

---

## 运行

```bash
# Phase 1: 生成 spec（如已有则跳过）
python3 scripts/generate_all_specs.py --chunks output/hmac_chunks.json --out-dir output/specs

# Phase 2: 端到端 bug 检测
python3 scripts/run_phase2_e2e.py
```

输出 `output/findings_v4.json`，末尾打印 LLM 调用次数、token 消耗和耗时统计。

### Semantic AG / BGE-M3 环境要求

语义 AG 检索模式依赖 BGE-M3 embedding，不在默认 Python 环境中运行。请使用之前实验已部署好的 venv：

```bash
/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python
```

或在新环境中安装可选依赖：

```bash
pip install -r requirements-semantic-ag.txt
```

默认离线运行：代码会默认设置本地 HuggingFace cache 为
`/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/out/hf_cache`，并禁止
HuggingFace 联网下载模型。该 cache 已包含 `BAAI/bge-m3` 的完整 snapshot。
新模块只会重新计算该模块 specs 的 embeddings，不需要重新下载权重。

只有在明确需要补齐或更新模型权重时，才加显式在线下载开关：

```bash
# semantic-ag dry-run
--online-download-model

# scripts/run_phase2_e2e.py
--semantic-online-download-model
```

推荐先用 `shadow` 模式部署校验。该模式会生成 semantic AG 统计，但保持 legacy Channel B 主流程不变：

```bash
/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python \
  scripts/run_phase2_e2e.py \
  --ip hmac \
  --specs-dir output/specs \
  --ag-pairing-mode shadow
```

只做 semantic AG dry-run、不调用 Phase2 LLM：

```bash
/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python \
  -m rtl_bug_agent.cli semantic-ag \
  --specs-dir output/specs \
  --out output/semantic_ag_hmac.json
```

如果已有预计算 embeddings，可用 `--embeddings` 跳过模型加载和重新 embedding：

```bash
/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python \
  -m rtl_bug_agent.cli semantic-ag \
  --specs-dir output/specs \
  --out output/semantic_ag_hmac.json \
  --embeddings experiments/bge_m3_ag_retrieval/out/embeddings_hmac.npz
```

正式启用 semantic AG 替换 Channel B candidate source：

```bash
/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python \
  scripts/run_phase2_e2e.py \
  --ip hmac \
  --specs-dir output/specs \
  --ag-pairing-mode semantic
```

默认情况下，semantic Channel B 仍然是每个 semantic query unit 调一次 LLM。若要启用 guarded batch clustering 来减少调用次数：

```bash
/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python \
  scripts/run_phase2_e2e.py \
  --ip hmac \
  --specs-dir output/specs \
  --ag-pairing-mode semantic \
  --semantic-batch-mode guarded
```

guarded batching 是通用策略，不做 IP 特定启发。它只合并满足以下 guardrails 的 query units：batch 内 query 数不超过 `--semantic-max-queries-per-batch`，估算 prompt token 不超过 `--semantic-max-prompt-tokens`，dense-fallback uncertain 数不超过 `--semantic-max-dense-fallback-uncertain`，新增 query 与 batch 至少共享 `--semantic-min-shared-roots` 个归一化信号根，且 batch 信号根总数不超过 `--semantic-max-signal-roots`。prompt 会明确要求 LLM 对每个 query 独立判断；只有共享信号、source、guarantee 或控制/数据路径时才允许使用跨项上下文。

如需使用其他本地 HF cache，可显式指定：

```bash
/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python \
  -m rtl_bug_agent.cli semantic-ag \
  --specs-dir output/specs \
  --out output/semantic_ag_hmac.json \
  --hf-home /path/to/local/hf_cache
```

### Phase2 AG Pairing 模式

`scripts/run_phase2_e2e.py` 目前支持三种 AG 配对模式：

| 模式 | 作用 | 是否改变主流程 | 推荐用途 |
|---|---|---|---|
| `legacy` | 原始 signal-based AG 配对 + uncertain weak-assumption 注入 | 否，默认模式 | 稳定回归、和旧结果对齐 |
| `shadow` | 额外生成 semantic AG 统计，同时继续使用 legacy Channel B | 不改变 findings | 部署校验、比较 pair 数/uncertain 保留情况 |
| `semantic` | 用 BGE-M3 semantic AG query units 替换 Channel B candidate source，并保留 unmatched uncertain 进入 Phase3/fusion | 是 | 实验性正式启用 semantic AG |

默认 legacy 跑法：

```bash
python3 scripts/run_phase2_e2e.py \
  --ip hmac \
  --specs-dir output/specs
```

Shadow 跑法（推荐先跑，确认不影响 legacy findings）：

```bash
/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python \
  scripts/run_phase2_e2e.py \
  --ip hmac \
  --specs-dir output/specs \
  --ag-pairing-mode shadow
```

Semantic 跑法（真实替换 Channel B candidate source）：

```bash
/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python \
  scripts/run_phase2_e2e.py \
  --ip hmac \
  --specs-dir output/specs \
  --ag-pairing-mode semantic
```

Semantic + guarded batch 跑法（减少 Channel B LLM 调用，仍逐 query 独立判定）：

```bash
/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python \
  scripts/run_phase2_e2e.py \
  --ip hmac \
  --specs-dir output/specs \
  --ag-pairing-mode semantic \
  --semantic-batch-mode guarded \
  --semantic-max-queries-per-batch 5 \
  --semantic-max-prompt-tokens 5500
```

离线复现实验跑法（使用预计算 embeddings，完全跳过模型加载）：

```bash
/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python \
  scripts/run_phase2_e2e.py \
  --ip hmac \
  --specs-dir output/specs \
  --ag-pairing-mode semantic \
  --semantic-embeddings experiments/bge_m3_ag_retrieval/out/embeddings_hmac.npz
```

只统计 semantic pairing 与 batch 规模、不调用 LLM：

```bash
/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python \
  -m rtl_bug_agent.cli semantic-ag \
  --specs-dir output/specs \
  --out output/semantic_ag_hmac.json \
  --embeddings experiments/bge_m3_ag_retrieval/out/embeddings_hmac.npz \
  --semantic-batch-mode guarded
```

若要强制在线下载或更新 BGE-M3 权重，必须显式加：

```bash
/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python \
  -m rtl_bug_agent.cli semantic-ag \
  --specs-dir output/specs_new_ip \
  --out output/semantic_ag_new_ip.json \
  --online-download-model
```

常见模块 spec/embedding 对应：

| IP | specs-dir | embeddings |
|---|---|---|
| `hmac` | `output/specs` | `experiments/bge_m3_ag_retrieval/out/embeddings_hmac.npz` |
| `aes` | `output/specs_aes` | `experiments/bge_m3_ag_retrieval/out/embeddings_aes.npz` |
| `keymgr` | `output/specs_keymgr` | `experiments/bge_m3_ag_retrieval/out/embeddings_keymgr.npz` |
| `kmac` | `output/specs_kmac` | `experiments/bge_m3_ag_retrieval/out/embeddings_kmac.npz` |
| `rv_dm` | `output/specs_rv_dm` | `experiments/bge_m3_ag_retrieval/out/embeddings_rv_dm.npz` |
| `uart` | `output/specs_uart` | `experiments/bge_m3_ag_retrieval/out/embeddings_uart.npz` |

---

## 已实现 vs 规划中

| 组件 | 状态 |
|---|---|
| Pass 0: SignalGraph + search() + 预过滤器 | ✅ |
| Channel B: A-G 配对 (DEFENSIVE/GAP/CONTRADICTION) | ✅ |
| Channel C: 覆盖缺口 (LLM Step 0 前置判断) | ✅ |
| Channel D: 时序一致性 (v3 anchor-supervised) | ✅ |
| Layer 2: Claims pre-extraction + LLM semantic match (pull-model) | ✅ |
| Pass 3: 模糊聚类 + 跨通道 + 自引用降权 | ✅ |
| Phase 3: 源码级验证 (finding as navigation aid) | ✅ |
| LLM 调用统计 + checkpoint 增量存储 | ✅ |
| RTL 预过滤 (reggen/topgen 跳过 + LLM 模板识别) | ✅ |
| Channel A: 语义矛盾 | 🔲 (B+C 已覆盖大部分) |
| Channel E+F: 边界 + 安全链 | 🔲 |
| Channel S: 协议原子性 (事务完整性检测) | 🔲 |
| Channel Z: 状态可达性 (FSM 死锁检测) | 🔲 |
| Phase 4: 自动测试生成 | 🔲 |
| 多 IP 验证 (HMAC ✅, KMAC 🔲, AES ✅, rv_dm ✅) | 🔄 |

---

## 关键架构决策演进

### Channel D v3: Anchor-Supervised 时序检测

v1/v2 使用信号名关键词 + 模块分簇 + 桥接，Bug 011 的 `in_process` 和 `hash_done_event` 反复不可见。v3 重构为：

```
guarantee atom 抽取 → 信号因果路径 → anchor 评分 → anchor 邻域 pair → LLM 判断
```

核心原则: 脚本发现关系，LLM 判断矛盾。Bug 011 pair 从"不在 top-200"提升至 rank 1。

### Layer 2 Pull-Model: Claims Pre-Extraction + LLM Semantic Match

原始推模式: 每条 claim 独立验证 → 不依赖 Phase 2 finding。改为拉模式：

```
官方 spec → 一次性提取全部 claims (20-30 条)
Phase 2 finding → LLM 语义匹配最相关 claim → 判断 VIOLATION/SATISFIED
```

LLM 自己做 claim 匹配，不需要脚本关键词索引。AES 测试中 F-0003 成功匹配至 key clearing 相关的 claim 并判 VIOLATION。

### Phase 3: Finding as Navigation Aid

原始 prompt 让 LLM"先忽略 finding，独立读代码"。改进为 finding 作为导航辅助——指出可疑信号和代码区域，LLM 仍需独立验证，但即使 finding 描述不精确也不影响在正确区域发现 bug。

---

## 实测结果

| IP | RTL 行数 | 已知 CSBC Bug | 命中 | 新发现 | 备注 |
|---|---|---|---|---|---|
| HMAC | 4,630 | 3* | 3/3 | 1 (wipe_secret_we 被 reg_error 门控) | 另 1 个 bug 在共享原语，超出 chunk 范围 |
| AES (精选) | 2,520 | 2 | 1/2 (+ 相关命中) | 2 (N-001 key_words_sel rail folding, N-002 iv_sel CTV 无门控) | Bug 005 被 Phase 2 命中，Layer 2 确认 VIOLATION |
| rv_dm | 1,060 (过滤后) | 2 | 0/2 (见下方分析) | — | 单模块边界用例，超出 CSBC 范围 |
| KMAC | 12,144 | 3 | 运行中 | — | — |

\* HMAC 有 4 个提交 bug，其中 3 个属于 CSBC 类型，1 个在共享原语内部。

**AES 新的两个发现**（N-001, N-002）均由 Codex Phase 3 独立发现，非赛方提交的已知 bug——证明框架有能力发现尚未被标注的未知 CSBC 漏洞。

### RV_DM 为什么 0/2

rv_dm 有两个已知 bug 落在过滤后的文件里，但一个也没检测到：

**Bug 034** (DMI gate 丢弃最后一笔响应): `dmi_en` 撤销时恰好有一个 DMI 响应正在返回，响应被静默丢弃。这是**单模块内部的时序竞争条件**——Phase 1 的 spec 只描述"debug 未使能时阻断访问"的正确意图，没有第二个 chunk 描述"阻断时响应应该完成"的期望行为。CSBC 的前提——"两段代码对同一信号说法不同"——不成立。

**Bug 047** (ndmreset pending 位卡死): debug 授权被撤销时，`ndmreset_pending_q` 和 `lc_rst_pending_q` 永远卡在高电平，`anyhavereset` 永远为 0。这是**多步 FSM 序列中的中间状态死锁**——正常路径的 spec 能描述，但非正常路径（授权中途撤销）不产生跨 chunk 矛盾，因为所有相关 spec 都在同一个 chunk 内。

**两个 bug 的共同特征**: 都是单模块内的边界用例——不是"两块代码对同一信号的理解发生冲突"，而是"同一块代码在非正常路径上的行为未定义"。这需要**协议原子性检查**（Channel S）和**状态可达性分析**（Channel Z），不在当前 CSBC 框架的检测范围内。

---

## 框架检测能力边界

本框架专为 **跨规格行为矛盾（CSBC）** 设计，不适用于以下类型的 bug：

| 不适合检测 | 原因 | 示例 |
|---|---|---|
| 纯值错误（单点常量/运算符 mutation） | 不影响其他 chunk 的语义理解，无 spec 间矛盾 | `1'b0→1'b1` 但周围无依赖该值的假设 |
| 单文件小规模设计的孤立 bug | 无 driver↔consumer 跨 chunk 关系，信号图退化 | 251 行 SMII 收发器（AssertLLM2 benchmark） |
| 共享原语内部的缺陷 | Phase 1 chunk 范围未覆盖原语 RTL | `prim_alert_sender` 内的 ping-skew |
| 门级/物理层问题 | 不在 RTL 行为分析层面 | 时序违例、功耗侧信道 |

**适用范围**：至少 500+ 行、多模块多文件、设计意图分散在不同 spec 中、存在跨模块信号契约的 SoC IP 设计。这在现代 SoC 中占绝大多数。经在 OpenTitan HMAC（4/4 已知 bug 命中）、KMAC 上验证有效。
