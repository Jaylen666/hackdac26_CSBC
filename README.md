# RTL Bug Agent

从 RTL 代码自动提取行为 Spec，通过跨 Spec 交叉比对发现硬件 Bug。

**核心概念：CSBC（Cross-Specification Behavioral Contradiction）** — 两个代码段对同一信号的语义理解不一致，每段单独看自洽，放在一起才暴露矛盾。

## 环境配置

### 1. 环境变量

在 `~/.env` 中配置 LLM API 密钥：

```bash
# Phase 2 批量分析 (DeepSeek)
GUOCHUANG_DEEPSEEK_API_KEY="sk-xxx"
GUOCHUANG_DEEPSEEK_BASE_URL="https://api.deepseek.com"
GUOCHUANG_DEEPSEEK_MODEL="deepseek-v4-pro"

# Phase 3 源码级验证 (GPT-4/Codex)
OPENAI_API_KEY="sk-xxx"
OPENAI_BASE_URL="https://api.openai.com"
OPENAI_MODEL="gpt-5.4"
```

### 2. Python 依赖

```bash
pip install -r requirements-semantic-ag.txt  # BGE-M3 embedding (可选)
```

系统 `python3` 即可运行大部分脚本。BGE-M3 语义嵌入需使用独立的 venv（实验目录下有预装好的 `.venv`），模型权重需离线缓存到本地 HuggingFace cache。

### 3. 本地 BGE-M3 模型缓存（离线运行）

模型本地路径示例：`experiments/bge_m3_ag_retrieval/out/hf_cache/`

默认离线运行，设置以下环境变量：
```python
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
HF_HOME=/path/to/local/hf_cache
```

如尚未下载权重，需先在线下载一次 BAAI/bge-m3，之后始终离线。

---

## 快速开始

### 对新 IP 跑完整流程

```bash
cd /home/smy/rtl_bug_agent

# Step 1: RTL 分块
python3 -m rtl_bug_agent.cli chunk \
  --rtl-dir /path/to/rtl \
  --out output/<ip>_chunks.json

# Step 2: 批量生成 Spec
python3 scripts/generate_all_specs.py \
  --chunks output/<ip>_chunks.json \
  --out-dir output/specs_<ip>

# Step 3: Phase 2 端到端 (semantic AG 模式)
/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python \
  scripts/run_phase2_e2e.py \
  --ip <ip> \
  --specs-dir output/specs_<ip> \
  --ag-pairing-mode semantic \
  --semantic-batch-mode guarded \
  --phase3-top-n 20 \
  --workers 8
```

输出：`output/findings_<ip>.json`

### 对已有 Spec 的模块

跳过 Step 1-2，直接跑 Phase 2：

```bash
/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python \
  scripts/run_phase2_e2e.py \
  --ip hmac \
  --specs-dir output/specs \
  --ag-pairing-mode semantic \
  --semantic-batch-mode guarded \
  --phase3-top-n 20 \
  --workers 8
```

### 只重跑 Spec 生成（跳过已有成功的）

把需要重跑的 chunk 列表传给：
```bash
python3 scripts/generate_selected_specs.py \
  --chunks output/<ip>_chunks.json \
  --specs-dir output/specs_<ip> \
  --ids chunk_id1,chunk_id2
```

---

## 架构

```
Phase 1: Spec 生成
  .sv → chunker → LLM → spec JSON (behavior / guarantees / assumptions / uncertain_points)

Phase 2: 交叉比对
  Pass 0: SignalGraph (信号 driver/consumer 图)
    ├─ Channel B: BGE-M3 Semantic AG 配对 (assumption vs guarantee)
    ├─ Channel C: 覆盖缺口检测
    ├─ Channel D: 时序一致性
    └─ Layer 2: 官方文档 spec 对齐

  Fusion: 模糊聚类 + 跨通道交叉验证 + 打分排序

Phase 3: 源码级验证 (Codex/GPT-5.4)
  读取原始 .sv 文件，独立验证 Phase 2 findings
```

### Channel B: BGE-M3 Semantic AG 配对 (核心通道)

```
Spec atoms → BGE-M3 嵌入 → AG 配对评分:
  score = 0.8 × cosine(emb_q, emb_c) + 0.2 × signal_overlap(q, c)

语义剪枝: threshold ≥ 0.66, top-K=5
筛选后的 pair 送 LLM 做 mismatch 分析:
  CONTRADICTION | GAP | DEFENSIVE
```

### 运行模式

