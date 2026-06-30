"""
Channel C: Coverage Gap Detection
===================================

Finds signals where the consumer's handling (case/if dispatch) doesn't
cover all legal values declared by the driver.

No heuristic pre-filter — every signal with both drivers and consumers
is sent to the LLM.  The LLM first judges whether the signal carries
enum/dispatch semantics (Step 0), then does deep analysis only when
relevant.  This avoids maintaining IP-specific keyword lists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rtl_bug_agent.llm.client import OpenAICompatibleClient
from rtl_bug_agent.phase2.signal_graph import SignalGraph

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = _PROJECT_ROOT / "config/prompts/phase2/channel_c_coverage_gap.md"


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


def run_channel_c(
    graph: SignalGraph,
    client: OpenAICompatibleClient,
    prompt_path: str | Path = DEFAULT_PROMPT,
    max_tokens: int = 10000,
    workers: int = 4,
    checkpoint_path: str | None = None,
) -> list[dict[str, Any]]:
    """Run coverage-gap detection on every signal with drivers+consumers.
    *workers* parallel LLM calls.  If *checkpoint_path* is given,
    findings are append-incrementally as JSONL lines."""

    prompt_template = Path(prompt_path).read_text(encoding="utf-8")
    candidates = _gather_candidates(graph)

    if not candidates:
        print("Channel C: no driver+consumer signals found.")
        return []

    # Restore from checkpoint
    ckpt = _JsonlCheckpoint(checkpoint_path) if checkpoint_path else None
    all_findings: list[dict[str, Any]] = ckpt.load() if ckpt else []
    processed: set[str] = {f.get("_signal", "") for f in all_findings}
    remaining = [c for c in candidates if c["signal"] not in processed]

    if processed:
        print(f"  Channel C: {len(processed)} signals from checkpoint, "
              f"{len(remaining)} remaining")
    if not remaining:
        gaps = sum(1 for f in all_findings if f.get("verdict") == "GAP")
        print(f"  Channel C done: {len(all_findings)} findings ({gaps} gaps)")
        return all_findings

    def _process_one(cand):
        findings = _call_with_retry(
            lambda: _check_signal(
                cand["signal"], cand, graph, client, prompt_template, max_tokens
            ),
            attempts=3,
        )
        signal = cand["signal"]
        if findings is not None:
            for f in findings:
                f["_signal"] = signal
            if ckpt:
                ckpt.append_all(findings or [{"_signal": signal, "_empty": True}])
        return findings, signal

    gaps = 0
    total = len(remaining)
    done_count = 0

    if workers <= 1:
        for cand in remaining:
            signal = cand["signal"]
            print(f"  Channel C [signal] signal={signal} ... ", end="", flush=True)
            findings, _ = _process_one(cand)
            if findings is not None:
                all_findings.extend(findings)
                gaps += sum(1 for f in findings if f.get("verdict") == "GAP")
                print(f"{len(findings)} findings")
            done_count += 1
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_process_one, c): c for c in remaining}
            for future in as_completed(futures):
                cand = futures[future]
                try:
                    findings, sig = future.result()
                except Exception:
                    findings = None
                    sig = cand["signal"]
                if findings is not None:
                    all_findings.extend(findings)
                    gaps += sum(1 for f in findings if f.get("verdict") == "GAP")
                done_count += 1
                print(
                    f"  Channel C [{done_count}/{total}] signal={sig} "
                    f"({len(findings) if findings else 0} findings) ... done"
                )

    all_findings = [f for f in all_findings if not f.get("_empty")]
    print(
        f"  Channel C done: {len(all_findings)} total findings "
        f"({gaps} gaps)"
    )
    return all_findings


# ------------------------------------------------------------------
# JSONL checkpoint helper
# ------------------------------------------------------------------


class _JsonlCheckpoint:
    """Thread-safe-ish JSONL checkpoint.

    Atomic append for single-line writes under PIPE_BUF (4KB)."""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> list[dict[str, Any]]:
        p = Path(self.path)
        if not p.exists():
            return []
        findings: list[dict[str, Any]] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    findings.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return findings

    def append_all(self, findings: list[dict[str, Any]]) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            for fd in findings:
                f.write(json.dumps(fd, ensure_ascii=False) + "\n")


def _gather_candidates(graph: SignalGraph) -> list[dict[str, Any]]:
    """Return every signal that has ≥1 driver and ≥1 consumer.

    No heuristic filtering — the LLM handles triage.
    """
    candidates: list[dict[str, Any]] = []

    for signal, info in graph.signals.items():
        if not info.drivers or not info.consumers:
            continue

        # Collect driver contexts
        driver_behaviors: list[str] = []
        for driver_id in info.drivers:
            spec = graph.specs.get(driver_id)
            if spec:
                driver_behaviors.append(spec.get("behavior", "")[:600])

        # Collect consumer contexts
        consumer_contexts: list[dict[str, Any]] = []
        for consumer_id in info.consumers:
            spec = graph.specs.get(consumer_id)
            if spec:
                consumer_contexts.append({
                    "spec_id": consumer_id,
                    "summary": spec.get("summary", "")[:150],
                    "behavior": spec.get("behavior", "")[:800],
                    "uncertain_points": spec.get("uncertain_points", []),
                })

        if driver_behaviors and consumer_contexts:
            candidates.append({
                "signal": signal,
                "driver_behaviors": driver_behaviors,
                "consumer_contexts": consumer_contexts,
            })

    return candidates


def _check_signal(
    signal: str,
    cand: dict[str, Any],
    graph: SignalGraph,
    client: OpenAICompatibleClient,
    prompt_template: str,
    max_tokens: int,
) -> list[dict[str, Any]]:
    """Ask the LLM to triage and (if relevant) analyse coverage for one signal."""

    payload = {
        "signal": signal,
        "driver_context": (
            "以下 spec 描述了信号的可能取值和行为：\n\n"
            + "\n\n".join(cand["driver_behaviors"])
        ),
        "consumers": cand["consumer_contexts"],
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
    return parsed.get("findings", [])


def _parse_llm_response(content: str) -> dict[str, Any]:
    import re

    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))
