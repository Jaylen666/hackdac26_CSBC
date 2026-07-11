# RTL Bug Agent

RTL Bug Agent 从 OpenTitan RTL 和官方文档中提取行为 spec，再通过跨 spec 的 Assumption-Guarantee（A-G）分析发现潜在硬件 bug。

当前项目维护和复现的是精简版 CSBC v3 流程：

```text
RTL
  -> RTL 分块（chunk）
  -> LLM 生成行为 spec
  -> BGE-M3 semantic Channel B
  -> LLM 分析 A-G 关系
  -> 官方 ref 抽取
  -> ref forward/reverse 匹配
  -> findings_<ip>.json
```

当前完整端到端流程只运行：

1. RTL 分块和 spec 生成
2. semantic Channel B
3. 官方 ref 抽取和双向匹配

当前流程不运行 Channel C、Channel D、Channel F、Layer 2、formal、structure mode 或 Phase 3。保留的 Phase 3 prompt 和历史 Phase 3 结果仅用于后续实验和审计。

## 1. 环境

### 1.1 项目和 RTL

项目目录：

```text
/home/smy/rtl_bug_agent
```

OpenTitan RTL 默认位于：

```text
/home/smy/opentitan/hw/ip/<ip>/rtl/
```

官方 ref 抽取默认读取对应 IP 下的：

```text
/home/smy/opentitan/hw/ip/<ip>/data/*.hjson
/home/smy/opentitan/hw/ip/<ip>/doc/theory_of_operation.md
```

`tlul` 使用其专用的 `doc/TlulProtocolChecker.md`。

### 1.2 LLM 环境变量

在 `/home/smy/.env` 中配置 Phase 1、Channel B 和 ref 抽取使用的 provider：

```dotenv
GUOCHUANG_DEEPSEEK_API_KEY=your-api-key
GUOCHUANG_DEEPSEEK_BASE_URL=https://your-provider.example/v1
GUOCHUANG_DEEPSEEK_MODEL=your-model
```

不要把 API key 写入项目文件、脚本、结果 JSON 或 Git。

### 1.3 BGE-M3

项目已经保留离线运行环境和模型缓存：

```text
experiments/bge_m3_ag_retrieval/.venv/
experiments/bge_m3_ag_retrieval/out/hf_cache/
```

推荐使用该虚拟环境运行完整流程：

```bash
cd /home/smy/rtl_bug_agent
VENV_PY=/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python
export HF_HOME=/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/out/hf_cache
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
```

## 2. 目录结构

```text
rtl_bug_agent/
├── config/
│   └── prompts/
│       ├── chunk_spec_agu_structured_slim_en.md
│       ├── phase2/
│       │   ├── channel_b_ag_pairing.md
│       │   └── ref_extract.md
│       └── phase3/
│           └── verify_agent.md
├── rtl_bug_agent/
│   ├── cli.py
│   ├── env.py
│   ├── llm/                  # OpenAI-compatible LLM client
│   ├── rtl/                  # RTL 分块和文件读写
│   ├── spec/                 # 行为 spec 生成
│   └── phase2/
│       ├── signal_graph.py   # spec 信号图
│       ├── semantic_ag.py    # BGE-M3 semantic A-G pairing
│       ├── channel_b.py      # Channel B LLM 分析
│       ├── ref_extract.py    # pipeline 内部的 ref 抽取
│       ├── ref_match.py      # pipeline 内部的 ref 匹配
│       └── fusion.py         # findings 汇总排序
├── scripts/
│   ├── run_csbc.sh           # 推荐：单模块完整流程
│   ├── run_csbc_batch_bg.sh  # 多模块后台串行运行
│   ├── _csbc_batch_runner.sh # batch runner 内部脚本
│   ├── generate_all_specs.py # 批量生成 spec
│   ├── generate_selected_specs.py
│   ├── run_phase2_e2e.py     # 可配置主入口，当前使用 --channels B
│   ├── extract_ref.py        # 独立 ref 抽取入口
│   ├── ref_match.py          # 独立 ref 匹配库
│   └── run_ref_match.py      # 独立 ref 匹配入口
├── output/
│   ├── <ip>_chunks.json
│   ├── specs_<ip>/
│   ├── .checkpoint_<ip>_B_semantic.json
│   ├── semantic_ag_shadow_<ip>.json
│   ├── findings_<ip>.json
│   ├── phase3_*_input.json                 # 历史 Phase 3 输入
│   ├── phase3_*_rerun_gpt55_xhigh_dualclaim.json
│   ├── ref_out/<ip>_ref_raw.json
│   └── .semantic_ag_cache/                 # 可重建的 embedding cache
├── experiments/
│   ├── bge_m3_ag_retrieval/.venv/
│   ├── bge_m3_ag_retrieval/out/hf_cache/
│   └── signal_only_ablation/               # 当前 CSV 和分析文档
└── README.md
```

