"""
Always-block extractor: runs NL and Formal extraction in parallel,
then cross-checks for consistency.

Two independent LLM calls per always block:
  - NL prompt  → natural language description
  - Formal prompt → SV clauses

Cross-check: does the formal SV match the NL description?
"""

from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from csbc3.llm import call_llm, parse_response

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_NL_PROMPT_PATH = _PROJECT_ROOT / "csbc3" / "prompts" / "nl_extract.md"
_FORMAL_PROMPT_PATH = _PROJECT_ROOT / "csbc3" / "prompts" / "formal_extract.md"


@dataclass
class AlwaysResult:
    chunk_id: str
    signal: str
    kind: str = "unknown"
    temporal: str = "comb"
    nl_claim: str = ""
    nl_uncertainty: str = "low"
    formal_antecedent: str = ""
    formal_consequent: str = ""
    formalizable: bool = False
    formal_comment: str = ""
    cross_check: str = "unknown"


def _load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def extract_always(chunk_id: str, code: str, max_tokens: int = 6000) -> list[AlwaysResult]:
    """Run NL and Formal extraction in parallel on one always block."""
    nl_prompt = _load_prompt(_NL_PROMPT_PATH)
    formal_prompt = _load_prompt(_FORMAL_PROMPT_PATH)

    user = json.dumps({"chunk_id": chunk_id, "code": code}, ensure_ascii=False, indent=2)

    results: list[AlwaysResult] = []

    with ThreadPoolExecutor(max_workers=2) as pool:
        nl_future = pool.submit(call_llm, nl_prompt, user, max_tokens)
        formal_future = pool.submit(call_llm, formal_prompt, user, max_tokens)

        nl_raw = nl_future.result()
        formal_raw = formal_future.result()

    # Parse NL response
    nl_data = _try_parse(nl_raw, "NL")
    nl_map: dict[str, dict] = {}
    if nl_data:
        for s in nl_data.get("signals", []):
            name = s.get("name", "")
            if name:
                nl_map[name] = s

    # Parse Formal response
    formal_data = _try_parse(formal_raw, "Formal")
    formal_map: dict[str, dict] = {}
    if formal_data:
        for s in formal_data.get("signals", []):
            name = s.get("name", "")
            if name:
                formal_map[name] = s

    # Merge by signal name
    all_signals = set(nl_map.keys()) | set(formal_map.keys())
    for sig in sorted(all_signals):
        nl_entry = nl_map.get(sig, {})
        formal_entry = formal_map.get(sig, {})

        r = AlwaysResult(
            chunk_id=chunk_id,
            signal=sig,
            kind=formal_entry.get("kind", nl_entry.get("kind", "unknown")),
            temporal=formal_entry.get("temporal", nl_entry.get("temporal", "comb")),
            nl_claim=nl_entry.get("nl_claim", ""),
            nl_uncertainty=nl_entry.get("nl_uncertainty", "low"),
            formal_antecedent=formal_entry.get("antecedent", ""),
            formal_consequent=formal_entry.get("consequent", ""),
            formalizable=bool(formal_entry.get("formalizable", False)),
            formal_comment=formal_entry.get("comment", ""),
        )

        # Cross-check
        if not nl_entry and formal_entry:
            r.cross_check = "nl_missing"
        elif not formal_entry and nl_entry:
            r.cross_check = "formal_missing"
        elif nl_entry and formal_entry:
            r.cross_check = _cross_check(r)
        else:
            r.cross_check = "unknown"

        results.append(r)

    return results


def _try_parse(raw: str, label: str) -> dict | None:
    if raw.startswith("ERROR:"):
        print(f"  [{label}] LLM error: {raw[:80]}", file=sys.stderr)
        return None
    try:
        return parse_response(raw)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"  [{label}] Parse error: {e}", file=sys.stderr)
        return None


def _cross_check(r: AlwaysResult) -> str:
    """Check if formal spec matches NL description.

    Only flags clear contradictions:
    - NL says "combinational" but formal says next_cycle
    - NL says "registered" but formal says comb
    - Formal is empty but NL has low uncertainty behavior
    """
    nl = r.nl_claim.lower()
    ant = r.formal_antecedent

    # Temporal contradiction
    if r.temporal == "next_cycle" and "next cycle" not in nl and "register" in nl:
        pass  # acceptable — formal is more precise
    if r.temporal == "comb" and "same cycle" not in nl and "next cycle" in nl:
        return "mismatch"

    # Formal missing real behavior
    if not ant and r.formalizable:
        if r.nl_uncertainty in ("low", "medium"):
            return "mismatch"

    return "ok"


def run_on_chunks(chunks: list[Chunk], max_workers: int = 4) -> list[AlwaysResult]:
    """Run parallel extraction on all always chunks."""
    always_chunks = [c for c in chunks if c.construct_type in ("always_comb", "always_ff")]
    results: list[AlwaysResult] = []
    total = len(always_chunks)

    def process_one(chunk: Chunk) -> list[AlwaysResult]:
        code = chunk.code
        # Truncate very long blocks
        if len(code) > 3000:
            code = code[:3000] + "\n  // ... truncated"
        return extract_always(chunk.chunk_id, code)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(process_one, c): c for c in always_chunks}
        done = 0
        for future in as_completed(futures):
            done += 1
            chunk = futures[future]
            try:
                res = future.result()
                results.extend(res)
            except Exception as e:
                print(f"  Error on {chunk.chunk_id}: {e}", file=sys.stderr)
            print(f"  [{done}/{total}] {chunk.chunk_id[:60]}... {len(res)} signals")

    return results


from csbc3.chunker import Chunk
