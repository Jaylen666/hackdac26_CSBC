from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rtl_bug_agent.phase2.formal_sketch import pick_property_draft, render_property_assertion


@dataclass
class FormalCheckResult:
    finding_id: str
    status: str
    confidence: float = 0.0
    assertion: str = ""
    module: str = ""
    spec_id: str = ""
    solver: str = ""
    workdir: str = ""
    log: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = {
            "finding_id": self.finding_id,
            "status": self.status,
            "confidence": round(float(self.confidence or 0.0), 3),
            "assertion": self.assertion,
            "module": self.module,
            "spec_id": self.spec_id,
            "solver": self.solver,
            "workdir": self.workdir,
            "log": self.log,
        }
        if self.error:
            out["error"] = self.error
        return out


def run_formal_checks(
    findings: list[dict[str, Any]],
    graph: Any,
    *,
    out_root: str | Path,
    top_n: int = 8,
    depth: int = 20,
) -> list[dict[str, Any]]:
    out_root = Path(out_root)
    base_dir = out_root / "formal_checks"
    base_dir.mkdir(parents=True, exist_ok=True)

    selected = sorted(findings, key=lambda f: f.get("score", 0), reverse=True)[:top_n]
    results: list[dict[str, Any]] = []
    for idx, finding in enumerate(selected, start=1):
        draft = _pick_draft(finding, graph)
        workdir = base_dir / f"{idx:02d}_{str(finding.get('finding_id', f'F-{idx:04d}')).replace('/', '_')}"
        if not draft:
            results.append(
                FormalCheckResult(
                    finding_id=str(finding.get("finding_id", f"F-{idx:04d}")),
                    status="SKIPPED",
                    confidence=_phase3_confidence(finding),
                    workdir=str(workdir),
                    error="no high-confidence property draft",
                ).to_dict()
            )
            continue
        results.append(_run_one(finding, draft, workdir, depth=depth).to_dict())

    (base_dir / "summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return results


def _pick_draft(finding: dict[str, Any], graph: Any) -> dict[str, Any] | None:
    draft = finding.get("formal_draft")
    if isinstance(draft, dict) and draft.get("assertion"):
        return draft
    picked = pick_property_draft(finding, graph, min_confidence=0.75)
    if not picked:
        return None
    assertion = picked.get("assertion") or render_property_assertion(picked.get("sketch", {}))
    if not assertion:
        return None
    picked = dict(picked)
    picked["assertion"] = assertion
    return picked


def _run_one(
    finding: dict[str, Any],
    draft: dict[str, Any],
    workdir: Path,
    *,
    depth: int,
) -> FormalCheckResult:
    workdir.mkdir(parents=True, exist_ok=True)
    source_file = str(draft.get("source_file", "") or "")
    module = str(draft.get("module", "") or "")
    sketch = draft.get("sketch", {})
    if not isinstance(sketch, dict):
        sketch = {}
    assertion = _render_immediate_assertion(sketch)
    solver = _select_solver()
    if not solver:
        return FormalCheckResult(
            finding_id=str(finding.get("finding_id", "")),
            status="SKIPPED",
            confidence=_phase3_confidence(finding),
            assertion=assertion,
            module=module,
            spec_id=str(draft.get("spec_id", "")),
            workdir=str(workdir),
            error="no SMT solver found on PATH",
        )

    target = _rewrite_target(Path(source_file), workdir, finding, draft, sketch)
    if target is None:
        return FormalCheckResult(
            finding_id=str(finding.get("finding_id", "")),
            status="SKIPPED",
            confidence=_phase3_confidence(finding),
            assertion=assertion,
            module=module,
            spec_id=str(draft.get("spec_id", "")),
            solver=solver,
            workdir=str(workdir),
            error="unable to locate module boundary for insertion",
        )

    script = workdir / "formal_check.ys"
    log_path = workdir / "run.log"
    script.write_text(
        _render_yosys_script(str(draft.get("module", "") or source_file.stem), target.name, depth),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["yosys", "-q", "-s", str(script)],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=600,
    )
    log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    log_path.write_text(log, encoding="utf-8")
    status = _status_from_output(log, proc.returncode)
    return FormalCheckResult(
        finding_id=str(finding.get("finding_id", "")),
        status=status,
        confidence=_phase3_confidence(finding),
        assertion=assertion,
        module=module,
        spec_id=str(draft.get("spec_id", "")),
        solver=solver,
        workdir=str(workdir),
        log=log,
    )


def _rewrite_target(
    source_file: Path,
    workdir: Path,
    finding: dict[str, Any],
    draft: dict[str, Any],
    sketch: dict[str, Any],
) -> Path | None:
    if not source_file.exists():
        return None
    lines = source_file.read_text(encoding="utf-8").splitlines()
    module_name = str(draft.get("module", "") or source_file.stem)
    bounds = _find_module_bounds(lines, module_name)
    if bounds is None:
        return None
    start, end = bounds
    assertion_block = _render_assertion_block(
        finding=finding,
        draft=draft,
        sketch=sketch,
    )
    rendered = list(lines)
    rendered[end:end] = assertion_block
    target = workdir / source_file.name
    target.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    return target


def _render_yosys_script(module_name: str, source_name: str, depth: int) -> str:
    return f"""read_verilog -sv -formal {source_name}
hierarchy -top {module_name}
proc
opt
flatten
sat -seq {depth} -prove-asserts -set-def-inputs -verify
"""


def _select_solver() -> str:
    for solver in ("boolector", "bitwuzla", "cvc5", "z3", "yices", "mathsat"):
        if shutil.which(solver):
            return solver
    return ""


def _status_from_output(text: str, returncode: int) -> str:
    """Classify yosys `sat` output into PASS / FAIL / ERROR / UNKNOWN.

    Uses yosys-specific result lines rather than loose substring matching,
    because tokens like "SAT" appear in "UNSAT" and unrelated log noise.
    """
    upper = text.upper()
    # yosys `sat -prove-asserts` emits explicit verdict lines.
    if "SAT PROOF FINISHED - NO UNREACHED ASSERTIONS" in upper:
        return "PASS"
    if "ASSERTION FAILED" in upper or "FAILED ASSERTION" in upper:
        return "FAIL"
    if re.search(r"\bSOLVING\b.*\bUNSAT\b", upper) or "PROOF FINISHED" in upper:
        return "PASS"
    if re.search(r"\bSOLVING\b.*\bSAT\b", upper) and "UNSAT" not in upper:
        return "FAIL"
    # Fall back to coarse markers only if no structured verdict matched.
    if "ASSERTION" in upper and "PASS" in upper:
        return "PASS"
    if returncode != 0:
        return "ERROR"
    return "UNKNOWN"


def _phase3_confidence(finding: dict[str, Any]) -> float:
    phase3 = finding.get("phase3") or {}
    try:
        return float(phase3.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _sanitize_comment(text: str) -> str:
    return re.sub(r"[\r\n]+", " ", text).strip()


def _find_module_bounds(lines: list[str], module_name: str) -> tuple[int, int] | None:
    start_idx = None
    for idx, line in enumerate(lines):
        name = _module_name(line)
        if name and (not module_name or name == module_name):
            start_idx = idx
            break
    if start_idx is None:
        if not module_name:
            return None
        for idx, line in enumerate(lines):
            if _module_name(line):
                start_idx = idx
                break
        if start_idx is None:
            return None

    depth = 0
    for idx in range(start_idx, len(lines)):
        line = lines[idx].strip()
        if _module_name(line):
            depth += 1
        elif line.startswith("endmodule"):
            depth -= 1
            if depth == 0:
                return start_idx, idx
    return None


def _module_name(line: str) -> str:
    m = re.match(r"^module(?:\s+automatic)?\s+([A-Za-z_][A-Za-z0-9_$]*)\b", line.strip())
    return m.group(1) if m else ""


def _render_assertion_block(
    *,
    finding: dict[str, Any],
    draft: dict[str, Any],
    sketch: dict[str, Any],
) -> list[str]:
    clock = str(sketch.get("clock") or "clk_i").strip() or "clk_i"
    reset = _reset_enable_condition(str(sketch.get("reset") or ""))
    body = _render_immediate_assertion(sketch)
    comment = _sanitize_comment(str(finding.get("title", "")))
    src = _sanitize_comment(str(draft.get("source_file", "")))
    block = [
        "  // formal_check inserted assertion",
        f"  // source: {src}",
        f"  // finding: {comment}",
        f"  always_ff @(posedge {clock}) begin",
    ]
    if reset:
        block.append(f"    if ({reset}) begin")
        block.append(f"      assert ({body});")
        block.append("    end")
    else:
        block.append(f"    assert ({body});")
    block.append("  end")
    return block


def _render_immediate_assertion(sketch: dict[str, Any]) -> str:
    antecedent = str(sketch.get("antecedent") or "").strip()
    consequent = str(sketch.get("consequent") or "").strip()
    temporal_shape = str(sketch.get("temporal_shape") or "").strip()
    if antecedent and consequent:
        if temporal_shape == "next_cycle":
            return f"!($past(({antecedent}))) || ({consequent})"
        return f"!(({antecedent})) || ({consequent})"
    if consequent:
        return consequent
    if antecedent:
        return antecedent
    return "1'b1"


def _reset_enable_condition(reset: str) -> str:
    reset = reset.strip()
    if not reset:
        return ""
    if re.search(r"(_ni|_n|_b)$", reset.lower()):
        return reset
    return f"!{reset}"