旧脚本、旧结果、旧实验和测试文件已经移动到：

```text
/home/smy/rtl_bug_agent_copy/
```

## 3. 推荐的一键流程

对一个新 IP，运行：

```bash
cd /home/smy/rtl_bug_agent
bash scripts/run_csbc.sh rv_dm
```

可用模块名包括：

```text
aes dma hmac keymgr kmac rv_dm soc_dbg_ctrl tlul uart
```

例如：

```bash
bash scripts/run_csbc.sh aes
bash scripts/run_csbc.sh hmac
bash scripts/run_csbc.sh keymgr
```

`run_csbc.sh` 执行以下步骤：

1. 使用 `rtl_bug_agent.cli chunk` 对 RTL 分块。
2. 使用 `generate_all_specs.py` 为所有 chunk 生成行为 spec。
3. 使用 `run_phase2_e2e.py --channels B --ag-pairing-mode semantic` 运行 semantic Channel B。
4. 在同一个 Phase 2 运行中抽取官方 ref，并执行 forward/reverse 匹配。

该脚本不会启用 trace、formal、structure mode、其他 channel 或 Phase 3。

主要输出位于 `output/`：

```text
output/<ip>_chunks.json
output/specs_<ip>/
output/.checkpoint_<ip>_B_semantic.json
output/semantic_ag_shadow_<ip>.json
output/ref_out/<ip>_ref_raw.json
output/findings_<ip>.json
```

## 4. 分阶段运行

### 4.1 RTL 分块

只执行分块：

```bash
cd /home/smy/rtl_bug_agent
$VENV_PY -m rtl_bug_agent.cli chunk \
  --rtl-dir /home/smy/opentitan/hw/ip/rv_dm/rtl \
  --out output/rv_dm_chunks.json \
  --prefilter
```

`--prefilter` 会使用 LLM 过滤部分结构性代码。若不希望调用预过滤 LLM，可以去掉该选项。

### 4.2 批量生成 spec

默认使用 `config/prompts/chunk_spec_agu_structured_slim_en.md`。如需实验其他 spec prompt，可以通过 `--prompt` 显式指定；不指定时使用该默认文件。

```bash
$VENV_PY scripts/generate_all_specs.py \
  --chunks output/rv_dm_chunks.json \
  --out-dir output/specs_rv_dm \
  --provider GUOCHUANG_DEEPSEEK \
  --workers 8
```

已有 spec 文件会被跳过。生成失败的 chunk 可以用 `generate_selected_specs.py` 单独重试：

```bash
$VENV_PY scripts/generate_selected_specs.py \
  --chunks output/rv_dm_chunks.json \
  --out-dir output/specs_rv_dm \
  --provider GUOCHUANG_DEEPSEEK \
  rv_dm__continuous_region__rv_dm__001
```

chunk ID 可以从 `output/<ip>_chunks.json` 或运行日志中查看。

### 4.3 只运行 semantic Channel B 和 ref

如果已有 `output/specs_<ip>/`，可以跳过分块和 spec 生成：

```bash
$VENV_PY scripts/run_phase2_e2e.py \
  --ip rv_dm \
  --out-root output \
  --specs-dir output/specs_rv_dm \
  --channels B \
  --ag-pairing-mode semantic \
  --semantic-batch-mode guarded \
  --semantic-hf-home /home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/out/hf_cache \
  --workers 8
```

`--channels B` 是当前推荐设置。当前实验不要添加 `C`、`D`、`L2`、`F`，也不要添加 `--phase3-top-n`、`--run-solver` 或 `--formal-check-top-n`。

### 4.4 单独抽取官方 ref

通常 ref 会由 `run_phase2_e2e.py` 自动处理。如果只需要重新抽取 ref：

```bash
$VENV_PY scripts/extract_ref.py rv_dm \
  --out-dir output/ref_out \
  --workers 4
```

