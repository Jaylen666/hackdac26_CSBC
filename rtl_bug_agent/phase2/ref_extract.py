"""LLM ref atom 抽取（逐文件抽取 → 增量汇总 → raw 落盘）。

这是 `experiments/ref_matching_upgrade/extract_ref.py` 的主包正式版：路径不再相对
实验目录硬编码，抽取逻辑抽成 `extract_refs(...)` 供 pipeline（run_phase2_e2e）调用，
CLI 入口保留以便单独重跑。

流程：
    1. 按 <ip> 确定输入文件（含特殊模块处理，见 SPECIAL_INPUTS / SKIP_MODULES）。
    2. **一个文件单独调用一次 LLM 抽取**（config/prompts/phase2/ref_extract.md），每次调用结束即自检。
    3. 各文件 ref_atoms **增量汇总**（ref_id 带文件名前缀，天然不冲突）。
    4. 落盘 <ip>_ref_raw.json（下游直接用 raw atoms 做双向匹配，见 ref_match.py）。

模块级去重（dedup_*）暂不启用：dedup 的 merge 会稀释嵌入向量、对 top-N 检索不利，
下游用 raw atoms 检索。相关函数保留但不在 extract_refs 主路径调用。
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
from typing import Any

from rtl_bug_agent.env import make_client, load_dotenv

# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
OPENTITAN_IP_ROOT = Path("/home/smy/opentitan/hw/ip")          # <ip> 目录的父目录
PROMPT_DIR = _REPO_ROOT / "config" / "prompts" / "phase2"
EXTRACT_PROMPT_PATH = PROMPT_DIR / "ref_extract.md"
DEDUP_PROMPT_PATH = PROMPT_DIR / "ref_dedup.md"
DEFAULT_OUT_DIR = _REPO_ROOT / "output" / "ref_out"
ENV_PATH = "/home/smy/.env"

# API：与 Channel B 相同
API_PREFIX = "GUOCHUANG_DEEPSEEK"
MAX_TOKENS = 30000
DEDUO_MAX_TOKENS = 100000
EXTRACT_RETRIES = 3        # 解析失败时的重试次数（LLM 非确定性：同一输入可能这次坏下次好）
DEDUP_RETRIES = 3          # dedup 解析失败时的重试次数
CKPT_DIRNAME = ".ckpt"     # checkpoint 目录名（位于 out-dir 下）

# ---------------------------------------------------------------------------
# 模块特殊处理
# ---------------------------------------------------------------------------
# 无可提炼硬件行为规范的模块（无官方 spec）——直接跳过。
SKIP_MODULES = {"flash_ctrl", "rv_core_ibex", "otp_ctrl", "otp_macro"}

# doc 部分的特殊处理。默认 doc = doc/theory_of_operation.md。
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

_VALID_KINDS = {"specific", "general", "unknown"}


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

    通用规则：data/*.hjson（跳过 *.prj.hjson）+ doc/theory_of_operation.md
    特殊模块见 SPECIAL_INPUTS。
    """
    spec = SPECIAL_INPUTS.get(ip)
    if spec and "alias" in spec:
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
    """健壮剥壳：处理 ```json 围栏、对象后的多余尾部内容、以及被截断的对象。"""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start != -1:
        try:
            obj, _end = json.JSONDecoder().raw_decode(text, start)
            return obj
        except json.JSONDecodeError:
            pass

    tail = text[-80:].replace("\n", "\\n")
    raise RuntimeError(
        f"LLM 响应无法解析为 JSON（长度 {len(content)}，结尾: …{tail!r}）。"
        f"若结尾像是被截断的字符串/缺右括号，多半是 MAX_TOKENS 太小——"
        f"当前 MAX_TOKENS={MAX_TOKENS}，请调大后重试。"
    )


# ---------------------------------------------------------------------------
# 抽取阶段自检
# ---------------------------------------------------------------------------
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


def extract_one_file(f: Path, prompt: str, client) -> dict:
    """一个文件一次 LLM 调用，返回该文件的抽取结果（已自检）。

    解析失败时重试 EXTRACT_RETRIES 次；仍失败则**跳过该文件**（返回空 atoms）。
    """
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        print(f"  [warn] 读取失败 {f.name}: {exc}")
        return {"file_involved": [f.name], "num_of_ref_atoms": 0, "ref_atoms": []}

    user_payload = f"===== FILE: {f.name} =====\n{text}"
    last_err = None
    for attempt in range(1, EXTRACT_RETRIES + 2):
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
def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _ip_ckpt_dir(ip: str, ckpt_root: Path) -> Path:
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


def extract_or_cached(f: Path, prompt: str, prompt_hash: str, client,
                      ckpt_dir: Path, fresh: bool) -> tuple[list[dict], bool]:
    """抽取一个文件：命中 checkpoint 直接返回缓存，否则调 LLM 并在成功后写缓存。"""
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
# 抽取主流程（库函数 + CLI 共用）
# ---------------------------------------------------------------------------
def _kind_dist(atoms: list[dict]) -> dict:
    return dict(Counter(a.get("ref_kind") for a in atoms))


