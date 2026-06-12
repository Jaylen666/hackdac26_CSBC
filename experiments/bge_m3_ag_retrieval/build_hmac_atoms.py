#!/usr/bin/env python3
"""Build retrieval atoms from existing module spec JSON files."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path("/home/smy/rtl_bug_agent")
DEFAULT_SPECS = ROOT / "output/specs"
DEFAULT_OUT = Path(__file__).resolve().parent / "out/atoms_hmac.jsonl"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _signal_tokens(signals: list[str]) -> str:
    toks: list[str] = []
    for sig in signals:
        toks.append(sig)
        toks.extend(part for part in re.split(r"[_\W]+", sig) if part)
    return " ".join(dict.fromkeys(toks))


def _refs_text(refs: Any) -> str:
    if isinstance(refs, list):
        return " ".join(str(r) for r in refs)
    if refs:
        return str(refs)
    return ""


def _base_context(spec: dict[str, Any]) -> str:
    fields = [
        f"chunk_id: {spec.get('chunk_id', '')}",
        f"summary: {spec.get('summary', '')}",
        f"security_implications: {spec.get('security_implications', '')}",
        f"source_file: {spec.get('source_file', '')}",
        f"line_range: {spec.get('line_start', '')}-{spec.get('line_end', '')}",
    ]
    return "\n".join(fields)


def _make_atom(
    spec: dict[str, Any],
    spec_path: Path,
    kind: str,
    index: int,
    text: str,
    signals: list[str],
    source_refs: Any,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    chunk_id = spec.get("chunk_id", spec_path.stem)
    atom_id = f"{chunk_id}::{kind}::{index}"
    context = _base_context(spec)
    signal_text = _signal_tokens(signals)
    embedding_text = "\n".join(
        part
        for part in [
            f"kind: {kind}",
            f"text: {text}",
            f"signals: {signal_text}",
            f"source_refs: {_refs_text(source_refs)}",
            context,
        ]
        if part.strip()
    )
    atom = {
        "atom_id": atom_id,
        "kind": kind,
        "spec_id": chunk_id,
        "spec_path": str(spec_path),
        "source_file": spec.get("source_file", ""),
        "line_start": spec.get("line_start"),
        "line_end": spec.get("line_end"),
        "text": text,
        "signals": signals,
        "source_refs": source_refs if source_refs is not None else [],
        "embedding_text": embedding_text,
    }
    if extra:
        atom.update(extra)
    return atom


def _iter_specs(specs_dir: Path, module: str) -> list[Path]:
    paths = sorted(specs_dir.glob(f"{module}*.json"))
    return [p for p in paths if p.is_file()]


def build_atoms(specs_dir: Path, module: str) -> list[dict[str, Any]]:
    atoms: list[dict[str, Any]] = []
    for path in _iter_specs(specs_dir, module):
        spec = _load_json(path)
        if "chunk_id" not in spec:
            continue

        for idx, assumption in enumerate(spec.get("assumptions", []) or []):
            if not isinstance(assumption, dict):
                continue
            text = assumption.get("constraint", "")
            if not text:
                continue
            bug_relevance = assumption.get("bug_relevance", "")
            full_text = "\n".join(
                part for part in [text, f"bug_relevance: {bug_relevance}"] if part
            )
            atoms.append(
                _make_atom(
                    spec,
                    path,
                    "assumption",
                    idx,
                    full_text,
                    list(assumption.get("related_signals", []) or []),
                    assumption.get("source_refs", []),
                    {"raw": assumption},
                )
            )

        for idx, guarantee in enumerate(spec.get("guarantees", []) or []):
            if not isinstance(guarantee, dict):
                continue
            text = guarantee.get("property", "")
            if not text:
                continue
            atoms.append(
                _make_atom(
                    spec,
                    path,
                    "guarantee",
                    idx,
                    text,
                    list(guarantee.get("output_signals", []) or []),
                    guarantee.get("source_refs", []),
                    {"raw": guarantee},
                )
            )

        for idx, point in enumerate(spec.get("uncertain_points", []) or []):
            text = str(point).strip()
            if not text:
                continue
            signals = sorted(set(re.findall(r"`([^`]+)`", text)))
            atoms.append(
                _make_atom(
                    spec,
                    path,
                    "uncertain",
                    idx,
                    text,
                    signals,
                    spec.get("evidence_refs", []),
                    {"raw": point},
                )
            )

    return atoms


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--specs-dir", type=Path, default=DEFAULT_SPECS)
    parser.add_argument("--module", default="hmac")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    atoms = build_atoms(args.specs_dir, args.module)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for atom in atoms:
            f.write(_json_dumps(atom) + "\n")

    counts: dict[str, int] = {}
    for atom in atoms:
        counts[atom["kind"]] = counts.get(atom["kind"], 0) + 1
    print(f"Wrote {len(atoms)} atoms to {args.out}")
    print(_json_dumps({"counts": counts}))


if __name__ == "__main__":
    main()
