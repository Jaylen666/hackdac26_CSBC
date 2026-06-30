"""
Layer 2: Official Spec — Implementation Consistency
=====================================================

Two-step pipeline:

1. **Extract** — LLM reads the official design document (e.g.
   ``theory_of_operation.md``) and extracts verifiable claims with
   signal names, keywords, and module scopes.

2. **Verify** — For each claim, full-text search across all RTL spec
   fields (``SignalGraph.search()``), then ask the LLM whether the
   implementation satisfies the claim.

The claim extraction step replaces hand-written claims (the v1
prototype in ``make_claims_for_hmac()``) with generic, IP-agnostic
LLM-driven extraction.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rtl_bug_agent.llm.client import OpenAICompatibleClient
from rtl_bug_agent.phase2.signal_graph import SignalGraph

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_VERIFY_PROMPT = _PROJECT_ROOT / "config/prompts/phase2/layer2_claim_check.md"
DEFAULT_EXTRACT_PROMPT = _PROJECT_ROOT / "config/prompts/phase2/layer2_extract_claims.md"


def _call_with_retry(fn, attempts: int = 3, delay: float = 1.0):
    import time as _time
    for a in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if a == attempts:
                print(f"ERROR (retries exhausted) ({exc})")
                return None
            _time.sleep(delay * a)

# Default paths to official design documents (configurable per IP).
DEFAULT_OFFICIAL_DOCS: dict[str, str] = {
    "hmac": str(
        Path("/home/smy/opentitan/hw/ip/hmac/doc/theory_of_operation.md")
    ),
    "kmac": str(
        Path("/home/smy/opentitan/hw/ip/kmac/doc/theory_of_operation.md")
    ),
    "rv_dm": str(
        Path("/home/smy/opentitan/hw/ip/rv_dm/doc/theory_of_operation.md")
    ),
    "aes": str(
        Path("/home/smy/opentitan/hw/ip/aes/doc/theory_of_operation.md")
    ),
    "uart": str(
        Path("/home/smy/opentitan/hw/ip/uart/doc/theory_of_operation.md")
    ),
    "keymgr": str(
        Path("/home/smy/opentitan/hw/ip/keymgr/doc/theory_of_operation.md")
    ),
    "dma": str(
        Path("/home/smy/opentitan/hw/ip/dma/doc/theory_of_operation.md")
    ),
    "tlul": str(
        Path("/home/smy/opentitan/hw/ip/tlul/doc/TlulProtocolChecker.md")
    ),
    "soc_dbg_ctrl": str(
        Path("/home/smy/opentitan/hw/ip/soc_dbg_ctrl/doc/theory_of_operation.md")
    ),
}


# ---------------------------------------------------------------------------
# Step 1: Claim extraction (LLM-driven)
# ---------------------------------------------------------------------------


def extract_claims(
    doc_path: str | Path,
    client: OpenAICompatibleClient,
    prompt_path: str | Path = DEFAULT_EXTRACT_PROMPT,
    max_tokens: int = 32000,
) -> list[dict[str, Any]]:
    """Read an official design document and use the LLM to extract
    verifiable claims.

    Parameters
    ----------
    doc_path:
        Path to the official documentation (Markdown, plain text, etc.).
    client:
        LLM client.
    prompt_path:
        Path to the claim-extraction prompt template.
    max_tokens:
        Max output tokens for the LLM call.

    Returns
    -------
    A list of claim dicts, each with keys ``claim``, ``source``,
    ``signals``, ``keywords``, ``scope`` — ready for ``run_layer2()``.
    """
    doc_text = Path(doc_path).read_text(encoding="utf-8")

    # Truncate very large docs to stay within context limits
    if len(doc_text) > 20000:
        doc_text = doc_text[:20000] + "\n\n[... truncated for length ...]"

    prompt_template = Path(prompt_path).read_text(encoding="utf-8")

    payload = {
        "document_path": str(doc_path),
        "document": doc_text,
    }

    content = client.chat(
        messages=[
            {"role": "system", "content": prompt_template},
            {"role": "user",
             "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ],
        max_tokens=max_tokens,
    )

    parsed = _parse_llm_response(content)
    claims = parsed.get("claims", [])

    # Ensure every claim has required fields
    for c in claims:
        c.setdefault("signals", [])
        c.setdefault("keywords", [])
        c.setdefault("scope", "")

    return claims


def extract_claims_for_ip(
    ip_name: str,
    client: OpenAICompatibleClient,
    doc_paths: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Convenience: extract claims for a named IP using its default docs.

    Parameters
    ----------
    ip_name:
        IP key in ``DEFAULT_OFFICIAL_DOCS`` (e.g. ``"hmac"``).
    client:
        LLM client.
    doc_paths:
        Override the default doc path mapping.
    """
    paths = doc_paths or DEFAULT_OFFICIAL_DOCS
    doc_path = paths.get(ip_name)
    if not doc_path:
        raise KeyError(
            f"No default doc for IP '{ip_name}'. "
            f"Known: {list(paths.keys())}"
        )
    print(f"  Layer2: extracting claims from {doc_path} ...")
    claims = extract_claims(doc_path, client)
    print(f"  Layer2: extracted {len(claims)} claims")
    return claims


