

"""
单模块用法：
    /home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python run_ref_match.py --ip kmac --refs ref_out/kmac_ref_raw.json --findings scoring/kmac_output/findings_kmac_refaug.json

Ref ↔ finding 双向匹配（forward + reverse + 分层合并）。

设计见 README §2 / §4。核心思想：把 ref 按 `ref_kind` 分泛化/专精，用不同方向检索，再分层合并。

统一打分（**方向无关**，forward 与 reverse 共用同一份 cosine 矩阵与同一个公式）：

    score(finding, ref) = W_EMBED * cosine(embed_finding, embed_ref) + W_KW * kw_hit

    kw_hit = Σ len(命中的 finding 关键词) / Σ len(全部 finding 关键词)
             （finding 的关键词 = involved_signals；命中 = 与某个 ref keyword 归一后相等或子串包含）

方向：
- `general` ref → **forward**：finding 作 query，每 finding 召回 top-k 泛化 ref（泛化层，合并时排后）。
- `specific` ref → **reverse ∪ forward**（专精层，合并时排前）：
  - reverse：ref 作 query，每个专精 ref 反向召回 top-n∈[1,3] 个 finding（README §2.3 抓删除型 bug——
    ref 主动认领没点名它的 finding）。
  - forward（安全网）：finding 作 query，在**仅专精 ref**的候选池里召回 top-k。补上"语义贴某专精 ref、
    但不是该 ref 的 reverse top-n"的漏网 finding（如 soc_dbg F-0002）。因为专精池与泛化池已分开，
    这里不会重演 §1.2 的"专精被泛化压沉"——两层各有各的额度，互不竞争。
  - 两方向的命中按 ref_id 取并集（同 ref 取较高分），再按 score 排序、截 max_specific。
- `unknown` ref → **跳过**（README §3.3：进检索前人工已把 unknown 裁定为 specific/general；
  仍出现的 unknown 不静默吞掉，打印计数后跳过）。

合并（§4 护栏）：一个 finding 的 `ref_clues` = 专精层（在前，≤MAX_SPECIFIC，设 min_score 地板）
                                            + 泛化层（在后，≤MAX_GENERAL，设 min_score 地板）。

本模块**不改** fusion/scoring，纯函数式：吃 findings + ref atoms，吐带 `ref_clues` 的 findings。
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any

import numpy as np

from rtl_bug_agent.phase2.semantic_ag import (
    SemanticAgConfig,
    _encode,
    _l2_normalize,
    _normalize_signal,
)


# ---------------------------------------------------------------------------
# 打分配置（默认值，可被 eval 覆盖以做阈值扫描）
# ---------------------------------------------------------------------------
@dataclass
class RefMatchConfig:
    # 统一打分权重（用户指定：embedding 0.7 / keyword 0.3）
    w_embed: float = 0.7
    w_kw: float = 0.3

    # 专精层 reverse（护栏 1 —— n 取小 + min_score 地板）
    reverse_n: int = 3               # 每个专精 ref 反向召回的 finding 数上限（∈[1,3]）
    reverse_min_score: float = 0.40  # 专精层地板：找不到家宁可不贴
    # 专精层 forward 安全网：每 finding 在仅专精 ref 池里召回 top-k
    specific_forward_k: int = 2

    # 泛化层 forward
    forward_top_k: int = 3           # 每个 finding 召回的泛化 ref 数上限
    forward_min_score: float = 0.40  # 泛化层地板：宁缺毋滥

    # 合并（护栏 2）：每 finding 专精/泛化双额度
    max_specific: int = 4
    max_general: int = 2

    # keyword 子串命中的最短长度（避免 2 字符高频碎片乱命中）
    kw_min_substr: int = 3


# ---------------------------------------------------------------------------
# 文本 / 关键词
# ---------------------------------------------------------------------------
def _finding_query_text(f: dict[str, Any]) -> str:
    """finding 的 embedding query 文本：标题 + 矛盾描述 + 信号名。"""
    parts = [
        str(f.get("title", "") or ""),
        str(f.get("contradiction", "") or ""),
        " ".join(str(s) for s in (f.get("involved_signals", []) or [])),
    ]
    return "\n".join(p for p in parts if p)


def _norm_kw(k: str) -> str:
    """关键词归一：小写 + _normalize_signal（去 [..]、去端口/后缀）。"""
    return _normalize_signal(str(k).strip().lower())


def _finding_kw_norms(f: dict[str, Any]) -> list[str]:
    """finding 的关键词（= involved_signals）归一去空去重，保序。"""
    out: list[str] = []
    seen: set[str] = set()
    for s in f.get("involved_signals", []) or []:
        n = _norm_kw(s)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _ref_kw_norms(a: dict[str, Any]) -> set[str]:
    return {n for n in (_norm_kw(k) for k in (a.get("keywords", []) or [])) if n}


def _kw_hit(finding_kw: list[str], ref_kw: set[str], min_substr: int) -> float:
    """kw_hit = Σ len(命中的 finding 关键词) / Σ len(全部 finding 关键词)。

    每个 finding 关键词二值判命中（命中则整词字符数计入分子），因此更长、更专的
    信号名（如 `key_share1`）比短碎片（`msg`）贡献更大——天然偏袒具体信号。
    命中判据：归一后与某个 ref 关键词相等，或彼此为 ≥min_substr 字符的子串。
    """
    total = sum(len(s) for s in finding_kw)
    if total == 0:
        return 0.0
    hit = 0
    for fk in finding_kw:
        matched = fk in ref_kw
        if not matched and len(fk) >= min_substr:
            for rk in ref_kw:
                if len(rk) >= min_substr and (fk in rk or rk in fk):
                    matched = True
                    break
        if matched:
            hit += len(fk)
    return hit / total


# ---------------------------------------------------------------------------
# ref 装载 / 分类拆分
# ---------------------------------------------------------------------------
def split_atoms(atoms: list[dict[str, Any]]) -> tuple[list[dict], list[dict], int]:
    """按 ref_kind 拆成 (specific, general, n_unknown_skipped)。

    unknown 进检索前应已被人工裁定；仍残留的 unknown 跳过并计数（不静默吞）。
    """
    specific, general, unknown = [], [], 0
    for a in atoms:
        kind = str(a.get("ref_kind", "")).lower()
        if kind == "specific":
            specific.append(a)
        elif kind == "general":
            general.append(a)
        else:
            unknown += 1
    return specific, general, unknown


def _clue(a: dict[str, Any], score: float, cos: float, kw: float, layer: str) -> dict[str, Any]:
    return {
        "ref_id": a.get("ref_id"),
        "ref_content": a.get("ref_content"),
        "ref_kind": a.get("ref_kind"),
        "keywords": a.get("keywords", []),
        "layer": layer,                    # "specific" | "general"
        "score": round(float(score), 3),
        "cosine": round(float(cos), 3),
        "kw_hit": round(float(kw), 3),
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def match(
    findings: list[dict[str, Any]],
    atoms: list[dict[str, Any]],
    config: RefMatchConfig | None = None,
    sem_config: SemanticAgConfig | None = None,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """给每个 finding 绑定 `ref_clues`（专精层在前 + 泛化层在后）。就地修改并返回 findings。"""
    cfg = config or RefMatchConfig()
    scfg = sem_config or SemanticAgConfig()

    if not findings:
        return findings
    for f in findings:
        f.setdefault("ref_clues", [])

    specific, general, n_unknown = split_atoms(atoms)
    if verbose:
        print(f"  atoms: {len(atoms)}  specific={len(specific)}  general={len(general)}  "
              f"unknown(skipped)={n_unknown}")
    if not specific and not general:
        return findings

    # ── embedding（refs + findings 各编码一次，L2 归一后点积即 cosine）──────────
    all_refs = specific + general
    ref_vecs = _l2_normalize(_encode(
        scfg.model_name, [a.get("ref_content", "") for a in all_refs],
        scfg.batch_size, scfg.use_fp16, scfg.hf_home, scfg.offline,
    ))
    q_vecs = _l2_normalize(_encode(
        scfg.model_name, [_finding_query_text(f) for f in findings],
        scfg.batch_size, scfg.use_fp16, scfg.hf_home, scfg.offline,
    ))
    cos = q_vecs @ ref_vecs.T                       # [n_findings, n_refs]

    # ── kw_hit 矩阵（同形状），score = w_embed*cos + w_kw*kw ────────────────────
    f_kw = [_finding_kw_norms(f) for f in findings]
    r_kw = [_ref_kw_norms(a) for a in all_refs]
    kw = np.zeros_like(cos)
    for i, fk in enumerate(f_kw):
        for j, rk in enumerate(r_kw):
            kw[i, j] = _kw_hit(fk, rk, cfg.kw_min_substr)
    score = cfg.w_embed * cos + cfg.w_kw * kw

    n_spec = len(specific)                          # 前 n_spec 列是专精 ref

    # ── 专精层：reverse ∪ forward（都只在专精 ref 池里，不与泛化竞争）──────────
    # spec_hits[i] = {ref_列号 j: (score, cos, kw)}，两方向命中并集，同 ref 取较高分。
    spec_hits: list[dict[int, tuple]] = [{} for _ in findings]

    def _add_spec(i: int, j: int) -> None:
        s = float(score[i, j])
        if s < cfg.reverse_min_score:                # 找不到家宁可不贴
            return
        prev = spec_hits[i].get(j)
        if prev is None or s > prev[0]:
            spec_hits[i][j] = (s, float(cos[i, j]), float(kw[i, j]))

    # reverse：每个专精 ref 反向召回 top-n finding（护栏 1，§2.3 抓删除型 bug）
    reverse_n = max(1, min(cfg.reverse_n, 3))       # 强制 n∈[1,3]
    for j in range(n_spec):
        for i in np.argsort(-score[:, j])[:reverse_n]:
            _add_spec(int(i), j)

    # forward 安全网：每个 finding 在仅专精 ref 池里召回 top-k（补 reverse 漏网）
    if n_spec:
        for i in range(len(findings)):
            for j in np.argsort(-score[i, :n_spec])[:cfg.specific_forward_k]:
                _add_spec(i, int(j))

    # ── 泛化层：forward（每个 finding 召回 top-k 泛化 ref）───────────────────────
    gen_hits: list[list[tuple]] = [[] for _ in findings]
    for i in range(len(findings)):
        cand = []
        for jg in range(len(general)):
            j = n_spec + jg                          # 泛化 ref 在矩阵里的列
            s = float(score[i, j])
            if s >= cfg.forward_min_score:
                cand.append((s, float(cos[i, j]), float(kw[i, j]), general[jg]))
        cand.sort(key=lambda x: x[0], reverse=True)
        gen_hits[i] = cand[:cfg.forward_top_k]

    # ── 合并：专精层(前, 排序后截 max_specific) + 泛化层(后, 截 max_general)──────
    for i, f in enumerate(findings):
        spec_layer = sorted(
            ((s, c, k, specific[j]) for j, (s, c, k) in spec_hits[i].items()),
            key=lambda x: x[0], reverse=True,
        )[:cfg.max_specific]
        gen_layer = gen_hits[i][:cfg.max_general]
        clues = [_clue(a, s, c, k, "specific") for s, c, k, a in spec_layer]
        clues += [_clue(a, s, c, k, "general") for s, c, k, a in gen_layer]
        f["ref_clues"] = clues

    return findings


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------
def load_atoms(path: str) -> list[dict[str, Any]]:
    """从 <ip>_ref_raw.json / <ip>_ref.json 读 ref_atoms。"""
    import json
    d = json.load(open(path, encoding="utf-8"))
    return d.get("ref_atoms", []) if isinstance(d, dict) else list(d)


def load_findings(path: str) -> list[dict[str, Any]]:
    """从 findings_*.json 读 findings（兼容 {"findings":[...]} 与裸列表）。"""
    import json
    d = json.load(open(path, encoding="utf-8"))
    if isinstance(d, dict):
        return d.get("findings", []) or []
    return list(d)
