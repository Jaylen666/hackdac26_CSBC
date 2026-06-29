"""
Formal Runner: Execute Solver-Ready SVAs (Formal CSBC v2.0 §9)
================================================================

Collects all findings with ``formal.status == "PENDING"`` (from Channel B or F),
generates an independent SymbiYosys (.sby) project per SVA, runs the solver
(sby + z3 BMC), and backfills ``formal.result``.

This runs **before Phase 3**, so the Phase-3 prompts can reference both the
LLM's reasoning (verdict/contradiction) and the tool's evidence (formal_result).

Design:
- One temporary sby project per SVA (isolated work dirs, no cross-talk).
- Timeout + error tolerance: one failure does not block others.
- Signal widths extracted from RTL (yosys read_verilog → json → port widths),
  fed to ``render_sva_bind`` to close v2.5 fix 2.

Result schema (backfilled to ``finding["formal"]["result"]``):
    {
      "status": "PASS" | "FAIL" | "UNKNOWN" | "TIMEOUT" | "ERROR",
      "engine": "sby_z3",
      "duration_s": float,
      "trace_file": str | None,     # relative path to .vcd counterexample (FAIL)
      "error_log": str | None,      # error excerpt (ERROR)
    }
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from rtl_bug_agent.phase2.sva_bind import render_sva_bind


def run_formal_solver(
    findings: list[dict[str, Any]],
    rtl_files: list[str | Path],
    work_dir: str | Path,
    timeout_per_sva: int = 300,
    depth: int = 20,
) -> list[dict[str, Any]]:
    """Run the formal solver on all PENDING SVAs in *findings*.

    Args:
        findings: list of finding dicts; only those with
                  ``formal["status"] == "PENDING"`` are executed.
        rtl_files: RTL source files (.sv/.v) for the design under test.
        work_dir: directory for sby projects (one subdir per SVA).
        timeout_per_sva: max seconds per SVA execution (default 300).
        depth: BMC depth (default 20).

    Returns:
        The same findings list with ``formal["result"]`` backfilled for PENDING ones.
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # Extract signal widths from RTL (for render_sva_bind).
    signal_widths = _extract_signal_widths(rtl_files)

    pending = [
        (i, f) for i, f in enumerate(findings)
        if (f.get("formal", {}) or {}).get("status") == "PENDING"
    ]
    if not pending:
        print("Formal runner: no PENDING SVAs.")
        return findings

    print(f"Formal runner: {len(pending)} PENDING SVAs, depth={depth}, timeout={timeout_per_sva}s")

    for idx, finding in pending:
        formal = finding.get("formal", {}) or {}
        sva = formal.get("sva", "")
        bind_module = formal.get("bind_module", "")
        if not sva or not bind_module:
            # Backfill to formal_result (tool execution result).
            finding["formal_result"] = {
                "status": "ERROR",
                "engine": "sby_z3",
                "duration_s": 0.0,
                "error_log": "Missing sva or bind_module in PENDING finding",
                "sva": sva or "",
                "verdict": "ERROR",
            }
            continue

        project_name = f"sva_{idx:04d}"
        project_dir = work_dir / project_name
        result = _run_sby_z3(
            sva=sva,
            bind_module=bind_module,
            bind_signals=formal.get("bind_signals", []),
            clock=formal.get("clock", ""),
            reset=formal.get("reset", ""),
            rtl_files=rtl_files,
            project_dir=project_dir,
            depth=depth,
            timeout=timeout_per_sva,
            signal_widths=signal_widths,
        )
        # Backfill to formal_result (tool execution result).
        # Add the SVA that was actually executed and map status to verdict.
        finding["formal_result"] = {
            **result,
            "sva": sva,
            "verdict": result["status"],  # PASS/FAIL/UNKNOWN/TIMEOUT/ERROR
        }

    return findings