# ---------------------------------------------------------------------------
# Deprecated: hand-written claims (replaced by extract_claims)
# ---------------------------------------------------------------------------


def make_claims_for_hmac() -> list[dict[str, Any]]:
    """DEPRECATED — use ``extract_claims_for_ip("hmac", client)`` instead.

    Kept as a fallback for offline / no-LLM scenarios.
    """
    return [
        {
            "claim": (
                "In HMAC mode, the message length for the second (outer) "
                "round is calculated as: block_size + digest_size_in_bits. "
                "For SHA-256: 512 + 256 = 768 bits. "
                "For SHA-384/512: 1024 + 384 = 1408 bits, "
                "1024 + 512 = 1536 bits. "
                "The implementation must distinguish SHA-384 from SHA-512."
            ),
            "source": "theory_of_operation.md:L110-120",
            "signals": ["sha_msg_len", "digest_size_i", "digest_size"],
            "keywords": ["SHA2_512", "BlockSizeSHA512", "outer", "message length"],
            "scope": "hmac_core",
        },
        {
            "claim": (
                "HMAC computation has two rounds. Completion indicators "
                "(hmac_done and STATUS.hmac_idle) must be consistent with "
                "the two-round timeline."
            ),
            "source": "theory_of_operation.md:L97-121",
            "signals": ["hash_done_event", "in_process", "hmac_done", "hmac_idle"],
            "keywords": ["done", "idle", "complete", "process", "cool_down"],
            "scope": "hmac",
        },
        {
            "claim": (
                "Secret key updates and wipe_secret should only happen "
                "when the engine is idle. Wipe must clear internal key state."
            ),
            "source": "theory_of_operation.md + registers.md",
            "signals": ["secret_key", "secret_key_d", "wipe_secret", "cfg_block"],
            "keywords": ["wipe", "secret_key", "erase", "cfg_block", "idle"],
            "scope": "hmac",
        },
        {
            "claim": (
                "In SHA-2-only mode, message length passes through "
                "without HMAC adjustments."
            ),
            "source": "theory_of_operation.md:L17-18",
            "signals": ["hmac_en_i", "sha_msg_len", "message_length_i"],
            "keywords": ["hmac_en", "sha_msg_len", "bypass", "forward"],
            "scope": "hmac_core",
        },
        {
            "claim": (
                "HMAC supports SHA-2 256, 384, and 512. "
                "Digest size is configured via CFG register."
            ),
            "source": "theory_of_operation.md:L101",
            "signals": ["digest_size", "digest_size_i", "digest_size_supplied"],
            "keywords": ["SHA2_256", "SHA2_384", "SHA2_512", "digest_size", "CFG"],
            "scope": "",
        },
    ]


# ---------------------------------------------------------------------------
# Step 2: Claim verification
# ---------------------------------------------------------------------------