def extract_refs(
    ip: str,
    out_dir: Path,
    *,
    ip_root: Path = OPENTITAN_IP_ROOT,
    ckpt_dir: Path | None = None,
    workers: int = 4,
    fresh: bool = False,
    client: Any = None,
) -> Path | None:
    """抽取 <ip> 的 ref atoms，写 <out_dir>/<ip>_ref_raw.json。

    返回 raw 文件路径；<ip> 属于 SKIP_MODULES 时返回 None。
    幂等：文件级 checkpoint 命中即跳过 LLM 调用（内容哈希失效自动重抽）。
    """
    if ip in SKIP_MODULES:
        print(f"[skip] {ip} 属于无官方 spec 的跳过列表（SKIP_MODULES），不处理。")
        return None

    out_dir = Path(out_dir)
    ckpt_root = ckpt_dir if ckpt_dir is not None else out_dir / CKPT_DIRNAME
    ip_ckpt_dir = _ip_ckpt_dir(ip, ckpt_root)
    if fresh and ip_ckpt_dir.exists():
        shutil.rmtree(ip_ckpt_dir)
        print(f"[fresh] 已清空 checkpoint: {ip_ckpt_dir}")

    files = resolve_input_files(ip, ip_root)
    alias = SPECIAL_INPUTS.get(ip, {}).get("alias")
    print(f"=== {ip} 输入文件（{len(files)}）"
          + (f"  [别名复用 {alias}]" if alias else "") + " ===")
    for f in files:
        print(f"  {f}")

    if not EXTRACT_PROMPT_PATH.exists():
        raise FileNotFoundError(f"抽取提示词不存在: {EXTRACT_PROMPT_PATH}")
    extract_prompt = EXTRACT_PROMPT_PATH.read_text(encoding="utf-8")
    extract_prompt_hash = _sha16(extract_prompt)

    if client is None:
        client = make_client(API_PREFIX)

    workers = max(1, min(workers, len(files)))
    print(f"\n=== 逐文件抽取（{API_PREFIX}，每文件一次调用，workers={workers}）===")

    per_file_atoms: list[list[dict]] = [[] for _ in files]

    def _run_one(i: int, f: Path) -> tuple[int, list[dict], bool]:
        atoms, cached = extract_or_cached(
            f, extract_prompt, extract_prompt_hash, client, ip_ckpt_dir, fresh
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
            for fut in as_completed(fut_to_idx):
                i = fut_to_idx[fut]
                try:
                    _, atoms, cached = fut.result()
                    per_file_atoms[i] = atoms
                    tag = "[cache] " if cached else ""
                except Exception as exc:
                    print(f"  [warn] {files[i].name} 抽取线程异常，跳过：{exc}")
                    per_file_atoms[i] = []
                    tag = ""
                done_count += 1
                print(f"  [{done_count}/{len(files)}] {tag}{files[i].name} "
                      f"-> {len(per_file_atoms[i])} atoms  分布={_kind_dist(per_file_atoms[i])}")

    all_atoms: list[dict] = []
    files_involved: list[str] = []
    for i, f in enumerate(files):
        all_atoms.extend(per_file_atoms[i])
        files_involved.append(f.name)

    print(f"\n  汇总：{len(all_atoms)} atoms（来自 {len(files_involved)} 个文件）"
          f"  分布={_kind_dist(all_atoms)}")

    out_dir.mkdir(parents=True, exist_ok=True)
    raw_doc = {
        "ip": ip,
        "file_involved": files_involved,
        "num_of_ref_atoms": len(all_atoms),
        "total_tokens": None,
        "ref_atoms": all_atoms,
    }
    raw_path = out_dir / f"{ip}_ref_raw.json"
    raw_path.write_text(json.dumps(raw_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  wrote raw → {raw_path}")
    return raw_path


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM ref atom 抽取（逐文件抽取，raw 落盘）")
    ap.add_argument("ip", help="IP 名，如 kmac / tlul / prim")
    ap.add_argument("--ip-root", type=Path, default=OPENTITAN_IP_ROOT,
                    help=f"IP 目录父路径（默认 {OPENTITAN_IP_ROOT}）")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                    help=f"输出目录（默认 {DEFAULT_OUT_DIR}）")
    ap.add_argument("--workers", type=int, default=4,
                    help="逐文件抽取的并行 worker 数（默认 4；设 1 退回串行）")
    ap.add_argument("--fresh", action="store_true",
                    help="忽略并清空该 IP 的 checkpoint，全量重跑")
    ap.add_argument("--ckpt-dir", type=Path, default=None,
                    help=f"checkpoint 根目录（默认 <out-dir>/{CKPT_DIRNAME}）")
    args = ap.parse_args()

    load_dotenv(ENV_PATH)
    extract_refs(
        args.ip, args.out_dir,
        ip_root=args.ip_root, ckpt_dir=args.ckpt_dir,
        workers=args.workers, fresh=args.fresh,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
