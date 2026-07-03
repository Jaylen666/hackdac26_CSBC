"""
Phase 1: Extract structured formal clauses from RTL chunks using LLM.

The LLM is STRICTLY required to output SV expressions for antecedent/consequent,
not prose.  Clauses marked formalizable=false are skipped by the Z3 engine and
handled by the LLM residual channel instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rtl_bug_agent.schema import RtlChunk

API_KEY = "sk-f59e93f159894ca88aa2fcb7e9d2b749"
BASE_URL = "https://api.deepseek.com/v1"
MODEL = "deepseek-v4-flash"

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PROMPT_PATH = _PROJECT_ROOT / "csbc2" / "prompts" / "extract_clauses.md"


@dataclass
class FormalClause:
    signal: str
    kind: str            # guarantee | assumption
    antecedent: str      # SV expression
    consequent: str      # SV expression
    temporal: str        # comb | next_cycle | always
    formalizable: bool
    claim: str = ""
    risk: str = ""
    spec_id: str = ""
    item_id: str = ""
    source_file: str = ""
    line_start: int = 0
    line_end: int = 0


def call_llm(prompt: str, user_content: str, max_tokens: int = 4000) -> str:
    """Call the DeepSeek v4-flash API and return raw response text."""
    import urllib.request

    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"ERROR: {e}"


def parse_response(text: str) -> dict[str, Any]:
    """Parse LLM JSON response, stripping markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Try to find a JSON object
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        raise ValueError(f"No JSON object found in response: {text[:300]}")
    return json.loads(m.group(0))


def extract_clauses_from_chunk(
    chunk: RtlChunk,
    max_tokens: int = 4000,
) -> list[FormalClause]:
    """Extract formal clauses from a single RTL chunk."""
    prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    user_content = json.dumps({
        "chunk_id": chunk.chunk_id,
        "kind": chunk.kind,
        "module": chunk.module,
        "source_file": chunk.source_file,
        "line_start": chunk.line_start,
        "line_end": chunk.line_end,
        "code": _with_line_numbers(chunk),
    }, ensure_ascii=False, indent=2)

    raw = call_llm(prompt, user_content, max_tokens)
    if raw.startswith("ERROR:"):
        print(f"  LLM error: {raw}")
        return []

    try:
        parsed = parse_response(raw)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"  Parse error: {e}")
        return []

    clauses: list[FormalClause] = []
    spec_id = chunk.chunk_id

    for item in parsed.get("guarantees", []):
        if isinstance(item, dict):
            clauses.append(_to_clause(item, "guarantee", spec_id, chunk))

    for item in parsed.get("assumptions", []):
        if isinstance(item, dict):
            clauses.append(_to_clause(item, "assumption", spec_id, chunk))

    return clauses


def _to_clause(item, kind, spec_id, chunk):
    return _dict_to_clause(item, kind, spec_id, chunk)

def _dict_to_clause(
    item: dict[str, Any],
    kind: str,
    spec_id: str,
    chunk: RtlChunk,
) -> FormalClause:
    return FormalClause(
        signal=str(item.get("signal", "")),
        kind=kind,
        antecedent=str(item.get("antecedent", "")),
        consequent=str(item.get("consequent", "")),
        temporal=str(item.get("temporal", "comb")),
        formalizable=bool(item.get("formalizable", False)),
        claim=str(item.get("claim", "")),
        risk=str(item.get("risk", "")),
        spec_id=spec_id,
        item_id=f"{kind[:1].upper()}{len(item.get('signal',''))}",
        source_file=chunk.source_file,
        line_start=chunk.line_start,
        line_end=chunk.line_end,
    )


def _with_line_numbers(chunk: RtlChunk) -> str:
    numbered = []
    for offset, line in enumerate(chunk.code.splitlines(), start=chunk.line_start):
        numbered.append(f"{offset:5d}: {line}")
    return "\n".join(numbered)


def filter_formalizable(clauses: list[FormalClause]) -> list[FormalClause]:
    return [c for c in clauses if c.formalizable]


def batch_extract(
    chunks: list[RtlChunk],
    max_chunks: int = 10,
) -> list[FormalClause]:
    """Extract clauses from chunks, limited to max_chunks for testing."""
    all_clauses: list[FormalClause] = []
    for i, chunk in enumerate(chunks[:max_chunks]):
        print(f"  [{i+1}/{min(len(chunks), max_chunks)}] {chunk.chunk_id[:60]}...", end=" ", flush=True)
        clauses = extract_clauses_from_chunk(chunk)
        formal = [c for c in clauses if c.formalizable]
        print(f"{len(clauses)} clauses ({len(formal)} formalizable)")
        all_clauses.extend(clauses)
    return all_clauses