| 模式 | 命令 flag | 说明 |
|------|----------|------|
| `legacy` | 默认 | 原始信号名 AG 配对 |
| `semantic` | `--ag-pairing-mode semantic` | BGE-M3 语义 AG (推荐) |
| `shadow` | `--ag-pairing-mode shadow` | 同时跑 semantic + legacy, 不改变主流程 |

---

## 目录

```
rtl_bug_agent/
├── config/prompts/
│   ├── chunk_spec.md                    # Phase 1 spec 生成 prompt
│   └── phase2/                          # Phase 2/3 prompts
│       ├── channel_b_ag_pairing.md
│       ├── channel_c_coverage_gap.md
│       ├── channel_d_temporal.md
│       ├── layer2_claim_check.md
│       ├── layer2_extract_claims.md
│       └── phase3/
│           └── verify.md
├── rtl_bug_agent/                       # 核心库
│   ├── cli.py
│   ├── llm/client.py                    # OpenAI 兼容客户端
│   ├── rtl/chunker.py                   # RTL 语义分块
│   ├── spec/extractor.py                # Spec 生成
│   └── phase2/
│       ├── signal_graph.py              # 信号图 + 全文检索
│       ├── semantic_ag.py               # BGE-M3 嵌入 + AG 配对
│       ├── channel_b.py                 # AG 配对 + LLM 分析
│       ├── channel_c.py                 # 覆盖缺口
│       ├── channel_d.py                 # 时序一致性
│       ├── layer2.py                    # 官方 spec 对齐
│       ├── layer2_pull.py               # Pull-model spec 对齐
│       ├── uncertain_collector.py       # U-UP 通道
│       ├── phase3.py                    # 源码级验证
│       └── fusion.py                    # 融合排序
├── scripts/
│   ├── generate_all_specs.py
│   ├── generate_selected_specs.py
│   └── run_phase2_e2e.py
├── output/                              # 各 IP 运行结果
│   ├── *_chunks.json                    # Phase 1 产物
│   ├── specs_*/                         # Phase 1 specs
│   ├── findings_*.json                  # Phase 2+3 findings
│   └── semantic_ag_*.json               # Semantic AG 结果
├── tests/
├── demo/                                # N-003 发现过程 demo
└── experiments/                         # (不上传) 开发实验 + 模型缓存
```

---

## 脚本速查

| 脚本 | 用途 |
|------|------|
| `scripts/run_phase2_e2e.py` | Phase 2+3 端到端主入口 |
| `scripts/generate_all_specs.py` | 为所有 chunk 生成 spec |
| `scripts/generate_selected_specs.py` | 为指定 chunk 重试 spec |
| `python3 -m rtl_bug_agent.cli chunk` | RTL 语义分块 |
| `python3 -m rtl_bug_agent.cli semantic-ag` | 单独跑 semantic AG (不调 LLM) |
| `demo/n003_discovery.py` | N-003 发现过程离线 demo |

### 常用参数 (`run_phase2_e2e.py`)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--ip` | `hmac` | IP 名称 |
| `--specs-dir` | `output/specs_<ip>` | Spec 目录 |
| `--ag-pairing-mode` | `legacy` | `legacy` / `semantic` / `shadow` |
| `--semantic-batch-mode` | `single` | `single` / `guarded` |
| `--phase3-top-n` | `0` | Phase 3 验证 top-N findings (0=跳过) |
| `--workers` | `8` | 并行 LLM 调用数 |
| `--force` | `false` | 忽略 checkpoint 重新跑 |

---

## 已测试 IP

| IP | RTL 行数 | 已知Bug总数 | 命中 | 新发现 |
|---|---|---|---|---|
| HMAC | 4,630 | 6 | 5/6 | 2 |
| AES | 2,520 | 4 | 4/4 | 2 |
| KMAC | 12,144 | 4 | 1/4 | 1 |
| Keymgr | ~3,000 | 3 | 2/3 | 0 |
| UART | ~1,500 | 2 | 2/2 | 1 |
| RV_DM | 1,060 | 2 | 0/2 | 0 |

---

## 框架检测边界

适用于：**跨模块/跨 chunk 信号契约矛盾** (CSBC)，多模块 SoC IP (500+ 行)。

不适于：
- 单 always 块内部纯值错误
- 单模块 FSM 死锁 / 状态可达性问题 (需 Channel Z)
- 共享原语内部缺陷 (Phase 1 chunk 范围未覆盖)
- 时序竞争条件 (需 Channel S)
