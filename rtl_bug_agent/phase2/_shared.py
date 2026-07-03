"""
Shared utilities for Phase 2 channels.

Consolidates functions that were previously copy-pasted across
channel_b.py, channel_c.py, channel_d.py, channel_f.py, layer2.py,
phase3.py, and phase3_agent.py.

Canonical location — update here, not in individual files.
"""

from __future__ import annotations

import json
import re
import time as _time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# LLM response parsing
# ---------------------------------------------------------------------------


def parse_llm_response(content: str) -> dict[str, Any]:
    """Parse the LLM JSON response, handling markdown wrapping.

    Handles:
    - Markdown code fences (```json ... ```)
    - Truncated JSON (closing brace missing due to max_tokens)
    - Embedded JSON objects within text
    - Partially truncated claim arrays

    This is the canonical version. Use this everywhere instead of
    reimplementing.
    """
    text = content.strip()
    if not text:
        raise ValueError("LLM returned empty content")

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Regex extraction
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Truncated JSON recovery — try to close the object
    for marker in ("},\n", "}", "\"]", "]\n"):
        idx = text.rfind(marker)
        if idx > 0:
            truncated = text[:idx + len(marker.rstrip())] + "]}"
            try:
                result = json.loads(truncated)
                result["_truncated"] = True
                return result
            except json.JSONDecodeError:
                try:
                    result = json.loads(text[:idx + len(marker.rstrip())] + "}")
                    result["_truncated"] = True
                    return result
                except json.JSONDecodeError:
                    continue

    # Salvage path for truncated claim arrays
    salvaged = _salvage_truncated_claims(text)
    if salvaged is not None:
        return {"claims": salvaged, "_truncated": True}

    raise ValueError(f"Cannot parse LLM response: {text[:500]}")


def _salvage_truncated_claims(text: str) -> list[dict[str, Any]] | None:
    """Recover complete claim objects from a truncated ``{"claims": [...]}``.

    Scans from the first ``[`` after the ``claims`` key, tracking brace depth
    (ignoring braces inside strings), and collects each top-level ``{...}``
    object that closed cleanly. A trailing object cut off by ``max_tokens`` is
    silently dropped.
    """
    key = re.search(r'"claims"\s*:\s*\[', text)
    if not key:
        return None
    start = key.end()

    objects: list[dict[str, Any]] = []
    depth = 0
    obj_start = -1
    in_str = False
    escaped = False

    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start >= 0:
                snippet = text[obj_start: i + 1]
                try:
                    objects.append(json.loads(snippet))
                except json.JSONDecodeError:
                    pass
                obj_start = -1
        elif ch == "]" and depth == 0:
            break

    return objects or None


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


def call_with_retry(fn, attempts: int = 3, delay: float = 1.0):
    """Call *fn* with retries on LLM failures.

    Returns None if all attempts are exhausted.
    """
    for a in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if a == attempts:
                print(f"ERROR (retries exhausted) ({exc})")
                return None
            _time.sleep(delay * a)


# ---------------------------------------------------------------------------
# JSONL checkpoint
# ---------------------------------------------------------------------------


class JsonlCheckpoint:
    """Append-only JSONL checkpoint for crash-resilient channel processing.

    On load, reads all previously persisted findings.
    On append_all, atomically writes new findings as JSONL lines.

    This avoids re-processing signals/specs that were already handled
    before a crash.
    """

    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None

    def load(self) -> list[dict[str, Any]]:
        if not self.path or not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def append_all(self, findings: list[dict[str, Any]]) -> None:
        if not self.path or not findings:
            return
        lines = "\n".join(
            json.dumps(f, ensure_ascii=False) for f in findings
        )
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(lines + "\n")