抽取结果为：

```text
output/ref_out/rv_dm_ref_raw.json
output/ref_out/.ckpt/rv_dm/
```

`.ckpt` 是按源文件保存的增量 checkpoint。需要忽略旧 checkpoint、完整重跑时使用：

```bash
$VENV_PY scripts/extract_ref.py rv_dm \
  --out-dir output/ref_out \
  --workers 4 \
  --fresh
```

### 4.5 单独运行 ref 匹配

对已有 findings 和 raw ref 单独执行匹配：

```bash
$VENV_PY scripts/run_ref_match.py \
  --ip rv_dm \
  --refs output/ref_out/rv_dm_ref_raw.json \
  --findings output/findings_rv_dm.json \
  --out output/findings_rv_dm_ref_matched.json
```

ref 匹配包含两种方向：

- **Forward**：以 finding 为 query，召回语义相关的 ref。
- **Reverse**：以 specific ref 为 query，主动寻找最相关的 findings，用于发现 finding 文本没有直接描述但 RTL 可能违反官方约束的情况。

specific ref 和 general ref 分层保存到 finding 的 `ref_clues` 中。匹配过程只负责提供审计线索，不直接把 finding 判定为 bug。

## 5. 多模块运行

后台串行运行多个模块：

```bash
cd /home/smy/rtl_bug_agent
bash scripts/run_csbc_batch_bg.sh aes dma hmac keymgr kmac rv_dm soc_dbg_ctrl tlul uart
```

不传模块名时，脚本使用内部默认列表。运行日志写入 `output/`，包括批次日志和每个 IP 的日志；这些日志属于运行时产物，不是 CSBC v3 的核心分析结果。

查看进度：

```bash
ps -ef | rg 'run_csbc|_csbc_batch_runner'
tail -f output/batch_<timestamp>.log
```

## 6. Channel B 和 ref 的作用

### Semantic Channel B

Channel B 从 spec 中构建 assumption/guarantee 原子，并使用 BGE-M3 做语义检索。候选关系再由 LLM 判断是否存在：

- `CONTRADICTION`：两个行为描述可能冲突。
- `GAP`：一个行为约束没有被另一侧满足或覆盖。
- `DEFENSIVE`：关系是防御性或一致的，不构成 bug。

semantic pairing 的中间摘要保存到 `semantic_ag_shadow_<ip>.json`，Channel B 的增量结果保存到 `.checkpoint_<ip>_B_semantic.json`。

### Ref processing

ref 来自官方 `hjson`、testplan 和 theory-of-operation 文档。ref 抽取阶段把文档转换为带有 `ref_id`、`ref_kind`、关键词和硬件行为描述的原子条目；匹配阶段再将相关 ref 附加到 finding 的 `ref_clues`。

ref 是辅助证据，不等于 bug 结论。最终是否为 bug，需要后续源码级分析或人工审查；当前精简端到端流程不会自动执行 Phase 3。

## 7. 当前不使用的功能

以下功能不属于当前 CSBC v3 主流程：

- Channel C coverage gap
- Channel D temporal consistency
- Channel F SVA synthesis
- Layer 2 official-spec alignment
- formal solver 和 formal check
- structure-aware chunking / structure mode
- Phase 3 源码级验证
- Codex isolated agent 运行

不要在当前复现实验中添加这些选项。Phase 3 agent prompt 保存在 `config/prompts/phase3/verify_agent.md`，仅作为后续独立实验的输入模板。

## 8. 结果解释和恢复

当前流程主要查看：

```text
findings_<ip>.json                 # Channel B 融合后的 findings
ref_out/<ip>_ref_raw.json          # 官方 ref 原子
semantic_ag_shadow_<ip>.json       # semantic pairing 摘要
.checkpoint_<ip>_B_semantic.json   # Channel B 增量 checkpoint
```

如果需要保留当前结果并重新实验，建议使用新的输出根目录：

```bash
mkdir -p output/rerun_20260711
$VENV_PY scripts/run_phase2_e2e.py \
  --ip rv_dm \
  --out-root output/rerun_20260711 \
  --specs-dir output/specs_rv_dm \
  --channels B \
  --ag-pairing-mode semantic \
  --semantic-batch-mode guarded
```

历史文件没有从磁盘删除，而是归档在 `/home/smy/rtl_bug_agent_copy/`，可从那里恢复或对照。
