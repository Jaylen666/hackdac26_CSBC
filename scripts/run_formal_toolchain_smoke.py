#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rtl_bug_agent.phase2.formal_toolchain import resolve_formal_toolchain

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SMOKE_DIR = _PROJECT_ROOT / "formal_smoke"


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone formal toolchain smoke runner")
    parser.add_argument("--workdir", default=None, help="Optional parent directory for temp workdir")
    parser.add_argument("--keep-workdir", action="store_true", help="Keep the generated workdir")
    parser.add_argument("--json-out", default=None, help="Optional JSON summary output path")
    parser.add_argument("--yosys", default=None, help="Override yosys executable")
    parser.add_argument("--yosys-smtbmc", default=None, help="Override yosys-smtbmc executable")
    parser.add_argument("--sby", default=None, help="Override sby executable")
    parser.add_argument("--solver", default=None, help="Override SMT solver executable")
    args = parser.parse_args()

    toolchain = resolve_formal_toolchain(
        yosys=args.yosys,
        yosys_smtbmc=args.yosys_smtbmc,
        sby=args.sby,
        solver=args.solver,
        require_smtcheck=True,
        require_solver=True,
    )
    print(json.dumps({"toolchain": toolchain.to_dict()}, indent=2))

    workdir = Path(
        tempfile.mkdtemp(
            prefix="formal_toolchain_smoke_",
            dir=args.workdir,
        )
    )
    summary_path = workdir / "summary.json"
    records: list[dict[str, object]] = []

    try:
        for name in (
            "sat_pass.v",
            "sat_fail.v",
            "toolchain_pass.v",
            "toolchain_fail.v",
            "toolchain_pass.sby",
            "toolchain_fail.sby",
        ):
            shutil.copy2(_SMOKE_DIR / name, workdir / name)

        env = toolchain.solver_env()

        records.append(_run_expect_ok(
            [
                toolchain.yosys,
                "-p",
                "read_verilog -sv sat_pass.v; prep -top sat_pass; sat -set a 1 -prove y 1 -verify",
            ],
            cwd=workdir,
            env=env,
            label="yosys-sat-pass",
        ))
        records.append(_run_expect_fail(
            [
                toolchain.yosys,
                "-p",
                "read_verilog -sv sat_fail.v; prep -top sat_fail; sat -set a 1 -prove y 0 -verify",
            ],
            cwd=workdir,
            env=env,
            label="yosys-sat-fail",
        ))
        records.append(_run_expect_ok(
            [
                toolchain.yosys,
                "-p",
                "read_verilog -sv -formal toolchain_pass.v; prep -top toolchain_pass; write_smt2 -wires toolchain_pass.smt2",
            ],
            cwd=workdir,
            env=env,
            label="yosys-write-smt2",
        ))
        records.append(_run_expect_ok(
            [
                toolchain.yosys,
                "-p",
                "read_verilog -sv -formal toolchain_fail.v; prep -top toolchain_fail; write_smt2 -wires toolchain_fail.smt2",
            ],
            cwd=workdir,
            env=env,
            label="yosys-write-smt2-fail",
        ))
        records.append(_run_expect_ok(
            [
                toolchain.yosys_smtbmc,
                "-t",
                "1",
                "-s",
                toolchain.solver_name,
                "-m",
                "toolchain_pass",
                "toolchain_pass.smt2",
            ],
            cwd=workdir,
            env=env,
            label="yosys-smtbmc-pass",
        ))
        records.append(_run_expect_ok(
            [
                toolchain.sby,
                "-f",
                "--yosys",
                toolchain.yosys,
                "--smtbmc",
                toolchain.yosys_smtbmc,
                "-d",
                "toolchain_pass_run",
                "toolchain_pass.sby",
            ],
            cwd=workdir,
            env=env,
            label="sby-pass",
        ))
        records.append(_run_expect_fail(
            [
                toolchain.sby,
                "-f",
                "--yosys",
                toolchain.yosys,
                "--smtbmc",
                toolchain.yosys_smtbmc,
                "-d",
                "toolchain_fail_run",
                "toolchain_fail.sby",
            ],
            cwd=workdir,
            env=env,
            label="sby-fail",
        ))

        summary = {
            "toolchain": toolchain.to_dict(),
            "results": records,
            "workdir": str(workdir),
        }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.json_out:
            out_path = Path(args.json_out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Smoke summary written to {summary_path}")
    finally:
        if args.keep_workdir:
            print(f"Kept workdir: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def _run_expect_ok(cmd: list[str], *, cwd: Path, env: dict[str, str], label: str) -> dict[str, object]:
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    ok = proc.returncode == 0
    _report(label, cmd, proc)
    if not ok:
        raise SystemExit(f"{label} failed unexpectedly")
    return {
        "label": label,
        "cmd": cmd,
        "returncode": proc.returncode,
        "expect": "ok",
    }


def _run_expect_fail(cmd: list[str], *, cwd: Path, env: dict[str, str], label: str) -> dict[str, object]:
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    ok = proc.returncode != 0
    _report(label, cmd, proc)
    if not ok:
        raise SystemExit(f"{label} unexpectedly passed")
    return {
        "label": label,
        "cmd": cmd,
        "returncode": proc.returncode,
        "expect": "fail",
    }


def _report(label: str, cmd: list[str], proc: subprocess.CompletedProcess[str]) -> None:
    print(f"[{label}] rc={proc.returncode}")
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)


if __name__ == "__main__":
    main()
