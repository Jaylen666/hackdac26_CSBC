from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rtl_bug_agent.llm.client import OpenAICompatibleClient
from rtl_bug_agent.schema import RtlChunk


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = _PROJECT_ROOT / "config/prompts/chunk_spec_agu_structured_slim_en.md"


def generate_chunk_spec(
    chunk: RtlChunk,
    client: OpenAICompatibleClient,
    prompt_path: str | Path = DEFAULT_PROMPT,
    max_tokens: int = 6000,
) -> dict[str, Any]:
    prompt = Path(prompt_path).read_text(encoding="utf-8")
    user_payload = {
        "chunk": {
            "chunk_id": chunk.chunk_id,
            "kind": chunk.kind,
            "source_file": chunk.source_file,
            "module": chunk.module,
            "line_start": chunk.line_start,
            "line_end": chunk.line_end,
            "title": chunk.title,
            "context_summary": chunk.context_summary,
            "dependencies": chunk.dependencies,
        },
        "code_with_line_numbers": _with_line_numbers(chunk),
    }
    content = client.chat(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, indent=2)},
        ],
        max_tokens=max_tokens,
    )
    spec = _parse_json_object(content)
    spec.setdefault("chunk_id", chunk.chunk_id)
    spec.setdefault("source_file", chunk.source_file)
    spec.setdefault("line_start", chunk.line_start)
    spec.setdefault("line_end", chunk.line_end)
    spec.setdefault("guarantees", [])
    spec.setdefault("assumptions", [])
    spec.setdefault("uncertain_points", [])
    spec.setdefault("evidence_refs", [])
    spec.setdefault("security_implications", "")
    return spec


def _with_line_numbers(chunk: RtlChunk) -> str:
    numbered = []
    for offset, line in enumerate(chunk.code.splitlines(), start=chunk.line_start):
        numbered.append(f"{offset:5d}: {line}")
    return "\n".join(numbered)


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if not text:
        raise ValueError("LLM returned empty content")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("LLM spec output must be a JSON object")
    return value
