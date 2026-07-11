#!/usr/bin/env python3
"""Ref atom 抽取脚本（LLM 全托管：逐文件抽取 → 增量汇总 → 模块级去重）。

用法：
    /home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python scripts/extract_ref.py <ip>
    /home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python scripts/extract_ref.py kmac
    /home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/.venv/bin/python scripts/extract_ref.py tlul --out-dir ./output/ref_out

流程：
    1. 按 <ip> 确定输入文件（含特殊模块的特殊处理，见 SPECIAL_INPUTS / SKIP_MODULES）。
    2. **一个文件单独调用一次 LLM 抽取**（prompts/ref_extract.md），每次调用结束即做格式自检。
    3. 把每个文件的 ref_atoms **增量汇总**到一份列表（ref_id 带文件名前缀，天然不冲突）。
    4. 落盘一份：
         - <ip>_ref_raw.json        逐文件汇总（下游直接使用 raw atoms 做检索）

    注：模块级去重逻辑已注释掉（dedup 会合并多角度条目，稀释嵌入向量，
    对 top-N 检索不利；raw atoms 保持原子粒度，检索质量更好）。
    去重函数 dedup_atoms / dedup_with_retry / validate_dedup / build_dedup_review 保留在文件中
    以备将来恢复，但不参与正常执行。

API：与 Channel B 相同（make_client("GUOCHUANG_DEEPSEEK")）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from rtl_bug_agent.env import make_client, load_dotenv

# ---------------------------------------------------------------------------
# Default paths — 可直接修改
# ---------------------------------------------------------------------------
OPENTITAN_IP_ROOT = Path("/home/smy/opentitan/hw/ip")          # <ip> 目录的父目录
PROMPT_DIR = PROJECT_ROOT / "config" / "prompts" / "phase2"
EXTRACT_PROMPT_PATH = PROMPT_DIR / "ref_extract.md"
DEDUP_PROMPT_PATH = PROMPT_DIR / "ref_dedup.md"
DEFAULT_OUT_DIR = PROJECT_ROOT / "output" / "ref_out"  # 输出目录
ENV_PATH = "/home/smy/.env"

# API：与 Channel B 相同
API_PREFIX = "GUOCHUANG_DEEPSEEK"
MAX_TOKENS = 30000
DEDUO_MAX_TOKENS = 100000
DEDUP_RETRIES = 3          # dedup 解析失败时的重试次数（同 EXTRACT_RETRIES 思路）
CKPT_DIRNAME = ".ckpt"     # checkpoint 目录名（位于 out-dir 下）
# ---------------------------------------------------------------------------
# 模块特殊处理
# ---------------------------------------------------------------------------
# 无可提炼硬件行为规范的模块（无官方 spec）——直接跳过。
SKIP_MODULES = {"flash_ctrl", "rv_core_ibex", "otp_ctrl", "otp_macro"}

# doc 部分的特殊处理。默认 doc = doc/theory_of_operation.md。
# 取值形式：
#   {"doc_glob": "doc/*.md"}      -> doc 下所有 md 都作为输入（每个单独一次 LLM 调用）
#   {"doc_files": ["doc/X.md"]}   -> 指定 doc 文件列表
#   {"alias": "prim"}             -> 完全复用另一个模块的输入文件（prim 家族共享 prim/doc）
PRIM_FAMILY_ALIAS = "prim"
SPECIAL_INPUTS: dict[str, dict] = {
    "prim":                   {"doc_glob": "doc/*.md"},   # 多个 prim_*.md，各自一次调用
    "prim_generic":           {"alias": PRIM_FAMILY_ALIAS},
    "prim_xilinx":            {"alias": PRIM_FAMILY_ALIAS},
    "prim_xilinx_ultrascale": {"alias": PRIM_FAMILY_ALIAS},
    "tlul":                   {"doc_files": ["doc/TlulProtocolChecker.md"]},
}

# data/ 下这些是工程/构建文件（FuseSoC core 等），非硬件行为规范，跳过。
_DATA_SKIP_SUFFIXES = (".prj.hjson",)


# ---------------------------------------------------------------------------
# 输入文件确定
# ---------------------------------------------------------------------------
def _collect_data_hjson(ip_dir: Path) -> list[Path]:
    data_dir = ip_dir / "data"
    if not data_dir.is_dir():
        return []
    out = []
    for f in sorted(data_dir.glob("*.hjson")):
        if any(f.name.endswith(sfx) for sfx in _DATA_SKIP_SUFFIXES):
            continue
        out.append(f)
    return out


def resolve_input_files(ip: str, ip_root: Path) -> list[Path]:
    """确定 <ip> 的输入文件（含特殊模块处理）。

    通用规则：
        data/*.hjson（跳过 *.prj.hjson）+ doc/theory_of_operation.md
    特殊模块见 SPECIAL_INPUTS。
    """
    spec = SPECIAL_INPUTS.get(ip)
    if spec and "alias" in spec:
        # prim 家族：完全复用 prim 的输入
        return resolve_input_files(spec["alias"], ip_root)

    ip_dir = ip_root / ip
    if not ip_dir.is_dir():
        raise FileNotFoundError(f"IP 目录不存在: {ip_dir}")

    files: list[Path] = _collect_data_hjson(ip_dir)

    if spec and "doc_glob" in spec:
        files.extend(sorted(ip_dir.glob(spec["doc_glob"])))
    elif spec and "doc_files" in spec:
        for rel in spec["doc_files"]:
            p = ip_dir / rel
            if p.exists():
                files.append(p)
            else:
                print(f"  [warn] 指定的 doc 文件不存在，跳过: {p}")
    else:
        toa = ip_dir / "doc" / "theory_of_operation.md"
        if toa.exists():
            files.append(toa)

    if not files:
        raise FileNotFoundError(
            f"{ip}: 未找到任何可用输入文件（data/*.hjson 与 doc 均为空）"
        )
    return files


# ---------------------------------------------------------------------------
# LLM 响应解析（复用 Channel B 的健壮剥壳逻辑）
# ---------------------------------------------------------------------------
def parse_llm_json(content: str) -> dict:
    """健壮剥壳：处理 ```json 围栏、对象后的多余尾部内容、以及被截断的对象。

    LLM 有两种常见坏输出，需分别处理：
      - **多余尾部（Extra data）**：完整对象后跟了解释文字或第二个对象。
        用 raw_decode 从第一个 '{' 只解析出**一个**完整对象即可，忽略其后内容。
      - **截断（Unterminated）**：对象没写完（少右括号/字符串没闭合），几乎总是 MAX_TOKENS 不够。
    """
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    # 首选：直接整体解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 从第一个 '{' 起，用 raw_decode 只解析一个完整对象（天然吞掉多余尾部）
    start = text.find("{")
    if start != -1:
        try:
            obj, _end = json.JSONDecoder().raw_decode(text, start)
            return obj
        except json.JSONDecodeError:
            pass

    # 到这里说明第一个对象本身就解析不出——多半是被截断
    tail = text[-80:].replace("\n", "\\n")
    raise RuntimeError(
        f"LLM 响应无法解析为 JSON（长度 {len(content)}，结尾: …{tail!r}）。"
        f"若结尾像是被截断的字符串/缺右括号，多半是 MAX_TOKENS 太小——"
        f"当前 MAX_TOKENS={MAX_TOKENS}，请调大后重试。"
    )


# ---------------------------------------------------------------------------
# 抽取阶段自检
# ---------------------------------------------------------------------------
_VALID_KINDS = {"specific", "general", "unknown"}


def validate_extract(doc: dict, src_name: str) -> dict:
    """单文件抽取结果的结构自检 + 轻量修正。"""
    atoms = doc.get("ref_atoms")
    if not isinstance(atoms, list):
        raise ValueError(f"{src_name}: 响应缺少 ref_atoms 数组")

    problems: list[str] = []
    for i, a in enumerate(atoms):
        kind = a.get("ref_kind")
        if kind not in _VALID_KINDS:
            problems.append(f"atom[{i}] ref_kind 非法: {kind!r}")
        if kind == "specific":
            if not (a.get("kind_reason") or "").strip():
                problems.append(f"atom[{i}] specific 缺 kind_reason")
        else:
            a["kind_reason"] = None

    n = len(atoms)
    if doc.get("num_of_ref_atoms") != n:
        print(f"    [fix] {src_name} num_of_ref_atoms {doc.get('num_of_ref_atoms')} -> {n}")
        doc["num_of_ref_atoms"] = n

    if problems:
        print(f"    [warn] {src_name} 自检发现问题（已落盘，需人工确认）：")
        for p in problems:
            print(f"           - {p}")
    return doc


EXTRACT_RETRIES = 3  # 解析失败时的重试次数（LLM 非确定性：同一输入可能这次坏下次好）


def extract_one_file(f: Path, prompt: str, client) -> dict:
    """一个文件一次 LLM 调用，返回该文件的抽取结果（已自检）。

    解析失败时重试 EXTRACT_RETRIES 次；仍失败则**跳过该文件**（返回空 atoms），
    不让单个文件的坏输出中断整个模块的增量汇总。
    """
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        print(f"  [warn] 读取失败 {f.name}: {exc}")
        return {"file_involved": [f.name], "num_of_ref_atoms": 0, "ref_atoms": []}

    user_payload = f"===== FILE: {f.name} =====\n{text}"
    last_err = None
    for attempt in range(1, EXTRACT_RETRIES + 2):  # 首次 + 重试
        content = client.chat(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_payload},
            ],
            max_tokens=MAX_TOKENS,
        )
        try:
            doc = parse_llm_json(content)
            return validate_extract(doc, f.name)
        except (RuntimeError, ValueError) as exc:
            last_err = exc
            print(f"    [retry] {f.name} 第 {attempt} 次解析失败：{str(exc)[:90]}")

    print(f"  [warn] {f.name} 连续 {EXTRACT_RETRIES + 1} 次解析失败，跳过该文件。最后错误：{last_err}")
    return {"file_involved": [f.name], "num_of_ref_atoms": 0, "ref_atoms": []}


# ---------------------------------------------------------------------------
# Checkpoint（文件级）
# ---------------------------------------------------------------------------
# 以文件为最小单位缓存抽取结果，dedup 作为独立最小单位单独缓存。
# 失效判据 = 内容哈希不匹配（源文件内容 / 提示词 / dedup 输入 一变即失效）。
def _sha16(text: str) -> str:
    """内容指纹：sha256 前 16 位十六进制，足够区分、够短。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _ip_ckpt_dir(ip: str, ckpt_root: Path) -> Path:
    """<ckpt_root>/<ip>/ —— 该 IP 所有文件级 checkpoint 的存放目录。"""
    return ckpt_root / ip


def load_file_ckpt(f: Path, prompt_hash: str, ckpt_dir: Path) -> list[dict] | None:
    """命中返回缓存的 ref_atoms，否则 None（不存在 / 源文件变 / 提示词变）。"""
    p = ckpt_dir / f"{f.name}.json"
    if not p.exists():
        return None
    try:
        ck = json.loads(p.read_text(encoding="utf-8"))
        src_hash = _sha16(f.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    if ck.get("src_hash") != src_hash or ck.get("prompt_hash") != prompt_hash:
        return None
    atoms = ck.get("ref_atoms")
    return atoms if isinstance(atoms, list) else None


def save_file_ckpt(f: Path, atoms: list[dict], prompt_hash: str, ckpt_dir: Path) -> None:
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    src_hash = _sha16(f.read_text(encoding="utf-8", errors="replace"))
    (ckpt_dir / f"{f.name}.json").write_text(
        json.dumps(
            {"src_name": f.name, "src_hash": src_hash,
             "prompt_hash": prompt_hash, "ref_atoms": atoms},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


def _dedup_input_hash(atoms: list[dict]) -> str:
    """dedup 输入指纹：抽取结果（all_atoms）的稳定 JSON。抽取一变即失效。"""
    return _sha16(json.dumps(atoms, ensure_ascii=False, sort_keys=True))


def load_dedup_ckpt(atoms: list[dict], prompt_hash: str, ckpt_dir: Path) -> dict | None:
    """命中返回缓存的 deduped 对象，否则 None（输入变 / 提示词变）。"""
    p = ckpt_dir / "_dedup.json"
    if not p.exists():
        return None
    try:
        ck = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if (ck.get("input_hash") != _dedup_input_hash(atoms)
            or ck.get("prompt_hash") != prompt_hash):
        return None
    return ck.get("deduped")


def save_dedup_ckpt(atoms: list[dict], deduped: dict, prompt_hash: str, ckpt_dir: Path) -> None:
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    (ckpt_dir / "_dedup.json").write_text(
        json.dumps(
            {"input_hash": _dedup_input_hash(atoms),
             "prompt_hash": prompt_hash, "deduped": deduped},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


def extract_or_cached(f: Path, prompt: str, prompt_hash: str, client,
                      ckpt_dir: Path, fresh: bool) -> tuple[list[dict], bool]:
    """抽取一个文件：命中 checkpoint 直接返回缓存，否则调 LLM 并在成功后写缓存。

    返回 (ref_atoms, cached)；cached=True 表示来自 checkpoint（未调 LLM）。
    LLM 连续失败返回空 atoms 时**不写 checkpoint**——重跑会重试该文件。
    """
    if not fresh:
        cached = load_file_ckpt(f, prompt_hash, ckpt_dir)
        if cached is not None:
            return cached, True

    doc = extract_one_file(f, prompt, client)
    atoms = doc.get("ref_atoms", [])
    if atoms:  # 仅在真正抽到内容时落 checkpoint（失败/空不缓存 → 重跑会重试）
        save_file_ckpt(f, atoms, prompt_hash, ckpt_dir)
    return atoms, False


# ---------------------------------------------------------------------------
# 去重阶段
# ---------------------------------------------------------------------------
def dedup_atoms(atoms: list[dict], prompt: str, client) -> dict:
    """模块级去重：一次 LLM 调用处理该模块全部 atom。"""
    payload = json.dumps({"ref_atoms": atoms}, ensure_ascii=False, indent=2)
    content = client.chat(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": payload},
        ],
        max_tokens=DEDUO_MAX_TOKENS,
    )
    return parse_llm_json(content)


def dedup_with_retry(atoms: list[dict], prompt: str, client) -> dict:
    """带重试的模块级去重：解析/自检失败重试 DEDUP_RETRIES 次；全失败抛异常。

    仿 extract_one_file：LLM 非确定性下同一输入可能这次坏下次好。
    成功即返回**已自检（validate_dedup）**的对象；调用方据此写 checkpoint。
    全部失败抛 RuntimeError，由调用方回退到 raw。
    """
    last_err = None
    for attempt in range(1, DEDUP_RETRIES + 2):  # 首次 + 重试
        try:
            deduped = dedup_atoms(atoms, prompt, client)
            return validate_dedup(deduped, atoms)
        except (RuntimeError, ValueError) as exc:
            last_err = exc
            print(f"    [retry] dedup 第 {attempt} 次失败：{str(exc)[:90]}")
    raise RuntimeError(f"dedup 连续 {DEDUP_RETRIES + 1} 次失败，最后错误：{last_err}")


def validate_dedup(deduped: dict, original_atoms: list[dict]) -> dict:
    """去重结果自检：字段合法 + 可追溯（每个原始 atom 都能被追踪）。"""
    out_atoms = deduped.get("ref_atoms")
    if not isinstance(out_atoms, list):
        raise ValueError("去重响应缺少 ref_atoms 数组")

    problems: list[str] = []
    for i, a in enumerate(out_atoms):
        kind = a.get("ref_kind")
        if kind not in _VALID_KINDS:
            problems.append(f"atom[{i}] ref_kind 非法: {kind!r}")
        if kind == "specific":
            if not (a.get("kind_reason") or "").strip():
                problems.append(f"atom[{i}] specific 缺 kind_reason")
        else:
            a["kind_reason"] = None
        # merged_from 缺省补自身
        mf = a.get("merged_from")
        if not isinstance(mf, list) or not mf:
            a["merged_from"] = [a.get("ref_id")]

    # 数量自检
    n = len(out_atoms)
    if deduped.get("num_of_ref_atoms") != n:
        print(f"  [fix] dedup num_of_ref_atoms {deduped.get('num_of_ref_atoms')} -> {n}")
        deduped["num_of_ref_atoms"] = n

    # 可追溯性：每个原始 ref_id 都应出现在某条的 merged_from 里
    orig_ids = {a.get("ref_id") for a in original_atoms}
    traced: set = set()
    for a in out_atoms:
        traced.update(a.get("merged_from") or [])
    missing = orig_ids - traced
    if missing:
        problems.append(
            f"以下原始 atom 在去重结果中无法追溯（可能被 LLM 漏掉）：{sorted(missing)}"
        )

    # dedup_stats 一律以 merged_from 为准**重新计算**，不信任 LLM 自报的计数
    # （LLM 常因跨文件 ref_id 各自从 001 起而误算 input_count）。
    # 注意：仅凭 merged_from 无法区分「包含丢弃」还是「多角度合并」——两者都是
    # 从 len(mf) 条里保留 1 条、去掉 len(mf)-1 条。故这里只报可计算的总量，
    # 具体是包含还是合并请看 <ip>_ref_dedup_review.json 里的组内全文。
    dedup_groups = 0    # 发生去重（合并/包含）的组数：merged_from 长度 > 1
    removed = 0         # 被去掉的原始 atom 总数（组内除保留条外的其余）
    for a in out_atoms:
        mf = a.get("merged_from") or [a.get("ref_id")]
        if len(mf) > 1:
            dedup_groups += 1
            removed += len(mf) - 1
    recomputed = {
        "input_count": len(original_atoms),
        "output_count": n,
        "dedup_groups": dedup_groups,
        "removed": removed,
    }
    llm_stats = deduped.get("dedup_stats") or {}
    if llm_stats != recomputed:
        print(f"  [fix] dedup_stats 以 merged_from 重算: {llm_stats} -> {recomputed}")
    deduped["dedup_stats"] = recomputed

    # 数量一致性交叉校验：input == output + removed
    if len(original_atoms) != n + removed:
        problems.append(
            f"计数不自洽：input={len(original_atoms)} != output={n} + removed={removed}"
            f"（可能有原始 atom 被漏掉或重复计入 merged_from）"
        )

    if problems:
        print("  [warn] 去重自检发现问题（已落盘，需人工确认）：")
        for p in problems:
            print(f"         - {p}")
    return deduped


def build_dedup_review(deduped: dict, original_atoms: list[dict]) -> dict:
    """构造人工审核文件：列出每个发生合并/包含的组的全部原始条目全文。"""
    by_id = {a.get("ref_id"): a for a in original_atoms}
    groups: list[dict] = []
    for a in deduped.get("ref_atoms", []):
        mf = a.get("merged_from") or [a.get("ref_id")]
        if len(mf) > 1:  # 只有真正发生去重的组才需要人工看
            groups.append({
                "kept_ref_id": a.get("ref_id"),
                "kept": {
                    "ref_content": a.get("ref_content"),
                    "ref_kind": a.get("ref_kind"),
                    "kind_reason": a.get("kind_reason"),
                    "keywords": a.get("keywords"),
                },
                "originals": [
                    by_id.get(i, {"ref_id": i, "_MISSING_IN_RAW": True}) for i in mf
                ],
            })
    return {
        "dedup_stats": deduped.get("dedup_stats"),
        "num_groups": len(groups),
        "groups": groups,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def _kind_dist(atoms: list[dict]) -> dict:
    return dict(Counter(a.get("ref_kind") for a in atoms))


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM ref atom 抽取（逐文件抽取 + 模块级去重）")
    ap.add_argument("ip", help="IP 名，如 kmac / tlul / prim")
    ap.add_argument("--ip-root", type=Path, default=OPENTITAN_IP_ROOT,
                    help=f"IP 目录父路径（默认 {OPENTITAN_IP_ROOT}）")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                    help=f"输出目录（默认 {DEFAULT_OUT_DIR}）")
    ap.add_argument("--no-dedup", action="store_true",
                    help="跳过模块级去重（仅逐文件抽取汇总）")
    ap.add_argument("--workers", type=int, default=4,
                    help="逐文件抽取的并行 worker 数（默认 4；设 1 退回串行）")
    ap.add_argument("--fresh", action="store_true",
                    help="忽略并清空该 IP 的 checkpoint，全量重跑")
    ap.add_argument("--ckpt-dir", type=Path, default=None,
                    help=f"checkpoint 根目录（默认 <out-dir>/{CKPT_DIRNAME}）")
    args = ap.parse_args()

    ip = args.ip
    load_dotenv(ENV_PATH)

    if ip in SKIP_MODULES:
        print(f"[skip] {ip} 属于无官方 spec 的跳过列表（SKIP_MODULES），不处理。")
        return 0

    # 1. 输入文件
    files = resolve_input_files(ip, args.ip_root)
    alias = SPECIAL_INPUTS.get(ip, {}).get("alias")
    print(f"=== {ip} 输入文件（{len(files)}）"
          + (f"  [别名复用 {alias}]" if alias else "") + " ===")
    for f in files:
        print(f"  {f}")

    # 2. 提示词
    if not EXTRACT_PROMPT_PATH.exists():
        raise FileNotFoundError(f"抽取提示词不存在: {EXTRACT_PROMPT_PATH}")
    # if not DEDUP_PROMPT_PATH.exists():
    #     raise FileNotFoundError(f"去重提示词不存在: {DEDUP_PROMPT_PATH}")
    extract_prompt = EXTRACT_PROMPT_PATH.read_text(encoding="utf-8")
    # dedup_prompt = DEDUP_PROMPT_PATH.read_text(encoding="utf-8")
    extract_prompt_hash = _sha16(extract_prompt)
    # dedup_prompt_hash = _sha16(dedup_prompt)

    # 2.5 checkpoint 目录（以文件为最小单位缓存；--fresh 清空）
    ckpt_root = args.ckpt_dir if args.ckpt_dir is not None else args.out_dir / CKPT_DIRNAME
    ckpt_dir = _ip_ckpt_dir(ip, ckpt_root)
    if args.fresh and ckpt_dir.exists():
        shutil.rmtree(ckpt_dir)
        print(f"[fresh] 已清空 checkpoint: {ckpt_dir}")

    client = make_client(API_PREFIX)

    # 3. 逐文件抽取（可并行）+ 按输入顺序汇总（每次调用结束即自检）
    #    并行只加速"每文件一次 LLM 调用"这一步；结果按输入下标回填定长槽位，
    #    并行结束后再按 files 顺序展平 → 输出顺序与串行完全一致（确定性）。
    #    落盘/去重/自检逻辑不受并行影响（都在并行结束之后）。
    workers = max(1, min(args.workers, len(files)))
    print(f"\n=== 逐文件抽取（{API_PREFIX}，每文件一次调用，workers={workers}）===")

    per_file_atoms: list[list[dict]] = [[] for _ in files]  # 预分配定长槽位

    def _run_one(i: int, f: Path) -> tuple[int, list[dict], bool]:
        atoms, cached = extract_or_cached(
            f, extract_prompt, extract_prompt_hash, client, ckpt_dir, args.fresh
        )
        return i, atoms, cached

    if workers <= 1:
        for i, f in enumerate(files):
            _, atoms, cached = _run_one(i, f)
            per_file_atoms[i] = atoms
            tag = "[cache] " if cached else ""
            print(f"  [{i + 1}/{len(files)}] {tag}{f.name} "
                  f"-> {len(atoms)} atoms  分布={_kind_dist(atoms)}")
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as pool:
            fut_to_idx = {pool.submit(_run_one, i, f): i for i, f in enumerate(files)}
            done_count = 0
            for fut in as_completed(fut_to_idx):     # 乱序完成，只用于打印进度
                i = fut_to_idx[fut]
                try:
                    _, atoms, cached = fut.result()
                    per_file_atoms[i] = atoms
                    tag = "[cache] " if cached else ""
                except Exception as exc:             # 单文件异常不拖垮整体
                    print(f"  [warn] {files[i].name} 抽取线程异常，跳过：{exc}")
                    per_file_atoms[i] = []
                    tag = ""
                done_count += 1
                print(f"  [{done_count}/{len(files)}] {tag}{files[i].name} "
                      f"-> {len(per_file_atoms[i])} atoms  分布={_kind_dist(per_file_atoms[i])}")

    # 按输入顺序展平（此处顺序 100% 确定，与 workers 无关）
    all_atoms: list[dict] = []
    files_involved: list[str] = []
    for i, f in enumerate(files):
        all_atoms.extend(per_file_atoms[i])
        files_involved.append(f.name)

    print(f"\n  汇总：{len(all_atoms)} atoms（来自 {len(files_involved)} 个文件）"
          f"  分布={_kind_dist(all_atoms)}")

    # 落盘去重前的原始汇总（留档）
    args.out_dir.mkdir(parents=True, exist_ok=True)
    raw_doc = {
        "ip": ip,
        "file_involved": files_involved,
        "num_of_ref_atoms": len(all_atoms),
        "total_tokens": None,
        "ref_atoms": all_atoms,
    }
    raw_path = args.out_dir / f"{ip}_ref_raw.json"
    raw_path.write_text(json.dumps(raw_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  wrote raw → {raw_path}")

    # 4. 模块级去重（暂时注释掉：dedup merge 会稀释嵌入向量，对 top-N 检索不利；
    #    下游直接用 raw atoms 做检索，待检索架构确定后再决定是否恢复。）
    # -------------------------------------------------------------------------
    # if args.no_dedup or not all_atoms:
    #     final_atoms = all_atoms
    #     dedup_stats = None
    #     review = {"dedup_stats": None, "num_groups": 0, "groups": []}
    #     if args.no_dedup:
    #         print("\n=== 跳过去重（--no-dedup）===")
    # else:
    #     deduped = None
    #     if not args.fresh:
    #         deduped = load_dedup_ckpt(all_atoms, dedup_prompt_hash, ckpt_dir)
    #         if deduped is not None:
    #             print(f"\n=== 模块级去重 [cache] 命中，跳过 ===")
    #
    #     if deduped is None:
    #         print(f"\n=== 模块级去重（{API_PREFIX}，一次调用）===")
    #         try:
    #             deduped = dedup_with_retry(all_atoms, dedup_prompt, client)
    #             save_dedup_ckpt(all_atoms, deduped, dedup_prompt_hash, ckpt_dir)
    #         except RuntimeError as exc:
    #             print(f"  [warn] {exc}")
    #             print(f"  [fallback] 去重失败，回退用去重前 raw atoms 作为最终结果")
    #             deduped = None
    #
    #     if deduped is not None:
    #         final_atoms = deduped.get("ref_atoms", [])
    #         dedup_stats = deduped.get("dedup_stats")
    #         review = build_dedup_review(deduped, all_atoms)
    #         print(f"  去重：{len(all_atoms)} -> {len(final_atoms)}  stats={dedup_stats}")
    #         print(f"  去重涉及 {review['num_groups']} 组（详见 review 文件）")
    #     else:
    #         final_atoms = all_atoms
    #         dedup_stats = {
    #             "status": "dedup_failed_fallback_to_raw",
    #             "input_count": len(all_atoms),
    #             "output_count": len(all_atoms),
    #         }
    #         review = {"dedup_stats": dedup_stats, "num_groups": 0, "groups": []}
    #
    # # 5. 落盘最终结果 + 人工审核文件
    # final_doc = {
    #     "ip": ip,
    #     "file_involved": files_involved,
    #     "num_of_ref_atoms": len(final_atoms),
    #     "total_tokens": None,
    #     "dedup_stats": dedup_stats,
    #     "ref_atoms": final_atoms,
    # }
    # out_path = args.out_dir / f"{ip}_ref.json"
    # out_path.write_text(json.dumps(final_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    # review_path = args.out_dir / f"{ip}_ref_dedup_review.json"
    # review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    # -------------------------------------------------------------------------

    print(f"\n=== 完成 ===")
    print(f"  atoms: {len(all_atoms)}  分布={_kind_dist(all_atoms)}")
    print(f"  raw → {raw_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
