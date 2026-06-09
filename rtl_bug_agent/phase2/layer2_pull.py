"""
Layer 2 (Pull Model): LLM-driven semantic claim matching.

Pre-extracts ALL verifiable claims from the official spec once.
For each Phase 2 finding, sends the finding + ALL claims to the LLM.
The LLM picks the semantically relevant claims and judges consistency.

No script-based keyword indexing — the LLM does the matching.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rtl_bug_agent.llm.client import OpenAICompatibleClient
from rtl_bug_agent.phase2.signal_graph import SignalGraph

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EXTRACT_PROMPT = _PROJECT_ROOT / "config/prompts/phase2/layer2_extract_claims.md"
_MATCH_PROMPT = _PROJECT_ROOT / "config/prompts/phase2/layer2_claim_check.md"


# ------------------------------------------------------------------
# Step 1: Extract ALL claims
# ------------------------------------------------------------------


def extract_all_claims(
    doc_path: str, client: OpenAICompatibleClient, attempts: int = 3
) -> list[dict[str, Any]]:
    prompt = _EXTRACT_PROMPT.read_text(encoding="utf-8")
    doc_text = Path(doc_path).read_text(encoding="utf-8")
    if len(doc_text) > 20000:
        doc_text = doc_text[:20000] + "\n\n[... truncated for length ...]"

    import time as _time
    for a in range(1, attempts + 1):
        content = client.chat(
            messages=[{"role": "system", "content": prompt},
                       {"role": "user", "content": doc_text}],
            max_tokens=8000,
        )
        claims = _parse_claims(content)
        if claims:
            return claims
        if a < attempts:
            _time.sleep(2 * a)
    return []


def _parse_claims(content: str) -> list[dict[str, Any]]:
    # DeepSeek sometimes returns empty content; fall back to reasoning
    if not content or not content.strip():
        return []

    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    data = None

    # Try strict parse
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try truncation recovery: find last complete claim object
    if data is None:
        for end_pattern in ('"}', '"}', '"}'):
            idx = text.rfind(end_pattern)
            if idx > 0:
                try:
                    data = json.loads(text[:idx + 2] + "]}")
                    break
                except json.JSONDecodeError:
                    continue

    # Try regex: extract the claims array
    if data is None:
        match = re.search(r'"claims"\s*:\s*(\[.*\])', text, flags=re.DOTALL)
        if match:
            try:
                data = {"claims": json.loads(match.group(1))}
            except json.JSONDecodeError:
                pass

    # Last resort: parse individual claim objects one by one
    if data is None:
        claim_texts = re.findall(r'\{\s*"claim"\s*:\s*"([^"]*)"', text)
        if claim_texts:
            data = [{"claim": t, "source": "", "signals": [], "keywords": []} for t in claim_texts]

    if isinstance(data, dict):
        data = data.get("claims", data.get("findings", []))
    if not isinstance(data, list):
        return []

    claims = []
    for item in data:
        if isinstance(item, str):
            claims.append({"claim": item, "source": "", "signals": [], "keywords": []})
        elif isinstance(item, dict):
            claims.append(item)
    return claims


# ------------------------------------------------------------------
# Step 2: LLM reads full spec directly (no claim extraction)
# ------------------------------------------------------------------


def verify_finding_raw_spec(
    finding: dict[str, Any],
    spec_text: str,
    graph: SignalGraph,
    client: OpenAICompatibleClient,
) -> dict[str, Any]:
    """One LLM call with finding + full spec text.  LLM finds relevant
    design intent in the spec and judges consistency against the RTL.
    No pre-extracted claims needed."""
    prompt = _MATCH_PROMPT.read_text(encoding="utf-8")

    # Lightweight RTL context from the finding's involved specs
    rtl_snippets = []
    seen = set()
    for sig in finding.get("involved_signals", [])[:5]:
        info = graph.signals.get(sig)
        if not info: continue
        for spec_id in info.drivers + info.consumers:
            if spec_id in seen: continue
            seen.add(spec_id)
            spec = graph.specs.get(spec_id)
            if spec:
                rtl_snippets.append({
                    "spec_id": spec_id,
                    "summary": spec.get("summary", "")[:100],
                    "behavior": spec.get("behavior", "")[:300],
                    "guarantees": [g.get("property","")[:200] for g in spec.get("guarantees",[])[:2]],
                })

    # Truncate spec to fit context window
    if len(spec_text) > 25000:
        spec_text = spec_text[:25000] + "\n\n[... truncated ...]"

    payload = {
        "finding": {
            "title": finding.get("title", ""),
            "verdict": finding.get("verdict", ""),
            "involved_signals": finding.get("involved_signals", [])[:10],
            "contradiction": finding.get("contradiction", "")[:400],
        },
        "official_spec": spec_text,
        "rtl_context": rtl_snippets[:4],
    }

    content = client.chat(
        messages=[{"role": "system", "content": prompt},
                   {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)}],
        max_tokens=2500,
    )
    return _parse_verdict(content)


# ------------------------------------------------------------------
# Legacy: LLM picks from pre-extracted claims (one call per finding)
# ------------------------------------------------------------------


def verify_finding_llm_match(
    finding: dict[str, Any],
    all_claims: list[dict[str, Any]],
    graph: SignalGraph,
    client: OpenAICompatibleClient,
) -> dict[str, Any]:
    """One LLM call: pick relevant claims, then judge the finding against them."""

    prompt = _MATCH_PROMPT.read_text(encoding="utf-8")

    # Lightweight RTL context from the finding's involved specs
    rtl_snippets = []
    seen = set()
    for sig in finding.get("involved_signals", [])[:5]:
        info = graph.signals.get(sig)
        if not info: continue
        for spec_id in info.drivers + info.consumers:
            if spec_id in seen: continue
            seen.add(spec_id)
            spec = graph.specs.get(spec_id)
            if spec:
                rtl_snippets.append({
                    "spec_id": spec_id,
                    "summary": spec.get("summary", "")[:100],
                    "behavior": spec.get("behavior", "")[:300],
                    "guarantees": [g.get("property","")[:200] for g in spec.get("guarantees",[])[:2]],
                })

    # Compact claims list
    compact_claims = [
        {"id": i + 1, "claim": c.get("claim",""), "source": c.get("source","")}
        for i, c in enumerate(all_claims)
    ]

    payload = {
        "finding": {
            "title": finding.get("title", ""),
            "verdict": finding.get("verdict", ""),
            "involved_signals": finding.get("involved_signals", [])[:10],
            "contradiction": finding.get("contradiction", "")[:400],
        },
        "all_claims": compact_claims,
        "rtl_context": rtl_snippets[:4],
    }

    content = client.chat(
        messages=[{"role": "system", "content": prompt},
                   {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)}],
        max_tokens=2500,
    )
    return _parse_verdict(content)


def _parse_verdict(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {"verdict": "ERROR", "reasoning": content[:500]}
        return json.loads(match.group(0))