def run_layer2(
    claims: list[dict[str, Any]],
    graph: SignalGraph,
    client: OpenAICompatibleClient,
    prompt_path: str | Path = DEFAULT_VERIFY_PROMPT,
    max_tokens: int = 2500,
    search_limit: int = 10,
) -> list[dict[str, Any]]:
    """Verify a set of claims against RTL specs.

    1. Full-text search for each claim.
    2. LLM decides whether the retrieved specs satisfy the claim.
    """
    prompt_template = Path(prompt_path).read_text(encoding="utf-8")
    results: list[dict[str, Any]] = []
    violations = 0
    partials = 0

    for idx, c in enumerate(claims):
        claim_text = c["claim"][:120].replace("\n", " ")
        print(
            f"  Layer2 [{idx + 1}/{len(claims)}] \"{claim_text}...\" ... ",
            end="", flush=True,
        )

        hits = graph.search(
            signals=c.get("signals"),
            keywords=c.get("keywords"),
            scope=c.get("scope"),
            limit=search_limit,
        )

        if not hits:
            print("NO HITS")
            results.append({
                "claim": c["claim"],
                "claim_source": c.get("source", ""),
                "verdict": "NOT_FOUND",
                "reasoning": "No matching RTL specs.",
                "evidence_specs": [],
                "gap_description": "",
            })
            continue

        verdict = _call_with_retry(
            lambda: _verify_claim(c, hits, client, prompt_template, max_tokens),
            attempts=3,
        )
        if verdict is None:
            results.append({
                "claim": c["claim"],
                "claim_source": c.get("source", ""),
                "verdict": "NOT_FOUND",
                "reasoning": "LLM call failed after retries",
                "evidence_specs": [],
                "gap_description": "",
            })
            continue

        v = verdict.get("verdict", "NOT_FOUND")
        if v == "VIOLATION":
            violations += 1
        elif v == "PARTIAL":
            partials += 1
        print(f"{v} ({len(hits)} hits)")
        results.append(verdict)

    print(
        f"  Layer2 done: {len(results)} claims checked "
        f"({violations} violations, {partials} partial)"
    )
    return results


def _verify_claim(
    claim: dict[str, Any],
    hits: list[dict[str, Any]],
    client: OpenAICompatibleClient,
    prompt_template: str,
    max_tokens: int,
) -> dict[str, Any]:
    trimmed = []
    for h in hits[:8]:
        trimmed.append({
            "chunk_id": h["chunk_id"],
            "summary": h["summary"],
            "behavior": h["behavior"],
            "guarantees": h["guarantees"],
            "assumptions": h["assumptions"],
            "uncertain_points": h["uncertain_points"],
            "snippets": h["snippets"],
            "source_file": h["source_file"],
            "line_start": h["line_start"],
            "line_end": h["line_end"],
        })

    payload = {
        "claim": claim["claim"],
        "claim_source": claim.get("source", ""),
        "retrieved_specs": trimmed,
    }

    content = client.chat(
        messages=[
            {"role": "system", "content": prompt_template},
            {"role": "user",
             "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ],
        max_tokens=max_tokens,
    )

    parsed = _parse_llm_response(content)
    parsed.setdefault("claim", claim["claim"])
    parsed.setdefault("claim_source", claim.get("source", ""))
    return parsed


def _parse_llm_response(content: str) -> dict[str, Any]:
    import re
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    # Try strict parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try regex extraction
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Last resort: truncate to last valid JSON token boundary
    # (handles LLM response cut off by max_tokens)
    # Try to find the last complete `},` or `}`, close the array/object
    for marker in ("},\n", "}", "\"]", "]\n"):
        idx = text.rfind(marker)
        if idx > 0:
            truncated = text[:idx + len(marker.rstrip())] + "]}"
            try:
                result = json.loads(truncated)
                result["_truncated"] = True
                return result
            except json.JSONDecodeError:
                # Try without extra "]"
                try:
                    result = json.loads(text[:idx + len(marker.rstrip())] + "}")
                    result["_truncated"] = True
                    return result
                except json.JSONDecodeError:
                    continue

    # Salvage path: response is a `{"claims": [ {...}, {...}, <cut off> ]}`
    # that was truncated mid-object. Recover every COMPLETE claim object by
    # scanning the array with brace-depth tracking and dropping the partial tail.
    salvaged = _salvage_truncated_claims(text)
    if salvaged is not None:
        return {"claims": salvaged, "_truncated": True}

    raise RuntimeError(f"Cannot parse LLM response: {text[:500]}")


def _salvage_truncated_claims(text: str) -> list[dict[str, Any]] | None:
    """Recover complete claim objects from a truncated ``{"claims": [...]}``.

    Scans from the first ``[`` after the ``claims`` key, tracking brace depth
    (ignoring braces inside strings), and collects each top-level ``{...}``
    object that closed cleanly. A trailing object cut off by ``max_tokens`` is
    silently dropped. Returns ``None`` if no array or no complete object found.
    """
    import re

    key = re.search(r'"claims"\s*:\s*\[', text)
    if not key:
        return None
    start = key.end()  # position just after the opening '['

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
                snippet = text[obj_start : i + 1]
                try:
                    objects.append(json.loads(snippet))
                except json.JSONDecodeError:
                    pass
                obj_start = -1
        elif ch == "]" and depth == 0:
            break  # end of the claims array

    return objects or None
