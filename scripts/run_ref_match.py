#!/usr/bin/env python3
"""批量运行 ref_match：给五个模块的 findings 附加 ref_clues，写入 *_ref_matched.json。

用法（批量，跑预设五个模块）：
    .venv/bin/python run_ref_match.py

用法（单模块，自定义路径）：
    .venv/bin/python run_ref_match.py --ip <name> --refs <ref_raw.json> --findings <findings.json>
    .venv/bin/python run_ref_match.py --ip hmac \\
        --refs ref_out/hmac_ref_raw.json \\
        --findings /path/to/findings_hmac.json

    输出默认写到 findings 文件同目录下的 findings_<ip>_ref_matched.json，
    可用 --out 指定输出路径覆盖。

输出格式：{"findings": [...]}，findings 原样保留，只有 ref_clues 字段被覆盖/新增。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ref_match import RefMatchConfig, match, load_atoms, load_findings

REF_OUT = PROJECT_ROOT / "output" / "ref_out"
FINDINGS_OUT = PROJECT_ROOT / "output"

MODULES = {
    ip: (REF_OUT / f"{ip}_ref_raw.json", FINDINGS_OUT / f"findings_{ip}.json")
    for ip in ("aes", "dma", "hmac", "keymgr", "kmac", "rv_dm", "soc_dbg_ctrl", "tlul", "uart")
}

cfg = RefMatchConfig()   # max_specific=4, max_general=2, floor=0.40


def run_one(ip: str, atoms_path: Path, findings_path: Path,
            out_path: Path | None = None) -> None:
    print(f"=== {ip} ===")
    if not atoms_path.exists():
        print(f"  [skip] ref 文件不存在: {atoms_path}")
        return
    if not findings_path.exists():
        print(f"  [skip] findings 文件不存在: {findings_path}")
        return

    atoms = load_atoms(str(atoms_path))
    findings = load_findings(str(findings_path))
    print(f"  refs={len(atoms)}  findings={len(findings)}")

    match(findings, atoms, config=cfg, verbose=True)

    n_spec = sum(any(c["layer"] == "specific" for c in f.get("ref_clues", [])) for f in findings)
    n_gen  = sum(any(c["layer"] == "general"  for c in f.get("ref_clues", [])) for f in findings)
    n_none = sum(not f.get("ref_clues") for f in findings)
    all_scores = [c["score"] for f in findings for c in f.get("ref_clues", [])]
    avg = sum(all_scores) / len(all_scores) if all_scores else 0.0
    print(f"  有专精层={n_spec} 有泛化层={n_gen} 空={n_none}  avg_score={avg:.3f}")

    dest = out_path or findings_path.parent / f"findings_{ip}_ref_matched.json"
    dest.write_text(
        json.dumps({"findings": findings}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  wrote → {dest}\n")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="ref_match 批量/单模块运行")
    ap.add_argument("--ip",       help="模块名（单模块模式必填）")
    ap.add_argument("--refs",     help="ref_raw.json 路径（单模块模式必填）")
    ap.add_argument("--findings", help="findings_*.json 路径（单模块模式必填）")
    ap.add_argument("--out",      help="输出路径（单模块模式可选，默认 findings 同目录）")
    args = ap.parse_args()

    # 单模块模式：三个参数必须同时给
    if args.ip or args.refs or args.findings:
        missing = [n for n, v in [("--ip", args.ip), ("--refs", args.refs),
                                   ("--findings", args.findings)] if not v]
        if missing:
            ap.error(f"单模块模式需同时指定 --ip / --refs / --findings，缺少: {' '.join(missing)}")
        run_one(args.ip,
                Path(args.refs), Path(args.findings),
                Path(args.out) if args.out else None)
        return 0

    # 批量模式
    print(f"RefMatchConfig: {cfg}\n")
    for ip, (atoms_path, findings_path) in MODULES.items():
        run_one(ip, atoms_path, findings_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