def _run_sby_z3(
    *,
    sva: str,
    bind_module: str,
    bind_signals: list[str],
    clock: str,
    reset: str,
    rtl_files: list[str | Path],
    project_dir: Path,
    depth: int,
    timeout: int,
    signal_widths: dict[str, int],
) -> dict[str, Any]:
    """Generate a SymbiYosys project, run it, parse the result."""
    project_dir.mkdir(parents=True, exist_ok=True)

    # Render bind file.
    bind_content = render_sva_bind(
        sva_text=sva,
        bind_module=bind_module,
        bind_signals=bind_signals,
        clock=clock,
        reset=reset,
        signal_widths=signal_widths,
    )
    bind_file = project_dir / "bind.sv"
    bind_file.write_text(bind_content, encoding="utf-8")

    # Generate .sby config.
    sby_config = _generate_sby_config(
        rtl_files=rtl_files,
        bind_file=bind_file,
        depth=depth,
    )
    sby_file = project_dir / "project.sby"
    sby_file.write_text(sby_config, encoding="utf-8")

    # Run sby.
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            ["sby", "-f", str(sby_file)],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.monotonic() - t0
        status, trace_file, error_log = _parse_sby_result(proc, project_dir)
        return {
            "status": status,
            "engine": "sby_z3",
            "duration_s": round(duration, 2),
            "trace_file": str(trace_file.relative_to(project_dir)) if trace_file else None,
            "error_log": error_log,
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "TIMEOUT",
            "engine": "sby_z3",
            "duration_s": timeout,
            "trace_file": None,
            "error_log": None,
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "engine": "sby_z3",
            "duration_s": round(time.monotonic() - t0, 2),
            "trace_file": None,
            "error_log": str(e)[:400],
        }


def _generate_sby_config(
    *,
    rtl_files: list[str | Path],
    bind_file: Path,
    depth: int,
) -> str:
    """Generate a .sby config for z3 BMC."""
    read_commands = "\n".join(
        f"read -sv {Path(f).resolve()}" for f in rtl_files
    )
    return f"""\
[options]
mode bmc
depth {depth}
expect pass,fail

[engines]
smtbmc z3

[script]
{read_commands}
read -formal {bind_file.resolve()}
prep -top bind_wrapper

[files]
"""


def _parse_sby_result(
    proc: subprocess.CompletedProcess,
    project_dir: Path,
) -> tuple[str, Path | None, str | None]:
    """Parse sby stdout/stderr to determine status.

    Returns:
        (status, trace_file, error_log)
    """
    stdout = proc.stdout.lower()
    stderr = proc.stderr

    # sby emits "PASS" / "FAIL" / "UNKNOWN" in stdout.
    if "status: passed" in stdout or "pass" in stdout:
        return "PASS", None, None
    if "status: failed" in stdout or "fail" in stdout:
        # Look for .vcd counterexample.
        trace_candidates = list(project_dir.glob("**/*.vcd"))
        trace = trace_candidates[0] if trace_candidates else None
        return "FAIL", trace, None
    if "status: unknown" in stdout or "unknown" in stdout:
        return "UNKNOWN", None, None

    # Fallback: treat as ERROR.
    error_snippet = (stderr or proc.stdout)[:400]
    return "ERROR", None, error_snippet


def _extract_signal_widths(rtl_files: list[str | Path]) -> dict[str, int]:
    """Extract signal bit widths from RTL using yosys.

    Returns a dict {signal_name: width}. Signals not found default to 1.

    Uses yosys to read the RTL, convert to JSON, then parse netnames/ports.
    If yosys is unavailable or fails, returns empty dict (render_sva_bind
    will default all signals to [0:0]).
    """
    if not rtl_files:
        return {}

    import json
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            json_out = tmpdir_path / "design.json"

            # Build yosys script: read all RTL files, hierarchy, write_json.
            read_cmds = "\n".join(f"read_verilog -sv {Path(f).resolve()}" for f in rtl_files)
            script = f"""{read_cmds}
hierarchy -auto-top
proc
write_json {json_out}
"""
            script_file = tmpdir_path / "extract.ys"
            script_file.write_text(script, encoding="utf-8")

            # Run yosys.
            proc = subprocess.run(
                ["yosys", "-q", "-s", str(script_file)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode != 0 or not json_out.exists():
                return {}

            # Parse JSON output.
            design = json.loads(json_out.read_text(encoding="utf-8"))
            widths: dict[str, int] = {}

            for module_name, module_data in design.get("modules", {}).items():
                # Ports.
                for port_name, port_info in module_data.get("ports", {}).items():
                    bits = port_info.get("bits", [])
                    widths[port_name] = len(bits) if isinstance(bits, list) else 1

                # Netnames (internal signals).
                for net_name, net_info in module_data.get("netnames", {}).items():
                    bits = net_info.get("bits", [])
                    widths[net_name] = len(bits) if isinstance(bits, list) else 1

            return widths

    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, Exception):
        # yosys not available, or RTL parse failed → fallback to empty.
        return {}
