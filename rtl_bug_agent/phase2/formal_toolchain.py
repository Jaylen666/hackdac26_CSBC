from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

_LOCAL_YOSYS = "/home/smy/hackdac26_work/tools/yosys-local/bin/yosys"
_LOCAL_YOSYS_SMTBMC = "/home/smy/hackdac26_work/tools/yosys-local/bin/yosys-smtbmc"
_LOCAL_SBY = "/usr/local/bin/sby"
_LOCAL_SOLVER = "/home/smy/hackdac26_work/tools/z3/usr/bin/z3"


@dataclass(frozen=True)
class FormalToolchain:
    yosys: str
    yosys_smtbmc: str
    sby: str
    solver: str = ""
    solver_name: str = ""
    solver_dir: str = ""
    yosys_supports_smtcheck: bool = False

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "yosys": self.yosys,
            "yosys_smtbmc": self.yosys_smtbmc,
            "sby": self.sby,
            "solver": self.solver,
            "solver_name": self.solver_name,
            "solver_dir": self.solver_dir,
            "yosys_supports_smtcheck": self.yosys_supports_smtcheck,
        }

    def solver_env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.solver_dir:
            env["PATH"] = f"{self.solver_dir}:{env.get('PATH', '')}"
        return env


def resolve_formal_toolchain(
    *,
    yosys: str | None = None,
    yosys_smtbmc: str | None = None,
    sby: str | None = None,
    solver: str | None = None,
    require_smtcheck: bool = True,
    require_solver: bool = True,
) -> FormalToolchain:
    yosys_path = _resolve_executable(
        "yosys",
        [yosys, os.environ.get("RTL_BUG_AGENT_YOSYS"), _LOCAL_YOSYS, "yosys", "/usr/bin/yosys"],
        probe=_probe_yosys_smtcheck if require_smtcheck else None,
        required=True,
    )
    smtbmc_path = _resolve_executable(
        "yosys-smtbmc",
        [
            yosys_smtbmc,
            os.environ.get("RTL_BUG_AGENT_YOSYS_SMTBMC"),
            _LOCAL_YOSYS_SMTBMC,
            "yosys-smtbmc",
            "/usr/bin/yosys-smtbmc",
        ],
        required=True,
    )
    sby_path = _resolve_executable(
        "sby",
        [sby, os.environ.get("RTL_BUG_AGENT_SBY"), _LOCAL_SBY, "sby"],
        required=True,
    )
    solver_path = _resolve_executable(
        "solver",
        [
            solver,
            os.environ.get("RTL_BUG_AGENT_SOLVER"),
            _LOCAL_SOLVER,
            "z3",
            "cvc5",
            "boolector",
            "bitwuzla",
            "yices",
            "mathsat",
            "cvc4",
        ],
        required=require_solver,
    )
    solver_name = Path(solver_path).name if solver_path else ""
    solver_dir = str(Path(solver_path).parent) if solver_path else ""
    return FormalToolchain(
        yosys=yosys_path,
        yosys_smtbmc=smtbmc_path,
        sby=sby_path,
        solver=solver_path,
        solver_name=solver_name,
        solver_dir=solver_dir,
        yosys_supports_smtcheck=_probe_yosys_smtcheck(yosys_path),
    )


def _resolve_executable(
    kind: str,
    candidates: Iterable[str | None],
    *,
    probe: Callable[[str], bool] | None = None,
    required: bool = True,
) -> str:
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        candidate = str(candidate).strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        resolved = _normalize_candidate(candidate)
        if not resolved:
            continue
        if probe and not probe(resolved):
            continue
        return resolved
    if required:
        raise RuntimeError(f"Unable to resolve {kind} executable")
    return ""


def _normalize_candidate(candidate: str) -> str:
    path = Path(candidate)
    if path.is_absolute() or "/" in candidate:
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
        return ""
    resolved = shutil.which(candidate)
    return resolved or ""


def _probe_yosys_smtcheck(yosys_path: str) -> bool:
    try:
        proc = subprocess.run(
            [yosys_path, "-p", "help hierarchy"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception:
        return False
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return proc.returncode == 0 and "-smtcheck" in text
