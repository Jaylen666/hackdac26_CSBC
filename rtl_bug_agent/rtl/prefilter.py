"""
RTL pre-filter: classify .sv file sections as BEHAVIORAL / STRUCTURAL.

Uses one LLM call per file to identify mechanically-repeated template
code (auto-generated register instances, signal-declaration loops, etc.)
that can be safely skipped.  Behavioural and mixed sections are kept.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rtl_bug_agent.llm.client import OpenAICompatibleClient

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PROMPT_PATH = _PROJECT_ROOT / "config/prompts/rlt_prefilter.md"


def _build_prompt() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    return _DEFAULT_PROMPT


def classify_sections(
    file_path: str | Path,
    client: OpenAICompatibleClient,
    max_lines_per_call: int = 1500,
) -> list[tuple[int, int]]:
    """Return a list of (start_line, end_line) ranges to **skip**.

    Only one LLM call per file.  If the file is small or the LLM
    cannot classify it, returns an empty list (everything kept).
    """
    src = Path(file_path)
    lines = src.read_text(encoding="utf-8").splitlines()
    total = len(lines)

    if total < 200:
        return []  # small files — analyse everything

    # Truncate to keep the LLM call affordable
    view_lines = lines[:max_lines_per_call]
    numbered = "\n".join(
        f"{i + 1:5d}: {line[:80]}" for i, line in enumerate(view_lines)
    )

    prompt = _build_prompt()
    user = (
        f"File: {src.name}  ({total} lines, showing first {len(view_lines)})\n\n"
        f"{numbered}"
    )

    content = client.chat(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user},
        ],
        max_tokens=4000,
    )

    ranges = _parse_sections(content)
    # Validate: clamp to actual file bounds
    valid: list[tuple[int, int]] = []
    for start, end in ranges:
        start = max(1, int(start))
        end = min(total, int(end))
        if end > start and (end - start + 1) < total * 0.9:
            valid.append((start, end))
    return valid


def _parse_sections(content: str) -> list[tuple[int, int]]:
    """Parse LLM response into (start, end) ranges.

    Accepts: ``[[358, 1943], [2100, 2200]]`` or a JSON array of dicts.
    """
    text = content.strip()
    # Strip markdown and reasoning prefix
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    # Look for the last JSON array in the text (skip reasoning preamble)
    arrays = list(re.finditer(r"\[[\d\s,\[\]]+\]", text))
    if arrays:
        text = arrays[-1].group(0)  # take the last array

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    results: list[tuple[int, int]] = []
    for item in data:
        if isinstance(item, list) and len(item) >= 2:
            results.append((int(item[0]), int(item[1])))
        elif isinstance(item, dict):
            typ = str(item.get("type", "")).upper()
            if typ == "STRUCTURAL":
                s = item.get("start", item.get("line_start", 0))
                e = item.get("end", item.get("line_end", 0))
                if s and e:
                    results.append((int(s), int(e)))
    return results


# ------------------------------------------------------------------
# Default prompt (embedded — also available as config/prompts/rtl_prefilter.md)
# ------------------------------------------------------------------

_DEFAULT_PROMPT = """Identify line ranges that are purely mechanical repetition
of the same template (e.g. 50 identical `prim_subreg_ext` instantiations).
Do NOT skip logic blocks (always_comb, always_ff, conditions, gating).

Output ONLY: [[start1, end1], [start2, end2], ...]
No explanation.  Empty array [] if nothing qualifies.
"""
